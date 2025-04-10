#include <WiFi.h>
#include <EEPROM.h>
#include "esp_camera.h"
#include "esp_timer.h"
#include "img_converters.h"
#include "Arduino.h"
#include "fb_gfx.h"
#include "soc/soc.h" //disable brownout problems
#include "soc/rtc_cntl_reg.h"  //disable brownout problems

// WiFi network credentials
const char* ssid = "DESKTOP-J7H5805 8266";
const char* password = "s99(630L";

// Network configuration
#define SERVER_IP "192.168.137.1" // Server IP
const int serverPort = 80;

// Device identification
String deviceId = "MAKZFSH79Y5V";  // Using the existing ID to maintain continuity

// AI Thinker ESP32-CAM pins
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
  
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

// Camera and network management
unsigned long lastCaptureTime = 0;
unsigned long lastConnectionAttempt = 0;
int consecutiveFailures = 0;
#define MAX_FAILURES 3
#define FRAME_INTERVAL 1000  // 1 second between frames
#define RESET_INTERVAL 10000  // Full reset every 10 seconds if problems persist

bool setupCamera() {
    WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0); // Disable brownout detector
    
    // Set GPIO pins for camera
    pinMode(PWDN_GPIO_NUM, OUTPUT);
    
    // Hardware reset sequence for camera
    digitalWrite(PWDN_GPIO_NUM, HIGH);  // Power down
    delay(500);
    digitalWrite(PWDN_GPIO_NUM, LOW);   // Power up
    delay(500);
    
    // Lower resolution and frame rate to reduce resource contention
    camera_config_t config;
    config.ledc_channel = LEDC_CHANNEL_0;
    config.ledc_timer = LEDC_TIMER_0;
    config.pin_d0 = Y2_GPIO_NUM;
    config.pin_d1 = Y3_GPIO_NUM;
    config.pin_d2 = Y4_GPIO_NUM;
    config.pin_d3 = Y5_GPIO_NUM;
    config.pin_d4 = Y6_GPIO_NUM;
    config.pin_d5 = Y7_GPIO_NUM;
    config.pin_d6 = Y8_GPIO_NUM;
    config.pin_d7 = Y9_GPIO_NUM;
    config.pin_xclk = XCLK_GPIO_NUM;
    config.pin_pclk = PCLK_GPIO_NUM;
    config.pin_vsync = VSYNC_GPIO_NUM;
    config.pin_href = HREF_GPIO_NUM;
    config.pin_sccb_sda = SIOD_GPIO_NUM;
    config.pin_sccb_scl = SIOC_GPIO_NUM;
    config.pin_pwdn = PWDN_GPIO_NUM;
    config.pin_reset = RESET_GPIO_NUM;
    
    config.xclk_freq_hz = 16000000;  // 16MHz
    config.pixel_format = PIXFORMAT_JPEG;
    
    // Use lowest reasonable resolution
    config.frame_size = FRAMESIZE_UXGA;  // 1600x1200
    config.jpeg_quality = 8;            // 0-63 lower number means higher quality
    config.fb_count = 1;                 // Minimum frame buffers
    
    // Initialize camera
    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK) {
        Serial.printf("Camera initialization failed with error 0x%x\n", err);
        return false;
    }
    
    // Further reduce camera settings to minimum
    sensor_t * s = esp_camera_sensor_get();
    if (s) {
        s->set_framesize(s, FRAMESIZE_HD);  // Make sure it's at lowest usable resolution
    }
    
    Serial.println("Camera initialized with minimum settings");
    consecutiveFailures = 0;
    return true;
}

void setupWiFi() {
    Serial.println("\nConnecting to WiFi...");
    WiFi.begin(ssid, password);
    
    // Wait for connection with a timeout
    int connectionAttempts = 0;
    while (WiFi.status() != WL_CONNECTED && connectionAttempts < 20) {
        delay(500);
        Serial.print(".");
        connectionAttempts++;
    }
    
    if (WiFi.status() == WL_CONNECTED) {
        Serial.println("\nWiFi connected!");
        Serial.print("IP Address: ");
        Serial.println(WiFi.localIP());
    } else {
        Serial.println("\nFailed to connect to WiFi. Will retry in setup loop.");
    }
}

void registerDevice() {
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("WiFi not connected. Cannot register device.");
        return;
    }
    
    WiFiClient regClient;
    
    if (regClient.connect(SERVER_IP, serverPort)) {
        String data = "{\"device_id\":\"" + deviceId + "\",\"type\":\"ESP32-CAM\"}";

        regClient.println("POST /register_device HTTP/1.1");
        regClient.println("Host: 192.168.1.100");
        regClient.println("Content-Type: application/json");
        regClient.print("Content-Length: ");
        regClient.println(data.length());
        regClient.println("Connection: close");
        regClient.println();
        regClient.println(data);

        // Wait briefly for response but don't hang
        delay(500);
        
        regClient.stop();
        Serial.println("Device registration attempt complete");
    } else {
        Serial.println("Failed to connect for device registration");
    }
}

bool captureAndSendFrame() {
    // If it's too soon since the last attempt, return
    unsigned long currentTime = millis();
    if (currentTime - lastCaptureTime < FRAME_INTERVAL) {
        return true;
    }
    
    lastCaptureTime = currentTime;
    
    // Only use the camera for capture
    Serial.println("Capturing frame...");
    camera_fb_t *fb = esp_camera_fb_get();
    
    if (!fb) {
        Serial.println("Camera capture failed");
        consecutiveFailures++;
        
        if (consecutiveFailures >= MAX_FAILURES) {
            Serial.println("Too many failures, resetting...");
            return false; // Signal for reset
        }
        
        return true;
    }

    // We got a frame, reset failure counter
    consecutiveFailures = 0;
    
    // Check if frame is valid
    if (fb->len == 0 || fb->buf == NULL) {
        Serial.println("Invalid frame buffer");
        esp_camera_fb_return(fb);
        return true;
    }
    
    Serial.printf("Captured image: %dx%d %d bytes\n", fb->width, fb->height, fb->len);
    
    // Check WiFi connection before attempting to send
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("WiFi disconnected. Reconnecting...");
        setupWiFi();
        esp_camera_fb_return(fb);
        return true; // Skip sending this time
    }
    
    // Attempt to send the frame only if we're not too close to the last attempt
    if (currentTime - lastConnectionAttempt > 1000) {
        lastConnectionAttempt = currentTime;
        
        // Use a WiFi client for the image upload
        WiFiClient imgClient;
        
        // Set a timeout for the entire connection attempt
        unsigned long connectionTimeout = millis() + 10000; // 10 second timeout
        
        bool connected = false;
        while (!connected && millis() < connectionTimeout) {
            connected = imgClient.connect(SERVER_IP, serverPort);
            if (!connected) {
                delay(100); // Short delay before retry
            }
        }
        
        if (connected) {
            Serial.println("Connected to server, sending image...");
            
            // Prepare HTTP headers for raw JPEG upload
            String header = "POST /upload/" + deviceId + " HTTP/1.1\r\n";
            header += "Host: " + String(SERVER_IP) + "\r\n";
            header += "Content-Type: image/jpeg\r\n";
            header += "Content-Length: " + String(fb->len) + "\r\n";
            header += "Connection: close\r\n\r\n";
            
            // Send headers
            imgClient.print(header);
            
            // Send image data in small chunks with timeouts
            const int chunkSize = 512;
            size_t bytesSent = 0;
            unsigned long lastProgressTime = millis();
            
            while (bytesSent < fb->len) {
                // Break if we've been trying too long
                if (millis() - lastProgressTime > 15000) { // 15 second timeout for progress
                    Serial.println("Sending timeout - incomplete transfer");
                    break;
                }
                
                // Calculate bytes to send in this chunk
                size_t bytesToSend = min(chunkSize, (int)(fb->len - bytesSent));
                
                // Send the chunk
                size_t sent = imgClient.write(fb->buf + bytesSent, bytesToSend);
                
                if (sent > 0) {
                    bytesSent += sent;
                    lastProgressTime = millis(); // Reset progress timer
                    
                    // Print progress every 10KB
                    if (bytesSent % 10240 == 0 || bytesSent == fb->len) {
                        Serial.printf("Sent %d/%d bytes (%.1f%%)\n", 
                            bytesSent, fb->len, (bytesSent * 100.0) / fb->len);
                    }
                } else {
                    // Nothing sent, might indicate a problem
                    Serial.println("Failed to send chunk, retrying...");
                    delay(100); // Brief pause before retry
                }
                
                // Small delay between chunks to allow TCP processing
                // and prevent ESP32 watchdog resets
                delay(5);
                
                // Periodically yield to the main loop
                if (bytesSent % (chunkSize * 8) == 0) {
                    yield();
                }
            }
            
            // Check if all bytes were sent
            if (bytesSent == fb->len) {
                Serial.printf("Successfully sent %d bytes\n", bytesSent);
                
                // Wait for server response with timeout
                unsigned long responseTimeout = millis() + 3000;
                bool receivedResponse = false;
                
                while (millis() < responseTimeout && imgClient.connected()) {
                    if (imgClient.available()) {
                        // Read and parse the HTTP response status line
                        String responseLine = imgClient.readStringUntil('\n');
                        Serial.println("Server response: " + responseLine);
                        
                        receivedResponse = true;
                        break;
                    }
                    delay(10);
                }
                
                if (!receivedResponse) {
                    Serial.println("No response from server");
                }
            } else {
                Serial.printf("Incomplete send: %d of %d bytes\n", bytesSent, fb->len);
                consecutiveFailures++;
            }
            
            // Close the connection
            imgClient.stop();
            Serial.println("Image upload complete");
        } else {
            Serial.println("Failed to connect to server for image upload");
            consecutiveFailures++;
        }
    }
    
    // Always release the frame buffer when done
    esp_camera_fb_return(fb);
    
    // Return true to indicate we don't need a full reset yet
    return (consecutiveFailures < MAX_FAILURES);
}

void setup() {
    Serial.begin(115200);
    delay(3000); // Give serial time to initialize
    
    Serial.println("\n\n--------------------");
    Serial.println("ESP32-CAM WiFi Camera");
    Serial.println("--------------------");
    
    // Disable brownout detector
    WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);
    
    // First setup WiFi, then camera
    setupWiFi();
    
    // Setup camera
    if (!setupCamera()) {
        Serial.println("Initial camera setup failed, restarting...");
        ESP.restart();
    }
    
    // Register device with the server
    registerDevice();
    
    lastCaptureTime = 0; // Allow immediate first capture
    lastConnectionAttempt = 0;
}

void loop() {
    // Check WiFi connection
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("WiFi disconnected. Reconnecting...");
        setupWiFi();
        delay(1000);
        return;
    }
    
    // Attempt to capture and send frame
    if (!captureAndSendFrame()) {
        // If failed too many times, do a full reset
        Serial.println("Performing full reset sequence");
        
        // Reset camera subsystem
        esp_camera_deinit();
        delay(1000);
        
        // Reinitialize camera
        if (!setupCamera()) {
            Serial.println("Camera reset failed, restarting ESP32...");
            delay(1000);
            ESP.restart();
        }
        
        // Reset failure counters
        consecutiveFailures = 0;
    }
    
    // Check if we need a periodic full reset
    if (millis() > RESET_INTERVAL && millis() % RESET_INTERVAL < 100) {
        Serial.println("Performing periodic camera reset");
        esp_camera_deinit();
        delay(500);
        setupCamera();
    }
    
    // Main loop delay - keep this relatively long to avoid overwhelming the system
    delay(200);
}