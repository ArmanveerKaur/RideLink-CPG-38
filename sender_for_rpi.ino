#include <Arduino.h>
#include <WiFi.h>
#include "esp_wifi.h"

#include <SPI.h>
#include <MFRC522v2.h>
#include <MFRC522DriverSPI.h>
#include <MFRC522DriverPinSimple.h>
#include <MFRC522Debug.h>

#include <ArduinoJson.h>

MFRC522DriverPinSimple ss_pin(5);
MFRC522DriverSPI driver{ss_pin};
MFRC522 mfrc522{driver};

const char* ap_ssid = "BusHotspot";
const char* ap_password = "bus12345"; 
const unsigned long POLL_INTERVAL = 2000;

unsigned long lastPoll = 0;
#include <vector>
#include <string>
std::vector<String> prevStations;

String macToString(const uint8_t *mac) {
  char buf[18];
  sprintf(buf, "%02X:%02X:%02X:%02X:%02X:%02X",
          mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  return String(buf);
}

void sendJson(const JsonDocument &doc) {
  String out;
  serializeJson(doc, out);
  Serial.println(out);
}

void setupWiFiAP() {
  WiFi.mode(WIFI_AP);
  WiFi.softAP(ap_ssid, ap_password);
  delay(100);
  Serial.print("AP started. IP: ");
  Serial.println(WiFi.softAPIP());
}

void setup() {
  Serial.begin(115200);
  while (!Serial) { delay(10); }

  SPI.begin();   
  mfrc522.PCD_Init();
  MFRC522Debug::PCD_DumpVersionToSerial(mfrc522, Serial);
  Serial.println(F("Scan PICC to see UID"));

  // Start WiFi AP
  setupWiFiAP();
  lastPoll = millis();
}

std::vector<String> getConnectedStationMACs() {
  std::vector<String> list;
  wifi_sta_list_t sta_list;
  esp_err_t err = esp_wifi_ap_get_sta_list(&sta_list);
  if (err != ESP_OK) {
    return list;
  }
  for (int i = 0; i < sta_list.num; ++i) {
    list.push_back(macToString(sta_list.sta[i].mac));
  }
  return list;
}

void handleStationChanges() {
  std::vector<String> current = getConnectedStationMACs();
  for (auto &mac : current) {
    bool found = false;
    for (auto &p : prevStations) { if (p == mac) { found = true; break; } }
    if (!found) {
      StaticJsonDocument<256> doc;
      doc["type"] = "wifi_event";
      doc["event"] = "connected";
      doc["mac"] = mac;
      doc["ts"] = millis();
      sendJson(doc);
    }
  }
  for (auto &mac : prevStations) {
    bool found = false;
    for (auto &c : current) { if (c == mac) { found = true; break; } }
    if (!found) {
      StaticJsonDocument<256> doc;
      doc["type"] = "wifi_event";
      doc["event"] = "disconnected";
      doc["mac"] = mac;
      doc["ts"] = millis();
      sendJson(doc);
    }
  }

  prevStations = current;
}

void loop() {
  unsigned long now = millis();

  if (now - lastPoll >= POLL_INTERVAL) {
    handleStationChanges();
    lastPoll = now;
  }

  if (mfrc522.PICC_IsNewCardPresent() && mfrc522.PICC_ReadCardSerial()) {
    String uidString = "";
    for (byte i = 0; i < mfrc522.uid.size; i++) {
      if (mfrc522.uid.uidByte[i] < 0x10) uidString += "0";
      char buf[3];
      sprintf(buf, "%02X", mfrc522.uid.uidByte[i]);
      uidString += String(buf);
    }
    StaticJsonDocument<256> doc;
    doc["type"] = "rfid";
    doc["uid"] = uidString;
    doc["ts"] = millis();

    sendJson(doc);
    Serial.print("RFID read: ");
    Serial.println(uidString);

    mfrc522.PICC_HaltA();
    delay(200);
  }
  delay(10);
}
