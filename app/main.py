from fastapi import FastAPI, File, UploadFile, HTTPException
import cv2
import numpy as np
from typing import List
from fastapi.responses import StreamingResponse
import threading

app = FastAPI()

# Dictionary to store frames from each device
device_frames = {}

# Lock for thread-safe access to frames
frame_lock = threading.Lock()


def process_frame(device_id: str, frame: np.ndarray):
    """
    Custom frame processing function.
    Modify this function to perform specific tasks (e.g., object detection).
    """
    # Example: Convert frame to grayscale
    processed_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # Save or process further
    cv2.imwrite(f"processed_device_{device_id}.jpg", processed_frame)


@app.get("/")
def home():
    return {"detail": "Hello and Welcome"}

@app.post("/upload/{device_id}")
async def upload_video(device_id: str, file: UploadFile = File(...)):
    """
    Endpoint for devices to upload a single frame or a short video.
    """
    if not file.content_type.startswith("image/") and not file.content_type.startswith("video/"):
        raise HTTPException(status_code=400, detail="Unsupported file type")

    # Read uploaded file into a numpy array
    file_bytes = await file.read()
    np_arr = np.frombuffer(file_bytes, np.uint8)

    if file.content_type.startswith("image/"):
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    else:
        # Handle video: Decode the first frame
        cap = cv2.VideoCapture(file.file)
        ret, frame = cap.read()
        cap.release()
        if not ret:
            raise HTTPException(status_code=400, detail="Failed to read video")

    # Save the frame to the device's storage
    with frame_lock:
        device_frames[device_id] = frame

    # Process the frame (e.g., analyze, store, etc.)
    process_frame(device_id, frame)

    return {"message": f"Frame from device {device_id} processed successfully"}


@app.get("/stream/{device_id}")
def stream_video(device_id: str):
    """
    Endpoint to stream processed frames for a specific device.
    """
    def generate():
        while True:
            with frame_lock:
                frame = device_frames.get(device_id)

            if frame is None:
                # Return a blank frame if no frame is available
                blank_frame = np.zeros((480, 640, 3), dtype=np.uint8)
                _, jpeg = cv2.imencode(".jpg", blank_frame)
            else:
                _, jpeg = cv2.imencode(".jpg", frame)

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n"
            )

    return StreamingResponse(generate(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/list_devices")
def list_devices():
    """
    Endpoint to list all devices currently streaming.
    """
    with frame_lock:
        devices = list(device_frames.keys())
    return {"devices": devices}
