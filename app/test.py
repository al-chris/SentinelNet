import requests
import cv2
import time

# Open the camera
cap = cv2.VideoCapture(0)

# Check if camera opened successfully
if not cap.isOpened():
    print("Error: Could not open camera.")
    exit()

# Device ID for the camera
device_id = 'camera1'
port = 80  # Change to port 80
# URL for the server    
url = "http://localhost"

config = {
    "enabled": True,
    "pixel_threshold": 30,
    "motion_threshold": 0.01,
    "buffer_seconds": 3.0,
    "min_recording_time": 5.0,
    "fps": 5
}

is_configured = False

try:
    print("Starting continuous frame capture. Press Ctrl+C to stop.")
    
    while True:
        # Capture frame from camera
        ret, frame = cap.read()
        
        if not ret:
            print("Error: Failed to capture frame")
            continue
        
        # Encode the frame as JPEG
        _, img_encoded = cv2.imencode('.jpg', frame)
        
        # Send to the server
        files = {'file': ('image.jpg', img_encoded.tobytes(), 'image/jpeg')}
        
        try:
            # Using port 80 instead of 8000
            response = requests.post(
                f'{url}:{port}/upload/{device_id}', 
                files=files,
                timeout=1  # Add timeout to prevent blocking
            )
            print(f"Frame sent. Response: {response.json()}")
        except requests.exceptions.RequestException as e:
            print(f"Error sending frame: {e}")
            

        # Configuring after the first frame is sent
        # Sending one frame will register the device, after which we configure it.

        
        if not is_configured:
            # Configure the camera on the server
            try:
                response = requests.post(
                    f"{url}:{port}/device/{device_id}/motion",
                    json=config,
                    timeout=1  # Add timeout to prevent blocking
                )
                print(f"Configuration response: {response.json()}")
                is_configured = True
            except requests.exceptions.RequestException as e:
                print(f"Error configuring device: {e}")

        
        
        # Optional: add a small delay to control frame rate
        # time.sleep(0.1)  # Uncomment to add delay between frames

except KeyboardInterrupt:
    print("\nStopping frame capture")
finally:
    # Release the camera
    cap.release()