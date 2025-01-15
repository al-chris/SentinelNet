#include <EthernetENC.h>
#include <SPI.h>
#include <EEPROM.h>
#include "esp32cam.h"

// Pin definitions for ENC28J60
#define SPI_MISO 12  
#define SPI_MOSI 13
#define SPI_SCK  14
#define ETH_CS   15

// Network configuration
byte mac[] = { 0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0xED };
#define MYIPADDR 192,168,1,28    // ESP32-CAM's IP
#define MYIPMASK 255,255,255,0
#define MYDNS 192,168,1,100      // Server IP
#define MYGW 192,168,1,100       // Server IP

// Server settings
IPAddress server(192, 168, 1, 100);
const int serverPort = 80;

// Device identification
String deviceId;
const int EEPROM_SIZE = 512;
const int ID_ADDRESS = 0;

EthernetClient client;

String generateUniqueId() {
    uint64_t chipId = ESP.getEfuseMac(); // Get chip ID
    char id[13];
    sprintf(id, "CAM_%08X", (uint32_t)chipId);
    return String(id);
}

void loadOrGenerateDeviceId() {
    EEPROM.begin(EEPROM_SIZE);
    
    // Try to read existing ID
    String savedId = "";
    for (int i = 0; i < 12; i++) {
        char c = EEPROM.read(ID_ADDRESS + i);
        if (c == 0) break;
        savedId += c;
    }
    
    if (savedId.length() == 0 || savedId == "CAM_00000000") {
        // Generate and save new ID if none exists
        deviceId = generateUniqueId();
        for (unsigned int i = 0; i < deviceId.length(); i++) {
            EEPROM.write(ID_ADDRESS + i, deviceId[i]);
        }
        EEPROM.write(ID_ADDRESS + deviceId.length(), 0);
        EEPROM.commit();
    } else {
        deviceId = savedId;
    }
    
    Serial.print("Device ID: ");
    Serial.println(deviceId);
}

void setupCamera() {
    pinMode(4, OUTPUT);
    digitalWrite(4, HIGH);
    
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

void sendMJPEGStream() {
    if (!client.connected()) {
        if (!client.connect(server, serverPort)) {
            Serial.println("Connection failed");
            delay(1000);
            return;
        }
        
        client.println("POST /upload/" + deviceId + " HTTP/1.1");
        client.println("Host: 192.168.1.100");
        client.println("Content-Type: multipart/x-mixed-replace; boundary=frame");
        client.println("Connection: keep-alive");
        client.println();
    }

    auto frame = esp32cam::Camera.capture();
    if (frame && frame->size() > 0) {
        client.println("--frame");
        client.println("Content-Type: image/jpeg");
        client.print("Content-Length: ");
        client.println(frame->size());
        client.println();
        client.write(frame->data(), frame->size());
        client.println();
    }
}

void setup() {
    Serial.begin(115200);
    while (!Serial) delay(100);
    Serial.println("\nESP32-CAM Security System Node");

    loadOrGenerateDeviceId();
    setupCamera();
    
    // Initialize Ethernet
    SPI.begin(SPI_SCK, SPI_MISO, SPI_MOSI, ETH_CS);
    Ethernet.init(ETH_CS);
    
    IPAddress ip(MYIPADDR);
    IPAddress dns(MYDNS);
    IPAddress gw(MYGW);
    IPAddress subnet(MYIPMASK);
    Ethernet.begin(mac, ip, dns, gw, subnet);
    
    delay(2000);
    registerDevice();
}

void loop() {
    if (Ethernet.linkStatus() == LinkOFF) {
        Serial.println("Ethernet cable disconnected");
        delay(1000);
        return;
    }

    sendMJPEGStream();
    delay(100);
}