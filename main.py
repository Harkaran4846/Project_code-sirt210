from flask import Flask, render_template, request, redirect, url_for, session
import firebase_admin
from firebase_admin import credentials, firestore, storage
import paho.mqtt.client as mqtt
import time
import os
from datetime import datetime
import cv2
from picamera2 import Picamera2

# Initialize Firebase Admin SDK with credentials and Firestore/Storage setup
cred = credentials.Certificate('/home/pi/firebase-credentials.json')
firebase_admin.initialize_app(cred, {'storageBucket': 'task-8d-12a23.appspot.com'})

db = firestore.client()  # Firestore client
bucket = storage.bucket()  # Firebase storage bucket

# Initialize Flask app
app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # You should use a stronger secret key

# MQTT Configuration for communication with Arduino
broker = 'broker.emqx.io'
topic_request = 'fingerprint/enroll/request'
topic_response = 'fingerprint/data'

client = mqtt.Client()  # MQTT client instance

# Route for the index page
@app.route('/')
def index():
    return render_template('index.html')

# Initialize global student data and fingerprint_received flag
student_data = None
fingerprint_received = False

# Function to handle incoming MQTT messages from Arduino
def on_message(client, userdata, msg):
    global student_data, fingerprint_received

    print(f"Message received: Topic: {msg.topic}, Payload: {msg.payload.decode()}")  # Debug print
    
    if msg.topic == topic_response:
        fingerprint_id = msg.payload.decode()
        fingerprint_received = True
        print(f"Fingerprint ID received: {fingerprint_id}")  # Debug print
        
        if student_data:
            student_ref = db.collection('students').document(student_data['rollno'])
            student_ref.update({'fingerprint_id': fingerprint_id})
            print(f"Fingerprint ID {fingerprint_id} saved for student {student_data['name']} ({student_data['rollno']})")
            capture_photos(student_data['name'], student_data['rollno'], student_data['email'])
            student_data = None  # Reset after capturing photos
        else:
            print("Student data is not available.")

# Set the callback function for MQTT message handling
client.on_message = on_message
client.connect(broker)  # Connect to MQTT broker
client.subscribe(topic_response)  # Subscribe to the fingerprint response topic
client.loop_start()  # Start listening for MQTT messages

# Route for admin login page
@app.route('/admin_login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        admin_id = request.form.get('admin_id')
        admin_password = request.form.get('admin_password')

        # Query Firestore for admin credentials
        admins_ref = db.collection('admins')
        docs = admins_ref.where('admin_id', '==', admin_id).where('admin_password', '==', admin_password).stream()

        if any(docs):  # If admin credentials match
            return redirect(url_for('admin_options'))
        else:
            error = "Wrong password"
            return render_template('admin_login.html', error=error)

    return render_template('admin_login.html')

# Route for student login page (not yet implemented)
@app.route('/student_login', methods=['GET', 'POST'])
def student_login():
    if request.method == 'POST':
        email = request.form.get('email')
        rollno = request.form.get('rollno')

        # Query Firestore for student with matching email and roll number
        student_ref = db.collection('students').where('email', '==', email).where('rollno', '==', rollno)
        docs = student_ref.stream()
        
        student_data = None
        for doc in docs:
            student_data = doc.to_dict()  # Get student data if a match is found

        if student_data:
            session['student_data'] = student_data  # Store student data in session
            return redirect(url_for('student_dashboard'))
        else:
            error = "Invalid email or roll number"
            return render_template('student_login.html', error=error)

    return render_template('student_login.html')

# Route for student dashboard page
@app.route('/student_dashboard')
def student_dashboard():
    student_data = session.get('student_data')
    
    if not student_data:
        return redirect(url_for('student_login'))  # Redirect to login if no student data in session

    # Fetch attendance data from Firestore
    attendance_ref = db.collection('attendance').document(student_data['name'])
    attendance_doc = attendance_ref.get()
    
    attendance_info = {
        'total_classes': 0,
        'attendance_percentage': 0
    }

    if attendance_doc.exists:
        attendance_data = attendance_doc.to_dict()
        attendance_info['total_classes'] = attendance_data.get('total_classes', 0)
        attendance_info['attendance_percentage'] = attendance_data.get('attendance_percentage', 0)

    return render_template('student_dashboard.html', student=student_data, attendance=attendance_info)

# Admin options route
@app.route('/admin_options')
def admin_options():
    return render_template('admin_options.html')

# Route to view all students' attendance
@app.route('/view_attendance')
def view_attendance():
    try:
        students = []
        # Query the 'students' collection
        students_ref = db.collection('students')
        docs = students_ref.stream()

        for doc in docs:
            student_data = doc.to_dict()

            # Log the student data for debugging purposes
            print(f"Student data: {student_data}")  # This will print the student data for each student document

            # Get the correct field name based on what's available in the document
            roll_number = student_data.get("rollno") or student_data.get("roll_number")
            student_name = student_data.get("name")

            # Query the attendance collection for each student
            attendance_ref = db.collection('attendance').document(student_name)
            attendance_doc = attendance_ref.get()

            total_classes = 0
            attendance_percentage = 0

            if attendance_doc.exists:
                attendance_data = attendance_doc.to_dict()
                total_classes = attendance_data.get('total_classes', 0)
                attendance_percentage = attendance_data.get('attendance_percentage', 0)

            student_info = {
                'roll_number': roll_number,
                'name': student_name,
                'email': student_data.get("email"),
                'total_classes': total_classes,
                'attendance_percentage': attendance_percentage
            }

            students.append(student_info)

        return render_template('view_attendance.html', students=students)

    except Exception as e:
        print(f"Error occurred: {e}")
        return f"An error occurred: {e}"

# Route for adding a new student
@app.route('/add_student', methods=['GET', 'POST'])
def add_student():
    global student_data, fingerprint_received
    fingerprint_received = False  # Reset for each new student enrollment

    if request.method == 'POST':
        # Get the student data from the form
        student_name = request.form['name']
        student_rollno = request.form['rollno']
        student_email = request.form['email']

        # Get the next available ID for the fingerprint enrollment (auto-incrementing)
        students_ref = db.collection('students')
        student_count = len([doc for doc in students_ref.stream()])
        fingerprint_id = student_count + 1  # Start from 1 and auto-increment

        # If the fingerprint ID exceeds 127, display a full storage message
        if fingerprint_id > 127:
            return render_template('storage_full.html')

        # Save initial data to Firestore
        student_data = {'name': student_name, 'rollno': student_rollno, 'email': student_email, 'fingerprint_id': fingerprint_id}
        student_ref = db.collection('students').document(student_rollno)
        student_ref.set(student_data)

        # Publish an MQTT message to Arduino to start fingerprint enrollment
        client.publish(topic_request, "start_enrollment")

        # Display waiting message while enrollment is in progress
        return render_template('waiting_for_fingerprint.html', message="Please place your finger on the sensor")

    return render_template('add_student.html')

# Function to capture photos of the student
def capture_photos(student_name, student_rollno, student_email):
    folder = create_folder(student_name)  # Create a folder for the student’s photos
    picam2 = Picamera2()
    picam2.configure(picam2.create_preview_configuration(main={"format": 'XRGB8888', "size": (640, 480)}))
    picam2.start()
    time.sleep(2)  # Let the camera adjust

    photo_count = 0
    while photo_count < 8:
        frame = picam2.capture_array()
        cv2.imshow('Capture', frame)
        
        key = cv2.waitKey(1) & 0xFF  # Corrected indentation
        
        if key == ord(' '):  # Space key to capture photo
            photo_count += 1
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{student_name}_{timestamp}.jpg"
            filepath = os.path.join(folder, filename)
            cv2.imwrite(filepath, frame)
            print(f"Photo {photo_count} saved: {filepath}")
            
            # Upload photo to Firebase
            upload_to_firebase(filepath, student_name, student_rollno)

        elif key == ord('q'):  # Q key to quit
            break

    cv2.destroyAllWindows()
    picam2.stop()

    # Store metadata for the student
    store_student_metadata(student_name, student_rollno)

@app.route('/logout')
def logout():
    # Clear session data if necessary
    session.clear()
    # Redirect to the index page
    return redirect(url_for('index'))

# Function to store student metadata
def store_student_metadata(name, rollno):
    students_ref = db.collection('students')
    student_data = {
        'name': name,
        'roll_no': rollno,
        'timestamp': firestore.SERVER_TIMESTAMP
    }
    students_ref.add(student_data)
    print(f"Stored metadata for {name} ({rollno}) in Firestore.")

# Helper function to create a folder for student photos
def create_folder(name):
    dataset_folder = "dataset"
    if not os.path.exists(dataset_folder):
        os.makedirs(dataset_folder)

    person_folder = os.path.join(dataset_folder, name)
    if not os.path.exists(person_folder):
        os.makedirs(person_folder)
    return person_folder

# Route for waiting for fingerprint enrollment
@app.route('/waiting_for_fingerprint')
def waiting_for_fingerprint():
    if session.get('photo_captured', False):
        # If photos are captured, redirect to success page
        return redirect(url_for('upload_success'))
    return render_template('waiting_for_fingerprint.html')

# Route for upload success page
@app.route('/upload_success')
def upload_success():
    return render_template('upload_success.html')

# Function to upload images to Firebase
def upload_to_firebase(local_path, student_name, student_rollno):
    bucket = storage.bucket()
    blob = bucket.blob(f"students/{student_name}_{student_rollno}/{os.path.basename(local_path)}")
    blob.upload_from_filename(local_path)
    print(f"Uploaded {local_path} to Firebase Storage.")

# Run the Flask app
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5002)
