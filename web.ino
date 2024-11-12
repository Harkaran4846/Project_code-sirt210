#include <WiFiNINA.h>  // Library for handling WiFi connectivity on NINA-based boards
#include <Adafruit_Fingerprint.h>  // Library for interacting with the Adafruit fingerprint sensor
#include <PubSubClient.h>  // Library for MQTT communication

// WiFi credentials
const char* ssid = "Free";  // WiFi SSID
const char* password = "12345678";  // WiFi password

// MQTT server configuration
const char* mqtt_server = "broker.emqx.io";  // MQTT broker address
const int mqtt_port = 1883;  // MQTT port
const char* topic_request = "fingerprint/enroll/request";  // Topic to listen for enrollment requests
const char* topic_response = "fingerprint/data";  // Topic to send enrollment data (fingerprint ID)

// Define the serial port for the fingerprint sensor
#if (defined(__AVR__) || defined(ESP8266)) && !defined(__AVR_ATmega2560__)
#include <SoftwareSerial.h>  // Use SoftwareSerial for communication with the fingerprint sensor if necessary
SoftwareSerial mySerial(2, 3);  // RX, TX pins for SoftwareSerial communication
#else
#define mySerial Serial1  // Use Serial1 for hardware serial if available
#endif

Adafruit_Fingerprint finger = Adafruit_Fingerprint(&mySerial);  // Initialize fingerprint sensor with the serial interface
WiFiClient wifiClient;  // WiFi client for MQTT communication
PubSubClient client(wifiClient);  // MQTT client instance

bool startEnrollment = false;  // Flag to track whether to start the fingerprint enrollment
uint8_t id;  // Variable to store the fingerprint ID for enrollment

// Setup function to initialize the WiFi, MQTT, and fingerprint sensor
void setup() {
  Serial.begin(9600);  // Start serial communication for debugging
  while (!Serial);  // Wait for the serial connection

  WiFi.begin(ssid, password);  // Connect to WiFi using the credentials
  connectWiFi();  // Function to ensure WiFi connection

  client.setServer(mqtt_server, mqtt_port);  // Set the MQTT server and port
  client.setCallback(callback);  // Set the callback function to handle incoming MQTT messages

  finger.begin(57600);  // Initialize fingerprint sensor with a baud rate of 57600
  if (finger.verifyPassword()) {  // Verify the fingerprint sensor password to ensure it's connected
    Serial.println("Fingerprint sensor found!");  // If sensor is found, print message
  } else {
    Serial.println("Fingerprint sensor not found :(");  // If sensor is not found, halt the program
    while (1) delay(1);  // Infinite loop to prevent the program from continuing
  }
}

// Function to connect to Wi-Fi
void connectWiFi() {
  Serial.print("Connecting to Wi-Fi...");
  while (WiFi.status() != WL_CONNECTED) {  // Wait until the Wi-Fi connection is established
    delay(1000);  // Wait for 1 second before retrying
    Serial.print(".");  // Print dot to show progress
  }
  Serial.println("\nConnected to Wi-Fi.");  // Print a message once connected
}

// MQTT callback function to handle incoming messages
void callback(char* topic, byte* payload, unsigned int length) {
  String message = "";
  for (int i = 0; i < length; i++) {
    message += (char)payload[i];  // Convert the payload to a string
  }

  // Check if the topic is for the fingerprint enrollment request
  if (String(topic) == topic_request && message == "start_enrollment") {
    startEnrollment = true;  // Set the flag to true to start enrollment
  }
}

// Main loop function
void loop() {
  if (!client.connected()) {  // If not connected to the MQTT broker, reconnect
    connectMQTT();
  }
  client.loop();  // Handle MQTT messages

  // If an enrollment request was received, start the enrollment process
  if (startEnrollment) {
    startEnrollment = false;  // Reset the flag
    id = getNextAvailableId();  // Get the next available ID for the new fingerprint
    if (id == 0) return;  // If no ID is available, stop enrollment

    Serial.println("Starting enrollment process...");
    Serial.println("Enrolling fingerprint with ID #" + String(id));  // Print enrollment ID
    if (getFingerprintEnroll()) {  // Start the enrollment process and check if it's successful
      Serial.println("Fingerprint enrollment and storage complete.");
      sendFingerprintData(id);  // Send the enrolled fingerprint ID back to the MQTT broker
    }
  }
}

// Function to get the next available ID for fingerprint enrollment
uint8_t getNextAvailableId() {
  uint8_t id = 1;
  while (id <= 127) {  // Check IDs from 1 to 127 (assuming the fingerprint sensor can store 127 fingerprints)
    if (!finger.loadModel(id)) {  // If this ID is not already occupied, return it
      return id;
    }
    id++;  // Increment the ID to check the next one
  }
  Serial.println("Fingerprint sensor storage is full.");  // If no available ID, print an error
  return 0;  // Return 0 if no ID is available
}

// Function to connect to the MQTT broker
void connectMQTT() {
  while (!client.connected()) {  // Keep trying to connect to the broker until successful
    if (client.connect("FingerprintClient")) {  // Attempt to connect with a client ID
      client.subscribe(topic_request);  // Subscribe to the topic for enrollment requests
    } else {
      delay(5000);  // Wait 5 seconds before retrying
    }
  }
}

// Function to send fingerprint data (ID) back to the MQTT broker
void sendFingerprintData(uint8_t id) {
  String message = String(id);  // Convert the fingerprint ID to a string
  client.publish(topic_response, message.c_str());  // Publish the ID to the response topic
}

// Function to handle the fingerprint enrollment process
uint8_t getFingerprintEnroll() {
  int p = -1;
  Serial.print("Waiting for valid finger to enroll as #"); Serial.println(id);
  while (p != FINGERPRINT_OK) {  // Keep waiting until a valid finger is placed on the sensor
    p = finger.getImage();  // Capture the fingerprint image
    switch (p) {  // Handle different cases for the fingerprint capture process
      case FINGERPRINT_OK:  // If image capture is successful
        Serial.println("Image taken");
        break;
      case FINGERPRINT_NOFINGER:  // No finger detected
        Serial.print(".");
        break;
      case FINGERPRINT_PACKETRECIEVEERR:  // Communication error
        Serial.println("Communication error");
        break;
      case FINGERPRINT_IMAGEFAIL:  // Imaging error
        Serial.println("Imaging error");
        break;
      default:  // Unknown error
        Serial.println("Unknown error");
        break;
    }
  }

  p = finger.image2Tz(1);  // Convert the first image to a template (position 1)
  if (p != FINGERPRINT_OK) return p;  // Return error if conversion fails

  Serial.println("Remove finger");  // Prompt to remove the finger after the first scan
  delay(2000);  // Wait for 2 seconds
  p = 0;
  while (p != FINGERPRINT_NOFINGER) {  // Wait until the finger is removed
    p = finger.getImage();
  }

  Serial.println("Place same finger again");  // Prompt to place the same finger again for a second scan
  while (p != FINGERPRINT_OK) {  // Wait until the second scan is successful
    p = finger.getImage();
    switch (p) {  // Handle different cases
      case FINGERPRINT_OK:
        Serial.println("Image taken");
        break;
      case FINGERPRINT_NOFINGER:
        Serial.print(".");
        break;
      case FINGERPRINT_PACKETRECIEVEERR:
        Serial.println("Communication error");
        break;
      case FINGERPRINT_IMAGEFAIL:
        Serial.println("Imaging error");
        break;
      default:
        Serial.println("Unknown error");
        break;
    }
  }

  p = finger.image2Tz(2);  // Convert the second image to a template (position 2)
  if (p != FINGERPRINT_OK) return p;  // Return error if conversion fails

  p = finger.createModel();  // Create a model from the two templates
  if (p != FINGERPRINT_OK) return p;  // Return error if model creation fails

  p = finger.storeModel(id);  // Store the fingerprint model in memory at the given ID
  if (p == FINGERPRINT_OK) {
    Serial.println("Stored!");  // If successful, print confirmation
  } else {
    // Handle different error cases
    if (p == FINGERPRINT_PACKETRECIEVEERR) {
      Serial.println("Communication error");
    } else if (p == FINGERPRINT_BADLOCATION) {
      Serial.println("Could not store in that location");
    } else if (p == FINGERPRINT_FLASHERR) {
      Serial.println("Error writing to flash");
    } else {
      Serial.println("Unknown error");
    }
    return p;  // Return the error code
  }

  return true;  // Return success
}
