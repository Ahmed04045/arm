/*
  =========================================================================
  ESP32 Sensor Network Gateway
  =========================================================================

  REQUIRED LIBRARIES:
    - "RF24" by TMRh20
    - "PubSubClient" by Nick O'Leary
    - "ArduinoJson" by Benoit Blanchon (v6+)
    - "SD" and "FS" - bundled with the ESP32 Arduino core, no separate
      install needed

  For wiring, refer to the schematic pdf

*/

#include <Arduino.h>
#include <SPI.h>
#include <RF24.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <FS.h>
#include <SD.h>

// ---------------------------------------------------------------------
// WIFI CONFIG
// ---------------------------------------------------------------------
const char *WIFI_SSID = "YOUR_WIFI_SSID";
const char *WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

const char *MQTT_BROKER = "192.168.1.100"; // broker IP or hostname
const uint16_t MQTT_PORT = 1883;
const char *MQTT_CLIENT_ID = "sensor-net-gateway";
const char *MQTT_USER = ""; // leave empty if no auth
const char *MQTT_PASSWORD = "";

// topic layout:
//   sensornet/<nodeId>/reading/<sensorName>: {"type","value","timestamp"}  (gateway publishes)
//   sensornet/<nodeId>/status: {"status":"green|yellow|red"} (gateway publishes)
//   sensornet/config/<sensorName>: {"normalMin","normalMax","hardMin","hardMax"}

const char *TOPIC_PREFIX = "sensornet";
const char *CONFIG_TOPIC_FILTER = "sensornet/config/+";
const char *CONFIG_TOPIC_PREFIX = "sensornet/config/";

// ---------------------------------------------------------------------
// RADIO - must match the Nano firmware exactly
// ---------------------------------------------------------------------
#define NRF_CE_PIN   4
#define NRF_CSN_PIN  16

const uint64_t UPLINK_ADDR   = 0xF0F0F0F0E1LL;  // Nano(s) -> ESP32
const uint64_t DOWNLINK_ADDR = 0xF0F0F0F0D2LL;  // ESP32 -> Nano(s)

// ---------------------------------------------------------------------
// SD CARD
// ---------------------------------------------------------------------
#define SD_CS_PIN 5
const char *LOG_FILE_PATH = "/sensornet_log.csv";
bool sdAvailable = false;

enum SensorType : uint8_t {
  SENSOR_TEMPERATURE = 0,
  SENSOR_HUMIDITY = 1,
  SENSOR_WATER_LEVEL = 2,
  SENSOR_AIR_QUALITY = 3,
  SENSOR_SOIL_MOISTURE = 4,
  SENSOR_LIGHT_INTENSITY = 5,
  SENSOR_COUNT = 6
};

enum LedCommand : uint8_t {
  CMD_GREEN_OK    = 0,
  CMD_YELLOW_WARN = 1,
  CMD_RED_ERROR   = 2
};

struct __attribute__((packed)) SensorPacket {
  uint8_t  nodeId;
  uint8_t  sensorType;
  float    value;
  uint32_t timestamp;
};

struct __attribute__((packed)) CommandPacket {
  uint8_t nodeId;
  uint8_t command;
};

// ---------------------------------------------------------------------
// RUNTIME-CONFIGURABLE RANGES
// These start as local placeholders. The computer/dashboard system is
// the intended source of truth for them: publish a retained message to
// sensornet/config/<sensorName> with any of normalMin/normalMax/
// hardMin/hardMax (any subset - only the fields present are updated) and
// the gateway adopts the new thresholds immediately, no reflash needed.
// "normal" outside range   -> yellow warning
// "hard"   outside range   -> treated as a faulty/implausible reading -> red
// ---------------------------------------------------------------------
struct Range { float normalMin, normalMax, hardMin, hardMax; };

Range ranges[SENSOR_COUNT] = {
  /* SENSOR_TEMPERATURE     */ { 10.0,  35.0,   -20.0,   60.0   }, // deg C
  /* SENSOR_HUMIDITY        */ { 20.0,  80.0,     0.0,  100.0   }, // %
  /* SENSOR_WATER_LEVEL     */ { 100,   900,       0,   1023    }, // raw ADC
  /* SENSOR_AIR_QUALITY     */ { 100,   700,       0,   1023    }, // raw ADC (MQ-135)
  /* SENSOR_SOIL_MOISTURE   */ { 200,   800,       0,   1023    }, // raw ADC
  /* SENSOR_LIGHT_INTENSITY */ { 5.0, 10000.0,     0.0, 65535.0 }  // lux
};

// ---------------------------------------------------------------------
// PER-NODE STATE TRACKING
// ---------------------------------------------------------------------
const uint8_t MAX_NODES = 32;
const unsigned long BATCH_WINDOW_MS = 500; // time to wait for the rest of a node's burst before judging

struct NodeState {
  bool     inUse = false;
  uint8_t  nodeId = 0;
  float    values[SENSOR_COUNT];
  bool     haveValue[SENSOR_COUNT] = {false};
  unsigned long batchStartTime = 0;
  bool     batchOpen = false;
};

NodeState nodes[MAX_NODES];

RF24 radio(NRF_CE_PIN, NRF_CSN_PIN);
WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);

const char *sensorName(uint8_t type) {
  switch (type) {
    case SENSOR_TEMPERATURE:     return "temperature";
    case SENSOR_HUMIDITY:        return "humidity";
    case SENSOR_WATER_LEVEL:     return "water_level";
    case SENSOR_AIR_QUALITY:     return "air_quality";
    case SENSOR_SOIL_MOISTURE:   return "soil_moisture";
    case SENSOR_LIGHT_INTENSITY: return "light_intensity";
    default:                     return "unknown";
  }
}

int sensorTypeFromName(const String &name) {
  for (uint8_t t = 0; t < SENSOR_COUNT; t++) {
    if (name.equals(sensorName(t))) return t;
  }
  return -1;
}

void connectWiFi();
void reconnectMQTT();
void mqttCallback(char *topic, byte *payload, unsigned int length);
void handleReading(const SensorPacket &packet);
int findOrCreateNode(uint8_t nodeId);
void publishReading(const SensorPacket &packet);
void evaluateAndRespond(NodeState &node);
void checkRange(const NodeState &node, uint8_t type, bool &hardFail, bool &softFail);
void sendCommand(uint8_t nodeId, LedCommand command);
void publishStatus(uint8_t nodeId, const char *statusText);
void initSD();
void logReadingToSD(const SensorPacket &packet);
void logStatusToSD(uint8_t nodeId, const char *statusText);

void setup() {
  Serial.begin(115200);
  connectWiFi();
  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setCallback(mqttCallback);

  if (!radio.begin()) {
    Serial.println(F("NRF24L01 not detected - check wiring"));
  }
  radio.setPALevel(RF24_PA_LOW);
  radio.setDataRate(RF24_250KBPS);
  radio.enableDynamicPayloads();
  radio.setRetries(5, 15);
  radio.openWritingPipe(DOWNLINK_ADDR);
  radio.openReadingPipe(1, UPLINK_ADDR);
  radio.startListening();

  initSD();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWiFi();
  if (!mqtt.connected()) reconnectMQTT();
  mqtt.loop();

  // Drain any waiting packets
  while (radio.available()) {
    SensorPacket packet;
    radio.read(&packet, sizeof(packet));
    handleReading(packet);
  }

  // Close out any node whose batch window has elapsed
  unsigned long now = millis();
  for (uint8_t i = 0; i < MAX_NODES; i++) {
    if (nodes[i].inUse && nodes[i].batchOpen &&
        now - nodes[i].batchStartTime >= BATCH_WINDOW_MS) {
      evaluateAndRespond(nodes[i]);
    }
  }
}

// ---------------------------------------------------------------------
// SD CARD LOGGING
// The SD card and the NRF24L01 share one SPI bus but use separate CS
// pins, so calls to each library never overlap - a radio read/write
// finishes and releases the bus (its CS goes high) before any SD.open/
// write/close call touches the bus with its own CS.
// ---------------------------------------------------------------------
void initSD() {
  if (!SD.begin(SD_CS_PIN)) {
    Serial.println(F("SD card not detected - logging to SD disabled"));
    sdAvailable = false;
    return;
  }
  sdAvailable = true;

  if (!SD.exists(LOG_FILE_PATH)) {
    File f = SD.open(LOG_FILE_PATH, FILE_WRITE);
    if (f) {
      f.println(F("nodeId,recordType,sensor,value,status,timestamp"));
      f.close();
    } else {
      Serial.println(F("Could not create SD log file"));
      sdAvailable = false;
    }
  }
  Serial.println(F("SD card ready for logging"));
}

void logReadingToSD(const SensorPacket &packet) {
  if (!sdAvailable) return;
  File f = SD.open(LOG_FILE_PATH, FILE_APPEND);
  if (!f) {
    Serial.println(F("SD write failed (reading), disabling SD logging"));
    sdAvailable = false;
    return;
  }
  // nodeId,recordType,sensor,value,status,timestamp
  f.print(packet.nodeId);      f.print(',');
  f.print("reading");          f.print(',');
  f.print(sensorName(packet.sensorType)); f.print(',');
  f.print(packet.value, 3);    f.print(',');
  f.print("");                 f.print(',');  // status column blank for readings
  f.println(packet.timestamp);
  f.close();
}

void logStatusToSD(uint8_t nodeId, const char *statusText) {
  if (!sdAvailable) return;
  File f = SD.open(LOG_FILE_PATH, FILE_APPEND);
  if (!f) {
    Serial.println(F("SD write failed (status), disabling SD logging"));
    sdAvailable = false;
    return;
  }
  // nodeId, recordType, sensor, value, status, timestamp
  f.print(nodeId);   f.print(',');
  f.print("status"); f.print(',');
  f.print("");        f.print(','); // sensor column blank for status rows
  f.print("");        f.print(','); // value column blank for status rows
  f.print(statusText); f.print(',');
  f.println(millis());
  f.close();
}

// Handles threshold updates pushed by the dashboard/computer system, e.g.
// topic "sensornet/config/air_quality" with payload
// {"normalMin":150,"normalMax":650,"hardMin":0,"hardMax":900}
// Any subset of the four fields may be included; only those present
// are changed.
void mqttCallback(char *topic, byte *payload, unsigned int length) {
  String topicStr(topic);
  if (!topicStr.startsWith(CONFIG_TOPIC_PREFIX)) return;

  String sensor = topicStr.substring(strlen(CONFIG_TOPIC_PREFIX));
  int type = sensorTypeFromName(sensor);
  if (type < 0) {
    Serial.print(F("Config update for unknown sensor: "));
    Serial.println(sensor);
    return;
  }

  StaticJsonDocument<160> doc;
  DeserializationError err = deserializeJson(doc, payload, length);
  if (err) {
    Serial.print(F("Bad config JSON: "));
    Serial.println(err.c_str());
    return;
  }

  Range &r = ranges[type];
  if (doc.containsKey("normalMin")) r.normalMin = doc["normalMin"];
  if (doc.containsKey("normalMax")) r.normalMax = doc["normalMax"];
  if (doc.containsKey("hardMin"))   r.hardMin   = doc["hardMin"];
  if (doc.containsKey("hardMax"))   r.hardMax   = doc["hardMax"];

  Serial.print(F("Updated range for "));
  Serial.print(sensor);
  Serial.print(F(": normal["));
  Serial.print(r.normalMin); Serial.print(F(", ")); Serial.print(r.normalMax);
  Serial.print(F("] hard["));
  Serial.print(r.hardMin); Serial.print(F(", ")); Serial.print(r.hardMax);
  Serial.println(F("]"));
}

void handleReading(const SensorPacket &packet) {
  int idx = findOrCreateNode(packet.nodeId);
  if (idx < 0) return; // node table full

  NodeState &node = nodes[idx];
  if (packet.sensorType < SENSOR_COUNT) {
    node.values[packet.sensorType] = packet.value;
    node.haveValue[packet.sensorType] = true;
  }
  if (!node.batchOpen) {
    node.batchOpen = true;
    node.batchStartTime = millis();
  }

  publishReading(packet);
  logReadingToSD(packet);
}

int findOrCreateNode(uint8_t nodeId) {
  for (uint8_t i = 0; i < MAX_NODES; i++) {
    if (nodes[i].inUse && nodes[i].nodeId == nodeId) return i;
  }
  for (uint8_t i = 0; i < MAX_NODES; i++) {
    if (!nodes[i].inUse) {
      nodes[i] = NodeState();
      nodes[i].inUse = true;
      nodes[i].nodeId = nodeId;
      return i;
    }
  }
  Serial.println(F("Node table full - increase MAX_NODES"));
  return -1;
}

void publishReading(const SensorPacket &packet) {
  StaticJsonDocument<128> doc;
  doc["type"] = sensorName(packet.sensorType);
  doc["value"] = packet.value;
  doc["timestamp"] = packet.timestamp;

  char payload[128];
  size_t len = serializeJson(doc, payload);

  char topic[64];
  snprintf(topic, sizeof(topic), "%s/%u/reading/%s",
           TOPIC_PREFIX, packet.nodeId, sensorName(packet.sensorType));

  mqtt.publish(topic, (uint8_t *)payload, len);
}

// Checks whatever readings arrived in this batch (including air quality),
// decides a status, tells the node which LED to show, publishes and logs
// the status, then resets for the next 10s cycle from that node.
void evaluateAndRespond(NodeState &node) {
  bool hardFail = false;
  bool softFail = false;

  for (uint8_t t = 0; t < SENSOR_COUNT; t++) {
    checkRange(node, t, hardFail, softFail);
  }

  LedCommand status;
  const char *statusText;
  if (hardFail) {
    status = CMD_RED_ERROR; // reading(s) far enough outside plausible
    statusText = "red"; // bounds to treat as a fault, not just a warning
  } else if (softFail) {
    status = CMD_YELLOW_WARN;
    statusText = "yellow";
  } else {
    status = CMD_GREEN_OK;
    statusText = "green";
  }

  sendCommand(node.nodeId, status);
  publishStatus(node.nodeId, statusText);
  logStatusToSD(node.nodeId, statusText);

  // Reset the batch (values are kept as "last known" until overwritten)
  node.batchOpen = false;
  for (uint8_t i = 0; i < SENSOR_COUNT; i++) node.haveValue[i] = false;
}

void checkRange(const NodeState &node, uint8_t type, bool &hardFail, bool &softFail) {
  if (!node.haveValue[type]) return; // nothing received this cycle - skip
  const Range &r = ranges[type];
  float v = node.values[type];
  if (v < r.hardMin || v > r.hardMax) {
    hardFail = true;
  } else if (v < r.normalMin || v > r.normalMax) {
    softFail = true;
  }
}

void sendCommand(uint8_t nodeId, LedCommand command) {
  CommandPacket cmd;
  cmd.nodeId = nodeId;
  cmd.command = command;

  radio.stopListening();
  radio.write(&cmd, sizeof(cmd));
  radio.startListening();
}

void publishStatus(uint8_t nodeId, const char *statusText) {
  StaticJsonDocument<64> doc;
  doc["status"] = statusText;

  char payload[64];
  size_t len = serializeJson(doc, payload);

  char topic[48];
  snprintf(topic, sizeof(topic), "%s/%u/status", TOPIC_PREFIX, nodeId);

  mqtt.publish(topic, (uint8_t *)payload, len);
}

void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.print(F("Connecting to WiFi"));
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print(F("WiFi connected, IP: "));
    Serial.println(WiFi.localIP());
  } else {
    Serial.println(F("WiFi connect timed out - will retry"));
  }
}

void reconnectMQTT() {
  if (WiFi.status() != WL_CONNECTED) return;
  Serial.print(F("Connecting to MQTT broker..."));
  bool ok;
  if (strlen(MQTT_USER) > 0) {
    ok = mqtt.connect(MQTT_CLIENT_ID, MQTT_USER, MQTT_PASSWORD);
  } else {
    ok = mqtt.connect(MQTT_CLIENT_ID);
  }
  if (ok) {
    Serial.println(F("connected"));
    mqtt.subscribe(CONFIG_TOPIC_FILTER);
  } else {
    Serial.print(F("failed, rc="));
    Serial.println(mqtt.state());
  }
}
