/*
  =========================================================================
  ARDUINO NANO SENSOR EDGE NODE
  =========================================================================
  req libs:
    - "DHT sensor library" by Adafruit
    - "Adafruit Unified Sensor" (dependency of the above)
    - "BH1750" by Christopher Laws (claws)
    - "RF24" by TMRh20

  refer to schematic in the repo for the wiring
*/

#include <Arduino.h>
#include <SPI.h>
#include <RF24.h>
#include <Wire.h>
#include <BH1750.h>
#include <DHT.h>

// ---------------------------------------------------------------------
// NETWORK CONFIG
// ---------------------------------------------------------------------
#define NODE_ID 1

const uint64_t UPLINK_ADDR    = 0xF0F0F0F0E1LL;  // nano to ESP32
const uint64_t DOWNLINK_ADDR  = 0xF0F0F0F0D2LL;  // ESP32 to nano

// ---------------------------------------------------------------------
// PIN DEFINITIONS
// ---------------------------------------------------------------------
#define DHTPIN 2
#define DHTTYPE DHT11

#define WATER_LEVEL_PIN A7
#define WATER_LEVEL_VCC_PIN 7

#define MQ135_PIN A1
#define SOIL_MOISTURE_PIN A0

#define LED_GREEN_PIN 3
#define LED_YELLOW_PIN 4
#define LED_RED_PIN 5

#define NRF_CE_PIN 9
#define NRF_CSN_PIN 10

// ---------------------------------------------------------------------
// TIMING
// ---------------------------------------------------------------------
const unsigned long SAMPLE_INTERVAL_MS     = 10000; // 10 seconds
const unsigned long WATER_SENSOR_WARMUP_MS = 50;    // settle time after power-up
const unsigned long RESPONSE_TIMEOUT_MS    = 1500;  // wait for ESP32 reply
const unsigned long YELLOW_BLINK_MS        = 300;

// ---------------------------------------------------------------------
// PROTOCOL
// ---------------------------------------------------------------------
enum SensorType : uint8_t {
  SENSOR_TEMPERATURE = 0,
  SENSOR_HUMIDITY = 1,
  SENSOR_WATER_LEVEL = 2,
  SENSOR_AIR_QUALITY = 3,
  SENSOR_SOIL_MOISTURE = 4,
  SENSOR_LIGHT_INTENSITY = 5
};

enum LedCommand : uint8_t {
  CMD_GREEN_OK = 0,
  CMD_YELLOW_WARN = 1,
  CMD_RED_ERROR = 2
};

struct __attribute__((packed)) SensorPacket {
  uint8_t  nodeId;
  uint8_t  sensorType;
  float    value;
  uint32_t timestamp;
};

//gateway
struct __attribute__((packed)) CommandPacket {
  uint8_t nodeId;   
  uint8_t command;
};

RF24 radio(NRF_CE_PIN, NRF_CSN_PIN);
DHT dht(DHTPIN, DHTTYPE);
BH1750 lightMeter;

unsigned long lastSampleTime   = 0;
unsigned long lastYellowToggle = 0;
bool yellowState = false;

LedCommand currentStatus = CMD_RED_ERROR; // pessimistic

void setup() {
  Serial.begin(9600);

  pinMode(WATER_LEVEL_VCC_PIN, OUTPUT);
  digitalWrite(WATER_LEVEL_VCC_PIN, LOW); // sensor stays OFF except while reading

  pinMode(LED_GREEN_PIN, OUTPUT);
  pinMode(LED_YELLOW_PIN, OUTPUT);
  pinMode(LED_RED_PIN, OUTPUT);
  digitalWrite(LED_GREEN_PIN, LOW);
  digitalWrite(LED_YELLOW_PIN, LOW);
  digitalWrite(LED_RED_PIN, LOW);

  dht.begin();

  Wire.begin();
  lightMeter.begin();

  if (!radio.begin()) {
    Serial.println(F("NRF24L01 not detected"));
  }
  radio.setPALevel(RF24_PA_LOW);
  radio.setDataRate(RF24_250KBPS);  
  radio.enableDynamicPayloads();
  radio.setRetries(5, 15);
  radio.openWritingPipe(UPLINK_ADDR);
  radio.openReadingPipe(1, DOWNLINK_ADDR);
  radio.startListening();

  applyLedState(CMD_RED_ERROR); // no exchange with the gateway has happened yet
}

void loop() {
  unsigned long now = millis();

  if (currentStatus == CMD_YELLOW_WARN && now - lastYellowToggle >= YELLOW_BLINK_MS) {
    yellowState = !yellowState;
    digitalWrite(LED_YELLOW_PIN, yellowState ? HIGH : LOW);
    lastYellowToggle = now;
  }

  if (now - lastSampleTime >= SAMPLE_INTERVAL_MS) {
    lastSampleTime = now;
    runSampleCycle();
  }
}

void runSampleCycle() {
  float temperature = dht.readTemperature();
  float humidity= dht.readHumidity();
  float waterLevel = readWaterLevel();
  float airQuality = analogRead(MQ135_PIN);
  float soilMoisture = analogRead(SOIL_MOISTURE_PIN);
  float lux = lightMeter.readLightLevel();

  bool dhtOk = !isnan(temperature) && !isnan(humidity);

  radio.stopListening();

  bool allSent = true;
  if (dhtOk) {
    allSent &= sendReading(SENSOR_TEMPERATURE, temperature);
    allSent &= sendReading(SENSOR_HUMIDITY, humidity);
  } else {
    allSent = false; 
  }
  allSent &= sendReading(SENSOR_WATER_LEVEL, waterLevel);
  allSent &= sendReading(SENSOR_AIR_QUALITY, airQuality);
  allSent &= sendReading(SENSOR_SOIL_MOISTURE, soilMoisture);
  allSent &= sendReading(SENSOR_LIGHT_INTENSITY, lux);

  radio.startListening();

  if (!allSent) {
    // when cant connect
    applyLedState(CMD_RED_ERROR);
    return;
  }

  LedCommand response;
  if (waitForCommand(response)) {
    applyLedState(response);
  } else {
    // reply too late
    applyLedState(CMD_RED_ERROR);
  }
}

float readWaterLevel() {
  digitalWrite(WATER_LEVEL_VCC_PIN, HIGH);
  delay(WATER_SENSOR_WARMUP_MS);
  int reading = analogRead(WATER_LEVEL_PIN);
  digitalWrite(WATER_LEVEL_VCC_PIN, LOW);
  return (float)reading;
}

bool sendReading(SensorType type, float value) {
  SensorPacket packet;
  packet.nodeId = NODE_ID;
  packet.sensorType = type;
  packet.value = value;
  packet.timestamp = millis();
  return radio.write(&packet, sizeof(packet));
}

bool waitForCommand(LedCommand &outCommand) {
  unsigned long start = millis();
  while (millis() - start < RESPONSE_TIMEOUT_MS) {
    if (radio.available()) {
      CommandPacket cmd;
      radio.read(&cmd, sizeof(cmd));
      if (cmd.nodeId == NODE_ID) {
        outCommand = (LedCommand)cmd.command;
        return true;
      }
      // not for us
    }
  }
  return false;
}

void applyLedState(LedCommand state) {
  currentStatus = state;

  digitalWrite(LED_GREEN_PIN, LOW);
  digitalWrite(LED_YELLOW_PIN, LOW);
  digitalWrite(LED_RED_PIN, LOW);
  yellowState = false;
  lastYellowToggle = millis();

  switch (state) {
    case CMD_GREEN_OK:
      digitalWrite(LED_GREEN_PIN, HIGH);

      break;
    case CMD_YELLOW_WARN:
      
      break;
    case CMD_RED_ERROR:
      digitalWrite(LED_RED_PIN, HIGH);

      break;
  }
}
