# tests/test_main.py

import pytest
from fastapi.testclient import TestClient
from app.main import app
import cv2
import numpy as np
import io
from datetime import datetime
import json
from pathlib import Path
import shutil
import os

client = TestClient(app)

@pytest.fixture(scope="session")
def sample_video():
    """Create a small test video file"""
    output_path = "tests/assets/test_video.mp4"
    if not os.path.exists("tests/assets"):
        os.makedirs("tests/assets")
        
    # Create a simple video with colored frames
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(output_path, fourcc, 20.0, (640,480))
    
    # Generate 30 frames (different colors)
    for i in range(30):
        # Create colored frame
        frame = np.zeros((480,640,3), np.uint8)
        frame[:] = (i*8, 128, 255-i*8)  # Varying colors
        out.write(frame)
    
    out.release()
    
    yield output_path
    
    # Cleanup
    if os.path.exists(output_path):
        os.remove(output_path)

@pytest.fixture(autouse=True)
def setup_and_cleanup():
    """Setup and cleanup test environment"""
    # Setup: Create test directories
    Path("tests/recordings").mkdir(exist_ok=True)
    
    yield
    
    # Cleanup: Remove test files and directories
    if Path("devices.json").exists():
        os.remove("devices.json")
    if Path("tests/recordings").exists():
        shutil.rmtree("tests/recordings")

def create_mjpeg_frame():
    """Create a sample MJPEG frame"""
    # Create a simple colored frame
    frame = np.zeros((480,640,3), np.uint8)
    frame[:] = (0, 128, 255)  # Orange color
    
    # Encode to JPEG
    _, buffer = cv2.imencode('.jpg', frame)
    return b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n'

def test_home_endpoint():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"detail": "Hello and Welcome"}

def test_register_device():
    test_device = {
        "device_id": "TEST_CAM_001",
        "type": "ESP32-CAM"
    }
    response = client.post("/register_device", json=test_device)
    assert response.status_code == 200
    assert response.json()["status"] == "registered"
    assert response.json()["device_id"] == "TEST_CAM_001"

    # Verify device was saved
    assert Path("devices.json").exists()
    with open("devices.json", "r") as f:
        devices = json.load(f)
        assert any(d["device_id"] == "TEST_CAM_001" for d in devices)

def test_update_device_alias():
    # First register a device
    test_device = {
        "device_id": "TEST_CAM_001",
        "type": "ESP32-CAM"
    }
    client.post("/register_device", json=test_device)
    
    # Update alias
    response = client.post(
        "/device/TEST_CAM_001/alias",
        json={"alias": "Living Room"}
    )
    assert response.status_code == 200
    assert response.json()["alias"] == "Living Room"

    # Verify alias was saved
    with open("devices.json", "r") as f:
        devices = json.load(f)
        device = next(d for d in devices if d["device_id"] == "TEST_CAM_001")
        assert device["alias"] == "Living Room"

def test_list_devices():
    # Register two test devices
    devices = [
        {"device_id": "TEST_CAM_001", "type": "ESP32-CAM"},
        {"device_id": "TEST_CAM_002", "type": "ESP32-CAM"}
    ]
    for device in devices:
        client.post("/register_device", json=device)

    response = client.get("/devices")
    assert response.status_code == 200
    assert len(response.json()["devices"]) == 2

def test_upload_stream(sample_video):
    # Register a test device
    test_device = {
        "device_id": "TEST_CAM_001",
        "type": "ESP32-CAM"
    }
    client.post("/register_device", json=test_device)
    
    # First send the initial POST request with headers
    headers = {
        "Content-Type": "multipart/x-mixed-replace; boundary=frame",
        "Host": "localhost",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache"
    }
    
    # Create a video capture object
    cap = cv2.VideoCapture(sample_video)
    
    # Use a streaming approach to mimic ESP32-CAM behavior
    frame_count = 0
    while cap.isOpened() and frame_count < 5:  # Test with first 5 frames
        ret, frame = cap.read()
        if not ret:
            break
            
        # Encode frame to JPEG
        _, buffer = cv2.imencode('.jpg', frame)
        jpeg_data = buffer.tobytes()
        
        # Format frame as in ESP32-CAM code
        frame_data = f"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg_data)}\r\n\r\n".encode()
        frame_data += jpeg_data + b"\r\n"
        
        # Upload frame
        response = client.post(
            f"/upload/TEST_CAM_001",
            content=frame_data,
            headers=headers
        )
        
        assert response.status_code == 200
        frame_count += 1
    
    cap.release()

def test_stream_endpoint():
    # Register a device
    test_device = {
        "device_id": "TEST_CAM_001",
        "type": "ESP32-CAM"
    }
    client.post("/register_device", json=test_device)
    
    # Upload a test frame
    frame_data = create_mjpeg_frame()
    client.post(
        "/upload/TEST_CAM_001",
        content=frame_data,
        headers={"Content-Type": "multipart/x-mixed-replace; boundary=frame"}
    )
    
    # Test stream endpoint with a limit of 1 frame
    response = client.get("/stream/TEST_CAM_001?limit=1")
    assert response.status_code == 200
    assert response.headers["content-type"] == "multipart/x-mixed-replace; boundary=frame"
    
    # Read all the content from the response
    content = b""
    for chunk in response.iter_bytes():
        content += chunk
    
    # Check the first frame from stream
    assert content.startswith(b'--frame')
    assert b'Content-Type: image/jpeg' in content

    # Close the response
    response.close()

def test_view_all_streams():
    # Register test devices
    devices = [
        {"device_id": "TEST_CAM_001", "type": "ESP32-CAM", "alias": "Living Room"},
        {"device_id": "TEST_CAM_002", "type": "ESP32-CAM", "alias": "Kitchen"}
    ]
    for device in devices:
        client.post("/register_device", json=device)
        client.post(f"/device/{device['device_id']}/alias", json={"alias": device["alias"]})

    response = client.get("/view")
    assert response.status_code == 200
    assert "Living Room" in response.text
    assert "Kitchen" in response.text
    assert 'src="/stream/TEST_CAM_001"' in response.text
    assert 'src="/stream/TEST_CAM_002"' in response.text

def test_nonexistent_device():
    response = client.get("/stream/NONEXISTENT_CAM")
    assert response.status_code == 200  # Should return empty frames
    
def test_invalid_device_registration():
    response = client.post("/register_device", json={})
    assert response.status_code == 200
    assert response.json()["status"] == "error"

if __name__ == "__main__":
    pytest.main(["-v"])