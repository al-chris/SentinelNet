#include <EthernetENC.h>
#include <SPI.h>
#include <EEPROM.h>
#include "esp32cam.h"

// Pin definitions for ENC28J60
#define SPI_MISO 12
#define SPI_MOSI 13
#define SPI_SCK 14
#define ETH_CS 15

// Network configuration
byte mac[] = { 0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0xED };
#define MYIPADDR 192, 168, 1, 28  // ESP32-CAM's IP
#define MYIPMASK 255, 255, 255, 0
#define MYDNS 192, 168, 1, 100  // Server IP
#define MYGW 192, 168, 1, 100   // Server IP

// Server settings
// IPAddress server(192, 168, 1, 100);
char server[] = "192.168.1.100";
const int serverPort = 80;

// Device identification
String deviceId;
const int EEPROM_SIZE = 512;
const int ID_ADDRESS = 0;

EthernetClient client;

String generateUniqueId() {
  uint64_t chipId = ESP.getEfuseMac();  // Get chip ID
  char id[13];
  sprintf(id, "CAM_%08X", (uint32_t)chipId);
  return String(id);
}

String generateRandomId() {
    const char charset[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";  // Characters for ID
    char randomId[13];  // 12 characters + null terminator

    // Seed the random number generator
    randomSeed(analogRead(0));  // Use analog pin for random seed

    for (int i = 0; i < 12; i++) {
        randomId[i] = charset[random(0, sizeof(charset) - 1)];  // Pick random char from charset
    }
    randomId[12] = '\0';  // Null-terminate the string

    return String(randomId);
}

void loadOrGenerateDeviceId() {
    EEPROM.begin(EEPROM_SIZE);
    
    // Read the existing device ID from EEPROM
    String savedId = "";
    for (int i = 0; i < 12; i++) {
        char c = EEPROM.read(ID_ADDRESS + i);
        if (c == 0) break;  // Stop if null-terminator is found
        savedId += c;
    }

    if (savedId.length() == 0 || savedId == "CAM_00000000") {
        // If no ID exists or it's the default one, generate a new random ID
        deviceId = generateRandomId();
        
        // Store the new ID in EEPROM
        for (unsigned int i = 0; i < deviceId.length(); i++) {
            EEPROM.write(ID_ADDRESS + i, deviceId[i]);
        }
        EEPROM.write(ID_ADDRESS + deviceId.length(), 0);  // Null-terminate the string
        EEPROM.commit();
    } else {
        deviceId = savedId;
    }

    Serial.print("Loaded Device ID: ");
    Serial.println(deviceId);
}


void setupCamera() {
  pinMode(4, OUTPUT);
  // digitalWrite(4, HIGH); // I'm turning this off for now

  auto resolution = esp32cam::Resolution::find(1024, 768);
  esp32cam::Config cfg;
  cfg.setPins(esp32cam::pins::AiThinker);
  cfg.setResolution(resolution);
  cfg.setBufferCount(2);
  cfg.setJpeg(80);

  bool ok = esp32cam::Camera.begin(cfg);
  if (!ok) {
    Serial.println("Camera initialization failed");
    while (1) delay(100);
  }
  Serial.println("Camera initialized successfully");
}

void registerDevice() {
  if (client.connect(server, serverPort)) {
    String data = "{\"device_id\":\"" + deviceId + "\",\"type\":\"ESP32-CAM\"}";

    client.println("POST /register_device HTTP/1.1");
    client.println("Host: 192.168.1.100");
    client.println("Content-Type: application/json");
    client.print("Content-Length: ");
    client.println(data.length());
    client.println();
    client.println(data);

    // Wait for response
    while (client.connected()) {
      if (client.available()) {
        String line = client.readStringUntil('\n');
        if (line == "\r") break;
      }
    }
    client.stop();
  }
}

void setupEthernet() {
    Serial.println("\nInitializing Ethernet...");
    
    // Initialize SPI
    Serial.println("Starting SPI...");
    SPI.begin(SPI_SCK, SPI_MISO, SPI_MOSI, ETH_CS);
    
    Serial.println("Initializing ENC28J60...");
    Ethernet.init(ETH_CS);
    
    // Configure static IP
    IPAddress ip(MYIPADDR);
    IPAddress dns(MYDNS);
    IPAddress gw(MYGW);
    IPAddress subnet(MYIPMASK);
    
    Serial.println("Starting Ethernet connection...");
    Ethernet.begin(mac, ip, dns, gw, subnet);
    delay(5000);

    if (Ethernet.hardwareStatus() == EthernetNoHardware) {
        Serial.println("ERROR: Ethernet hardware not found!");
        while (1) delay(1000);
    } else if (Ethernet.hardwareStatus() == EthernetENC28J60) {
        Serial.println("ENC28J60 hardware found");
    }

    Serial.print("IP Address: ");
    Serial.println(Ethernet.localIP());
    
    // Test network connectivity
    Serial.print("Testing connection to server ");
    Serial.println(server);
    
    if (client.connect(server, serverPort)) {
        Serial.println("Successfully connected to server!");
        client.stop();
    } else {
        Serial.println("Failed to connect to server. Error details:");
        Serial.print("Ethernet Link Status: ");
        Serial.println(Ethernet.linkStatus() == LinkON ? "ON" : "OFF");
    }
}

void sendMJPEGStream() {
  if (!client.connected()) {
    Serial.print("Connecting to server ");
    Serial.print(server);
    Serial.print(":");
    Serial.println(serverPort);

    if (!client.connect(server, serverPort)) {
      Serial.println("Connection failed. Debug info:");
      Serial.print("Link Status: ");
      Serial.println(Ethernet.linkStatus() == LinkON ? "ON" : "OFF");
      Serial.print("Local IP: ");
      Serial.println(Ethernet.localIP());
      delay(1000);
      return;
    }

    Serial.println("Connected! Sending HTTP headers...");

    // Send HTTP headers with debug output - BOUNDARY DECLARATION IN HEADER
    String request = "POST /upload/" + deviceId + " HTTP/1.1\r\n";
    request += "Host: " + String(server) + "\r\n";
    request += "Content-Type: multipart/x-mixed-replace; boundary=frame\r\n";
    request += "Connection: keep-alive\r\n";
    request += "Cache-Control: no-cache\r\n";
    request += "\r\n"; // Empty line to indicate end of headers

    Serial.println("Sending request:");
    Serial.println(request);

    client.print(request);
  }

  auto frame = esp32cam::Camera.capture();
  if (frame && frame->size() > 0) {
    // Send the boundary with proper format
    client.print("--frame\r\n");
    client.print("Content-Type: image/jpeg\r\n");
    client.print("Content-Length: ");
    client.print(frame->size());
    client.print("\r\n\r\n");
    
    // Send the binary JPEG data
    client.write(frame->data(), frame->size());
    
    // End with a newline (important for multipart parsing)
    client.print("\r\n");
    
    Serial.printf("Frame sent: %d bytes\n", frame->size());
  } else {
    Serial.println("Failed to capture frame");
  }
}

void setup() {
  Serial.begin(115200);
  while (!Serial) delay(100);
  Serial.println("\nESP32-CAM Security System Node");

  delay(2000);

  // loadOrGenerateDeviceId();
  
  
  setupCamera();
  setupEthernet();

  // Initialize Ethernet
  // SPI.begin(SPI_SCK, SPI_MISO, SPI_MOSI, ETH_CS);
  // Ethernet.init(ETH_CS);

  // IPAddress ip(MYIPADDR);
  // IPAddress dns(MYDNS);
  // IPAddress gw(MYGW);
  // IPAddress subnet(MYIPMASK);
  // Ethernet.begin(mac, ip, dns, gw, subnet);

  delay(5000);

  deviceId = generateRandomId();
  Serial.print("Loaded Device ID: ");
  Serial.println(deviceId);

  registerDevice();
}

void loop() {
  if (Ethernet.linkStatus() == LinkOFF) {
    Serial.println("Ethernet cable disconnected. Waiting...");
    delay(2000);
    return;
  }

  sendMJPEGStream();
  delay(100);
}