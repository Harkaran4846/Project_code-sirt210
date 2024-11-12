import face_recognition  # Library for face detection and recognition
import cv2  # OpenCV for image processing
import numpy as np  # NumPy for array manipulations
import os  # OS operations, such as path management
import time  # Time-related tasks
from google.cloud import storage, firestore  # Google Cloud Storage and Firestore SDK
from datetime import datetime  # Date and time handling
from picamera2 import Picamera2  # Camera library for Raspberry Pi
import firebase_admin  # Firebase Admin SDK
from firebase_admin import credentials  # For loading Firebase credentials
import paho.mqtt.client as mqtt  # MQTT library for communication
import google.auth  # For Google Cloud authentication
import tkinter as tk  # Tkinter for creating the GUI
from tkinter import messagebox  # Tkinter message box for pop-up messages

# Firebase initialization: Loading credentials and setting up Firestore and Storage
credentials, project = google.auth.load_credentials_from_file('/home/pi/firebase-credentials.json')
firebase_admin.initialize_app(credentials, {'storageBucket': 'task-8d-12a23.appspot.com'})
client = storage.Client(credentials=credentials, project=project)  # Google Cloud Storage client
bucket = client.bucket('task-8d-12a23.appspot.com')  # Firebase Storage bucket
db = firestore.Client(credentials=credentials, project=project)  # Firestore client

# MQTT setup: Define the broker and topics for communication
MQTT_BROKER = "broker.emqx.io"
MQTT_REQUEST_TOPIC = "attendance/request"  # Topic for sending attendance requests
MQTT_RESPONSE_TOPIC = "attendance/response"  # Topic for receiving attendance responses
CHECK_TOPIC = "attendance/check_connection"  # Topic for checking fingerprint sensor connection

mqtt_client = mqtt.Client()  # MQTT client instance for communication

# Camera setup: Initialize the camera and configure the preview settings
picam2 = Picamera2()
picam2.configure(picam2.create_preview_configuration(main={"format": 'XRGB8888', "size": (640, 360)}))

# Face recognition setup: Set up directories for storing images and encoding face data
download_dir = "/tmp/images/"
known_face_encodings = []  # List to store face encodings
known_face_names = []  # List to store student names corresponding to encodings

# GUI setup: Create the Tkinter window and add elements
root = tk.Tk()
root.title("Attendance System")  # Set window title

status_label = tk.Label(root, text="Welcome to the Attendance System", font=("Arial", 14))  # Label to display status
status_label.pack(pady=20)  # Add the label to the window with padding

# Function to download and encode student images from Firebase Storage
def download_and_encode_images():
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)  # Create directory if it does not exist
    blobs = bucket.list_blobs(prefix="students/")  # List all blobs (images) in the "students/" folder
    for blob in blobs:
        if blob.name.endswith(".jpg"):  # Check if the blob is a JPEG image
            student_folder = blob.name.split("/")[1]  # Extract student name from the folder
            image_path = os.path.join(download_dir, os.path.basename(blob.name))  # Path to store the image locally
            blob.download_to_filename(image_path)  # Download the image
            image = face_recognition.load_image_file(image_path)  # Load image for face recognition
            encodings = face_recognition.face_encodings(image)  # Get face encodings from the image
            if encodings:
                known_face_encodings.append(encodings[0])  # Append the first face encoding
                known_face_names.append(student_folder)  # Append the student's name

# Function to start face recognition and detect faces
def start_face_recognition():
    picam2.start()  # Start the camera
    print("Camera started")
    while True:
        frame = picam2.capture_array()  # Capture a frame from the camera
        rgb_frame = cv2.cvtColor(cv2.resize(frame, (0, 0), fx=0.5, fy=0.5), cv2.COLOR_BGR2RGB)  # Convert to RGB format
        face_locations = face_recognition.face_locations(rgb_frame, model='hog')  # Detect faces in the frame
        face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)  # Get face encodings
        
        if len(face_locations) > 0:  # If faces are detected, proceed
            for face_encoding in face_encodings:
                face_distances = face_recognition.face_distance(known_face_encodings, face_encoding)  # Compare with known faces
                best_match_index = np.argmin(face_distances)  # Get the index of the closest match
                if face_distances[best_match_index] < 0.6:  # If the match is strong enough
                    recognized_name = known_face_names[best_match_index]  # Get the student's name
                    student_name, roll_no = recognized_name.split("_")  # Extract name and roll number
                    mqtt_client.user_data_set({"recognized_face": (student_name, roll_no)})  # Store face recognition data
                    print(f"Face recognized: {student_name}. Please place your finger on the sensor.")
                    status_label.config(text=f"Face recognized: {student_name}. Please place your finger on the sensor.")
                    mqtt_client.publish(MQTT_REQUEST_TOPIC, "turn_on_fingerprint_sensor")  # Ask Arduino to activate fingerprint sensor
                    mqtt_client.publish(CHECK_TOPIC, "check_fingerprint_sensor")  # Check the fingerprint sensor connection
                    return  # Exit once the face is recognized
        
        time.sleep(0.1)  # Small delay to reduce CPU usage

# Function to mark attendance in Firestore
def mark_attendance(student_name, roll_no):
    student_ref = db.collection("attendance").document(student_name)  # Reference to the student's attendance document
    student_data = student_ref.get().to_dict() if student_ref.get().exists else {"attendance": [], "total_classes": 0}  # Fetch existing attendance data
    timestamp = datetime.now()  # Get current timestamp
    student_data["attendance"].append({
        "date": timestamp.strftime("%Y-%m-%d"),
        "time": timestamp.strftime("%H:%M:%S")
    })
    student_data["total_classes"] += 1  # Increment total classes attended
    days_attended = len(student_data["attendance"])  # Count the number of days attended
    attendance_percentage = (days_attended / student_data["total_classes"]) * 100  # Calculate attendance percentage
    student_data["attendance_percentage"] = attendance_percentage  # Update percentage
    student_ref.set(student_data)  # Save updated attendance data to Firestore
    messagebox.showinfo("Attendance", f"Attendance marked for {student_name} ({roll_no})")  # Display message
    status_label.config(text="Attendance marked successfully.")  # Update GUI status

# MQTT callback function to handle incoming messages
def on_message(client, userdata, message):
    payload = message.payload.decode()  # Decode the payload of the message
    if message.topic == MQTT_RESPONSE_TOPIC:
        if payload.isdigit():  # If a valid fingerprint ID is received
            student_name, roll_no = userdata["recognized_face"]
            mark_attendance(student_name, roll_no)  # Mark attendance for the recognized student
        elif payload == "Fingerprint sensor connected":
            print("Fingerprint sensor connected, ready for fingerprint verification.")  # Sensor is connected
        elif payload == "Fingerprint sensor not connected":
            print("Fingerprint sensor not connected. Please check the sensor.")  # Sensor not connected

# Initialize face encodings (download images from Firebase and encode them)
download_and_encode_images()

# MQTT setup: Set up the callback function and subscribe to topics
mqtt_client.on_message = on_message
mqtt_client.connect(MQTT_BROKER)  # Connect to the MQTT broker
mqtt_client.subscribe(MQTT_RESPONSE_TOPIC)  # Subscribe to the response topic
mqtt_client.subscribe(CHECK_TOPIC)  # Subscribe to the check connection topic
mqtt_client.loop_start()  # Start the MQTT loop to listen for messages

# Create a button in the GUI to start face recognition
start_button = tk.Button(root, text="Start Attendance", command=start_face_recognition, font=("Arial", 12))
start_button.pack(pady=10)  # Add the button to the window

root.mainloop()  # Run the Tkinter GUI event loop
