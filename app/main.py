from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, HTMLResponse
import cv2
import numpy as np
import threading
from typing import Dict, Optional
from datetime import datetime
import json
from pydantic import BaseModel
import os
from pathlib import Path

class Device(BaseModel):
    device_id: str
    alias: Optional[str] = None
    type: str
    last_seen: datetime
    status: str = "online"

class DeviceUpdate(BaseModel):
    alias: str

app = FastAPI()

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
        with self.lock:
            self.frames[device_id] = frame_data
            if device_id in self.devices:
                self.devices[device_id].last_seen = datetime.now()
                self.devices[device_id].status = "online"
                self.save_frames(device_id, frame_data)

    def save_frames(self, device_id: str, frame_data: bytes):
        # Save frame periodically (e.g., every minute)
        now = datetime.now()
        if now.second == 0:  # Save once per minute
            device_dir = self.recordings_dir / device_id / now.strftime("%Y-%m-%d")
            device_dir.mkdir(parents=True, exist_ok=True)
            
            filename = now.strftime("%H-%M-%S.jpg")
            with open(device_dir / filename, "wb") as f:
                f.write(frame_data)

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
    async for chunk in request.stream():
        if chunk.startswith(b"--frame"):
            try:
                jpeg_start = chunk.find(b'\xff\xd8')
                jpeg_end = chunk.find(b'\xff\xd9')
                if jpeg_start != -1 and jpeg_end != -1:
                    jpeg_data = chunk[jpeg_start:jpeg_end+2]
                    system.update_frame(device_id, jpeg_data)
            except Exception as e:
                print(f"Error processing frame: {e}")
                continue
    return {"message": "Stream ended"}

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