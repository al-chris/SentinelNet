# SentinelNet

An Ethernet-based Security Camera System built with FastAPI and OpenCV + ESP32-CAM

## Overview

SentinelNet is a lightweight, scalable security camera system that enables multiple camera devices to stream video over an Ethernet network. This system provides a centralized platform for receiving, processing, and viewing security camera feeds with intelligent motion detection capabilities.

## Features

- **Multi-Device Support**: Connect and manage multiple IP cameras simultaneously
- **Real-time Video Streaming**: View live camera feeds through a web interface
- **Frame Processing**: Process incoming video frames with custom analysis logic
- **Device Management**: Easily list and track connected camera devices
- **Motion Detection Integration**: Compatible with the `MotionDetector` class to add intelligent motion-based recording
- **RESTful API**: Well-documented API endpoints for integration with other systems

## Installation

### Prerequisites

- Python 3.7+
- pip (Python package installer)

### Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/al-chris/SentinelNet.git
   cd SentinelNet
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run the application:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

## API Documentation

### Endpoints

- `GET /`: Home endpoint that returns a welcome message
- `POST /upload/{device_id}`: Upload a video frame from a camera device
- `GET /stream/{device_id}`: Stream video from a specific device
- `GET /list_devices`: List all connected camera devices

### Example Usage

#### Sending Camera Frames

To send a frame from a camera device:

```python
import requests
import cv2

# Capture frame from camera
cap = cv2.VideoCapture(0)
ret, frame = cap.read()
cap.release()

# Encode the frame as JPEG
_, img_encoded = cv2.imencode('.jpg', frame)

# Send to the server
device_id = 'camera1'
files = {'file': ('image.jpg', img_encoded.tobytes(), 'image/jpeg')}
response = requests.post(f'http://localhost:8000/upload/{device_id}', files=files)

print(response.json())
```

#### Viewing Camera Streams

Access the camera stream in a web browser or HTML page:

```html
<img src="http://localhost:8000/stream/camera1" alt="Camera Stream">
```

## Integrating Motion Detection

The system can be extended with motion detection capabilities by integrating the `MotionDetector` class, which provides:

- Background subtraction for accurate motion detection
- Intelligent recording that only captures relevant events
- Configurable motion sensitivity and recording parameters
- Buffer recording before and after motion events

## Project Structure

```
SentinelNet/
├── app/
│   ├── __init__.py
│   └── main.py
├── tests/
├── requirements.txt
├── LICENSE
└── README.md
```

## Dependencies

- fastapi - Web framework for building APIs
- uvicorn - ASGI server implementation
- opencv-python - Computer vision library for image processing
- python-multipart - Support for parsing multipart form data
- cryptography - Cryptography library for secure connections

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## Future Enhancements

- User authentication and device authorization
<!-- - Video recording and storage management -->
- Motion detection configuration through web interface
- Email/SMS notifications on detected events
- Mobile application for remote monitoring
