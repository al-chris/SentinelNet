from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import cv2
import numpy as np
import threading
from typing import Dict, Optional
from datetime import datetime, timedelta
import json
from pydantic import BaseModel
import os
from pathlib import Path
import logging

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("security_system.log"),
        logging.StreamHandler()
    ]
)

class Device(BaseModel):
    device_id: str
    alias: Optional[str] = None
    type: str
    last_seen: datetime
    status: str = "online"

class DeviceUpdate(BaseModel):
    alias: str

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For development. In production, specify your frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store frames and device info with thread-safe access
class SecuritySystem:
    def __init__(self):
        self.frames: Dict[str, bytes] = {}
        self.devices: Dict[str, Device] = {}
        self.lock = threading.Lock()
        self.load_devices()
        
        # Ensure recording directory exists
        self.recordings_dir = Path("recordings")
        self.recordings_dir.mkdir(exist_ok=True)
        self.video_writers = {}
        self.last_video_time = {}

    def load_devices(self):
        try:
            with open("devices.json", "r") as f:
                devices_data = json.load(f)
                for d in devices_data:
                    d["last_seen"] = datetime.fromisoformat(d["last_seen"])
                    self.devices[d["device_id"]] = Device(**d)
        except FileNotFoundError:
            pass

    def save_devices(self):
        with open("devices.json", "w") as f:
            devices_data = []
            for device in self.devices.values():
                device_dict = device.model_dump()
                device_dict["last_seen"] = device_dict["last_seen"].isoformat()
                devices_data.append(device_dict)
            json.dump(devices_data, f, indent=2)

    def update_frame(self, device_id: str, frame_data: bytes):
        print("update_frame() called")
        with self.lock:
            logging.info(f"Updating frame for device {device_id}") 
            self.frames[device_id] = frame_data
            if device_id in self.devices:
                logging.info(f"Device {device_id} exists, updating last seen time")
                self.devices[device_id].last_seen = datetime.now()
                self.devices[device_id].status = "online"
                self.save_frames(device_id, frame_data)

    def save_frames(self, device_id: str, frame_data: bytes):
        now = datetime.now()
        
        # Convert frame_data to numpy array for OpenCV
        try:
            nparr = np.frombuffer(frame_data, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            logging.info(f"Save_frames has been called")
            logging.info(frame)
            
            # Create device directory
            device_dir = self.recordings_dir / device_id / now.strftime("%Y-%m-%d")
            device_dir.mkdir(parents=True, exist_ok=True)
            logging.info(f"Device directory created: {device_dir}")
            
            # Check if we need to create a new video file (every 5 minutes)
            current_video_time = now.replace(second=0, microsecond=0)
            current_video_time = current_video_time.replace(minute=(current_video_time.minute // 5) * 5)
            logging.info(f"Current video time: {current_video_time}")
            
            if device_id not in self.last_video_time or self.last_video_time[device_id] != current_video_time:
                # Close previous video writer if exists
                if device_id in self.video_writers and self.video_writers[device_id]:
                    self.video_writers[device_id].release()
                
                # Create new video file
                video_filename = f"{current_video_time.strftime('%H-%M')}_to_{(current_video_time + timedelta(minutes=5)).strftime('%H-%M')}.mp4"
                video_path = str(device_dir / video_filename)
                
                # Get frame dimensions
                height, width = frame.shape[:2]
                
                # Create video writer
                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                self.video_writers[device_id] = cv2.VideoWriter(
                    video_path, fourcc, 5.0, (width, height)
                )
                
                self.last_video_time[device_id] = current_video_time
                logging.info(f"Created new video file: {video_path}")
            
            # Write frame to video
            if device_id in self.video_writers and self.video_writers[device_id]:
                self.video_writers[device_id].write(frame)
                
            # Also save individual frames occasionally (e.g., every 60 frames)
            if not hasattr(self, 'frame_counters'):
                self.frame_counters = {}
            
            if device_id not in self.frame_counters:
                self.frame_counters[device_id] = 0
            
            self.frame_counters[device_id] += 1
            
            if self.frame_counters[device_id] % 60 == 0:
                jpg_filename = now.strftime("%H-%M-%S") + ".jpg"
                jpg_path = device_dir / jpg_filename
                cv2.imwrite(str(jpg_path), frame)
                
        except Exception as e:
            logging.info(f"Error saving video frame: {str(e)}")

    def get_frame(self, device_id: str) -> Optional[bytes]:
        with self.lock:
            return self.frames.get(device_id)

    def register_device(self, device_id: str, device_type: str):
        with self.lock:
            if device_id not in self.devices:
                self.devices[device_id] = Device(
                    device_id=device_id,
                    type=device_type,
                    last_seen=datetime.now()
                )
            else:
                self.devices[device_id].last_seen = datetime.now()
                self.devices[device_id].status = "online"
            self.save_devices()

    def update_device_alias(self, device_id: str, alias: str):
        with self.lock:
            if device_id in self.devices:
                self.devices[device_id].alias = alias
                self.save_devices()
                return True
            return False

system = SecuritySystem()


@app.get("/")
async def home():
    return {"detail": "Hello and Welcome"}


@app.post("/register_device")
async def register_device(request: Request):
    data = await request.json()
    device_id = data.get("device_id")
    device_type = data.get("type")
    if device_id and device_type:
        system.register_device(device_id, device_type)
        return {"status": "registered", "device_id": device_id}
    return {"status": "error", "message": "Invalid registration data"}

@app.post("/device/{device_id}/alias")
async def update_device_alias(device_id: str, update: DeviceUpdate):
    if system.update_device_alias(device_id, update.alias):
        return {"status": "updated", "device_id": device_id, "alias": update.alias}
    return {"status": "error", "message": "Device not found"}

@app.get("/devices")
async def list_devices():
    return {"devices": list(system.devices.values())}

@app.post("/upload/{device_id}")
async def upload_stream(device_id: str, request: Request):
    """
    Endpoint to receive camera frames from devices.
    Supports both raw JPEG uploads and multipart/form-data uploads.
    
    Args:
        device_id: The unique identifier for the camera device
        request: The incoming HTTP request containing image data
    
    Returns:
        JSON response indicating success or failure
    """
    current_time = datetime.now()
    
    # Register the device if it doesn't exist
    if device_id not in system.devices:
        system.register_device(device_id, "ESP32-CAM")
        logging.info(f"[{current_time.strftime('%Y-%m-%d %H:%M:%S')}] New device registered: {device_id}")
    else:
        logging.info(f"[{current_time.strftime('%Y-%m-%d %H:%M:%S')}] Received upload from: {device_id}")
    
    # Get the content-type header
    content_type = request.headers.get("content-type", "").lower()
    
    try:
        # Option 1: Handle raw JPEG uploads (from ESP32-CAM)
        if "image/jpeg" in content_type:
            try:
                # Get the raw request body
                raw_data = await request.body()
                
                # Basic validation for JPEG format
                if len(raw_data) < 2:
                    return {"status": "error", "message": "Empty or invalid image data"}
                
                if raw_data.startswith(b'\xff\xd8') and b'\xff\xd9' in raw_data:
                    # Valid JPEG data - update the frame
                    system.update_frame(device_id, raw_data)
                    
                    return {
                        "status": "success", 
                        "message": f"JPEG frame received ({len(raw_data)} bytes)",
                        "timestamp": current_time.isoformat()
                    }
                else:
                    return {
                        "status": "error", 
                        "message": "Invalid JPEG data (missing JPEG markers)"
                    }
            
            except Exception as e:
                print(f"Error processing raw JPEG: {str(e)}")
                return {"status": "error", "message": f"Raw JPEG processing error: {str(e)}"}
        
        # Option 2: Handle multipart/form-data uploads (from browsers or other clients)
        elif "multipart/form-data" in content_type:
            # Create a buffer to accumulate data
            buffer = b""
            boundary = None
            
            # Extract the boundary from content-type header
            for part in content_type.split(';'):
                part = part.strip()
                if part.startswith('boundary='):
                    boundary = part[9:].strip('"').encode()
                    boundary = b"--" + boundary
                    break
            
            if not boundary:
                return {
                    "status": "error", 
                    "message": "Missing boundary in multipart/form-data"
                }
            
            try:
                # Process the incoming multipart stream
                async for chunk in request.stream():
                    buffer += chunk
                    
                    # Look for complete frames
                    while True:
                        # Find the boundary
                        start_pos = buffer.find(boundary)
                        if start_pos == -1:
                            break
                        
                        # Find the end of headers (double CRLF)
                        headers_end = buffer.find(b"\r\n\r\n", start_pos)
                        if headers_end == -1:
                            break
                        
                        # Extract content length if present
                        content_length = None
                        header_text = buffer[start_pos:headers_end].decode('utf-8', errors='ignore')
                        for line in header_text.split('\r\n'):
                            if line.lower().startswith('content-length:'):
                                try:
                                    content_length = int(line.split(':', 1)[1].strip())
                                except ValueError:
                                    pass
                        
                        # If we couldn't find content length, try to find next boundary
                        if content_length is None:
                            next_boundary = buffer.find(boundary, start_pos + len(boundary))
                            if next_boundary == -1:
                                break  # Not enough data yet
                            
                            # Extract the frame data
                            data_start = headers_end + 4  # +4 for \r\n\r\n
                            frame_data = buffer[data_start:next_boundary]
                            
                            # Update buffer to remove processed data
                            buffer = buffer[next_boundary:]
                        else:
                            # We have content length, extract frame precisely
                            data_start = headers_end + 4  # +4 for \r\n\r\n
                            data_end = data_start + content_length
                            
                            # Check if we have enough data
                            if len(buffer) < data_end:
                                break  # Not enough data yet
                            
                            frame_data = buffer[data_start:data_end]
                            
                            # Update buffer to remove processed data
                            buffer = buffer[data_end:]
                        
                        # Process the frame data - look for JPEG markers
                        jpeg_start = frame_data.find(b'\xff\xd8')
                        jpeg_end = frame_data.rfind(b'\xff\xd9')
                        
                        if jpeg_start != -1 and jpeg_end != -1 and jpeg_end > jpeg_start:
                            jpeg_data = frame_data[jpeg_start:jpeg_end+2]
                            system.update_frame(device_id, jpeg_data)
                            logging.info(f"Processed multipart frame: {len(jpeg_data)} bytes")
                
                return {
                    "status": "success", 
                    "message": "Multipart stream processed",
                    "timestamp": current_time.isoformat()
                }
                
            except Exception as e:
                print(f"Error processing multipart data: {str(e)}")
                return {"status": "error", "message": f"Multipart processing error: {str(e)}"}
        
        # Unsupported content type
        else:
            return {
                "status": "error", 
                "message": f"Unsupported content-type: {content_type}. Expected 'image/jpeg' or 'multipart/form-data'"
            }
    
    except Exception as e:
        print(f"Unhandled error in upload_stream: {str(e)}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": f"Server error: {str(e)}"}
    
@app.get("/view")
async def view_all_streams():
    """Returns HTML page with all camera streams"""
    devices = list(system.devices.values())
    streams_html = ""
    for device in devices:
        name = device.alias or device.device_id
        streams_html += f"""
        <div style="margin: 20px;">
            <h2>{name}</h2>
            <img src="/stream/{device.device_id}" style="max-width: 640px;" />
        </div>
        """
    
    html = f"""
    <html>
        <head>
            <title>Security Camera System</title>
            <style>
                body {{ font-family: Arial, sans-serif; }}
                .stream-container {{ display: flex; flex-wrap: wrap; }}
            </style>
        </head>
        <body>
            <h1>Security Camera System</h1>
            <div class="stream-container">
                {streams_html}
            </div>
        </body>
    </html>
    """
    return HTMLResponse(content=html)

@app.get("/stream/{device_id}")
async def stream_video(device_id: str, limit: int = None):
    def generate():
        frame_count = 0
        while limit is None or frame_count < limit:
            frame_data = system.get_frame(device_id)
            if frame_data is None:
                # If device doesn't exist or has no frames, yield a blank frame or stop streaming
                blank = np.zeros((480, 640, 3), dtype=np.uint8)
                _, buffer = cv2.imencode('.jpg', blank)
                frame_data = buffer.tobytes()

            # If device doesn't exist or no frames, yield a blank frame and break after one iteration
            if device_id not in system.devices:
                yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame_data + b'\r\n'
                break

            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_data + b'\r\n')

            frame_count += 1

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=80)