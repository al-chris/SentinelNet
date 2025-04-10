from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import cv2
import numpy as np
import threading
from typing import Dict, Optional, List, Tuple
from datetime import datetime, timedelta
import json
from pydantic import BaseModel
import os
from pathlib import Path
import logging
import time
import uuid
from .motion_detector import create_motion_detector, MotionDetector

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
    motion_detection: bool = False

class DeviceUpdate(BaseModel):
    alias: str

class MotionConfig(BaseModel):
    enabled: bool = True
    pixel_threshold: int = 30
    motion_threshold: float = 0.01
    buffer_seconds: float = 3.0
    min_recording_time: float = 5.0
    fps: int = 15

class SecuritySystemConfig(BaseModel):
    continuous_recording: bool = True
    motion_detection: bool = True
    recording_segment_minutes: int = 5
    jpeg_snapshot_interval: int = 60  # Save JPEG every N frames

app = FastAPI(
    title="Security Camera API",
    description="API for managing security cameras with motion detection",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

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
        # Core data structures
        self.frames: Dict[str, bytes] = {}
        self.devices: Dict[str, Device] = {}
        self.lock = threading.Lock()
        
        # Configuration
        self.config = SecuritySystemConfig()
        self.load_config()
        self.load_devices()
        
        # Ensure recording directories exist
        self.recordings_dir = Path("recordings")
        self.recordings_dir.mkdir(exist_ok=True)
        
        # Continuous recording state
        self.video_writers = {}
        self.last_video_time = {}
        self.frame_counters = {}
        
        # Motion detection
        self.motion_detectors: Dict[str, MotionDetector] = {}
        self.motion_detected: Dict[str, bool] = {}
        self.annotated_frames: Dict[str, bytes] = {}
        
        # Initialize motion detectors for existing devices
        for device_id in self.devices:
            if self.devices[device_id].motion_detection:
                self.initialize_motion_detector(device_id)

    def load_config(self):
        """Load system configuration from file"""
        try:
            with open("system_config.json", "r") as f:
                config_data = json.load(f)
                self.config = SecuritySystemConfig(**config_data)
        except FileNotFoundError:
            # Use defaults
            self.save_config()
    
    def save_config(self):
        """Save system configuration to file"""
        with open("system_config.json", "w") as f:
            json.dump(self.config.model_dump(), f, indent=2)
    
    def load_devices(self):
        """Load device information from file"""
        try:
            with open("devices.json", "r") as f:
                devices_data = json.load(f)
                for d in devices_data:
                    d["last_seen"] = datetime.fromisoformat(d["last_seen"])
                    self.devices[d["device_id"]] = Device(**d)
        except FileNotFoundError:
            pass

    def save_devices(self):
        """Save device information to file"""
        with open("devices.json", "w") as f:
            devices_data = []
            for device in self.devices.values():
                device_dict = device.model_dump()
                device_dict["last_seen"] = device_dict["last_seen"].isoformat()
                devices_data.append(device_dict)
            json.dump(devices_data, f, indent=2)
    
    def initialize_motion_detector(self, device_id: str, config: Optional[MotionConfig] = None):
        """Initialize or update a motion detector for a device"""
        device_motion_dir = self.recordings_dir / device_id / "motion" / datetime.now().strftime("%Y-%m-%d")
        device_motion_dir.mkdir(exist_ok=True, parents=True)
        
        # Use provided config or default values
        if config is None:
            config = MotionConfig()
        
        # Use absolute path to system_config.json
        config_path = Path("system_config.json").absolute()
        
        self.motion_detectors[device_id] = create_motion_detector(
            pixel_threshold=config.pixel_threshold,
            motion_threshold=config.motion_threshold,
            buffer_seconds=config.buffer_seconds,
            fps=config.fps,
            save_dir=str(device_motion_dir),
            min_recording_time=config.min_recording_time,
            config_path=str(config_path)
        )
        logging.info(f"Initialized motion detector for device {device_id}")
        
        # Mark the device as having motion detection enabled
        if device_id in self.devices:
            self.devices[device_id].motion_detection = True
            self.save_devices()
    
    def update_frame(self, device_id: str, frame_data: bytes):
        """Update frame for a device and process for recording/motion detection"""
        with self.lock:
            # Only store the frame if it's valid JPEG data
            if not frame_data.startswith(b'\xff\xd8') or not frame_data.endswith(b'\xff\xd9'):
                logging.warning(f"Received invalid JPEG frame from {device_id} - not updating")
                return
            
            # Store the raw frame
            self.frames[device_id] = frame_data
            
            # Update device information
            if device_id in self.devices:
                self.devices[device_id].last_seen = datetime.now()
                self.devices[device_id].status = "online"
                
                # Convert JPEG to OpenCV format for processing
                try:
                    nparr = np.frombuffer(frame_data, np.uint8)
                    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    
                    if frame is None or frame.size == 0:
                        logging.warning(f"Failed to decode frame from {device_id} - skipping processing")
                        return
                    
                    # Process for continuous recording if enabled
                    if self.config.continuous_recording:
                        self.process_continuous_recording(device_id, frame)
                    
                    # Process for motion detection if enabled for this device
                    if self.config.motion_detection and self.devices[device_id].motion_detection:
                        self.process_motion_detection(device_id, frame)
                
                except Exception as e:
                    logging.error(f"Error processing frame: {str(e)}")
    
    def process_continuous_recording(self, device_id: str, frame: np.ndarray):
        """Process frame for continuous recording"""
        now = datetime.now()
        
        # Create device directory
        device_dir = self.recordings_dir / device_id / "continuous" / now.strftime("%Y-%m-%d")
        device_dir.mkdir(parents=True, exist_ok=True)
        
        # Check if we need to create a new video file (every X minutes)
        current_video_time = now.replace(second=0, microsecond=0)
        current_video_time = current_video_time.replace(
            minute=(current_video_time.minute // self.config.recording_segment_minutes) * self.config.recording_segment_minutes
        )
        
        try:
            if device_id not in self.last_video_time or self.last_video_time[device_id] != current_video_time:
                # Close previous video writer if exists
                if device_id in self.video_writers and self.video_writers[device_id]:
                    self.video_writers[device_id].release()
                
                # Create new video file
                video_filename = f"{current_video_time.strftime('%H-%M')}_to_" \
                    f"{(current_video_time + timedelta(minutes=self.config.recording_segment_minutes)).strftime('%H-%M')}.mp4"
                video_path = str(device_dir / video_filename)
                
                # Get frame dimensions
                height, width = frame.shape[:2]
                
                # Create video writer
                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                self.video_writers[device_id] = cv2.VideoWriter(
                    video_path, fourcc, 15.0, (width, height)
                )
                
                self.last_video_time[device_id] = current_video_time
                logging.info(f"Created new continuous recording file: {video_path}")
            
            # Write frame to video
            if device_id in self.video_writers and self.video_writers[device_id]:
                if self.video_writers[device_id].isOpened():
                    self.video_writers[device_id].write(frame)
                else:
                    logging.warning(f"Video writer for {device_id} is not open - recreating")
                    # Get frame dimensions
                    height, width = frame.shape[:2]
                    
                    # Create video writer
                    video_filename = f"{current_video_time.strftime('%H-%M')}_to_" \
                        f"{(current_video_time + timedelta(minutes=self.config.recording_segment_minutes)).strftime('%H-%M')}.mp4"
                    video_path = str(device_dir / video_filename)
                    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                    self.video_writers[device_id] = cv2.VideoWriter(
                        video_path, fourcc, 15.0, (width, height)
                    )
                    
                    if self.video_writers[device_id].isOpened():
                        self.video_writers[device_id].write(frame)
            
            # Save individual frames occasionally
            if device_id not in self.frame_counters:
                self.frame_counters[device_id] = 0
            
            self.frame_counters[device_id] += 1
            
            if self.frame_counters[device_id] % self.config.jpeg_snapshot_interval == 0:
                jpg_filename = now.strftime("%H-%M-%S") + ".jpg"
                jpg_path = device_dir / jpg_filename
                cv2.imwrite(str(jpg_path), frame)
                
        except Exception as e:
            logging.error(f"Error in continuous recording: {str(e)}")
    
    def process_motion_detection(self, device_id: str, frame: np.ndarray):
        """Process frame for motion detection"""
        try:
            # Initialize motion detector if needed
            if device_id not in self.motion_detectors:
                self.initialize_motion_detector(device_id)
            
            # Process frame through motion detector
            annotated_frame, motion_detected = self.motion_detectors[device_id].process_frame(frame)
            
            # Store the motion detection status
            old_status = self.motion_detected.get(device_id, False)
            self.motion_detected[device_id] = motion_detected
            
            # Log when motion starts or stops
            if motion_detected and not old_status:
                logging.info(f"Motion STARTED on device {device_id}")
            elif not motion_detected and old_status:
                logging.info(f"Motion STOPPED on device {device_id}")
            
            # Store the annotated frame for viewing
            _, annotated_buffer = cv2.imencode('.jpg', annotated_frame)
            self.annotated_frames[device_id] = annotated_buffer.tobytes()
            
        except Exception as e:
            logging.error(f"Error in motion detection: {str(e)}")

    def get_frame(self, device_id: str) -> Optional[bytes]:
        """Get the latest frame for a device"""
        with self.lock:
            return self.frames.get(device_id)
    
    def get_annotated_frame(self, device_id: str) -> Optional[bytes]:
        """Get the latest motion-annotated frame for a device"""
        with self.lock:
            return self.annotated_frames.get(device_id)

    def register_device(self, device_id: str, device_type: str):
        """Register a new device or update an existing one"""
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

    def update_device_alias(self, device_id: str, alias: str) -> bool:
        """Update the alias for a device"""
        with self.lock:
            if device_id in self.devices:
                self.devices[device_id].alias = alias
                self.save_devices()
                return True
            return False
    
    def configure_motion_detection(self, device_id: str, config: MotionConfig) -> bool:
        """Configure motion detection for a device"""
        with self.lock:
            if device_id not in self.devices:
                return False
            
            if config.enabled:
                self.initialize_motion_detector(device_id, config)
                self.devices[device_id].motion_detection = True
            else:
                # Disable motion detection
                if device_id in self.motion_detectors:
                    if hasattr(self.motion_detectors[device_id], 'stop_recording'):
                        self.motion_detectors[device_id].stop_recording()
                    del self.motion_detectors[device_id]
                self.devices[device_id].motion_detection = False
            
            self.save_devices()
            return True
    
    def set_system_config(self, config: SecuritySystemConfig) -> bool:
        """Update the system configuration"""
        with self.lock:
            self.config = config
            self.save_config()
            return True
    
    def cleanup(self):
        """Clean up resources when shutting down"""
        with self.lock:
            # Close all video writers
            for device_id, writer in self.video_writers.items():
                if writer and writer.isOpened():
                    writer.release()
                    logging.info(f"Closed video writer for {device_id}")
            
            # Process any remaining frames in motion detectors
            for device_id, detector in self.motion_detectors.items():
                # Force processing of any buffered frames
                if hasattr(detector, '_process_current_segment'):
                    try:
                        detector._process_current_segment()
                        logging.info(f"Processed remaining motion frames for {device_id}")
                    except Exception as e:
                        logging.error(f"Error processing final motion segment for {device_id}: {str(e)}")
                
                # Ensure the executor is shut down properly
                if hasattr(detector, 'executor'):
                    detector.executor.shutdown(wait=True)
                    logging.info(f"Shut down motion detector executor for {device_id}")

# Initialize the security system
system = SecuritySystem()

# FastAPI endpoint routes
@app.get("/")
async def home():
    return {"detail": "Security Camera System with Motion Detection"}

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
    devices_list = list(system.devices.values())
    for device in devices_list:
        device_id = device.device_id
        if device_id in system.motion_detected:
            setattr(device, "motion_active", system.motion_detected[device_id])
        else:
            setattr(device, "motion_active", False)
    
    return {"devices": devices_list}

@app.post("/upload/{device_id}")
async def upload_stream(device_id: str, request: Request):
    """
    Endpoint to receive camera frames from devices.
    Supports both raw JPEG uploads and multipart/form-data uploads.
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
                
                # More thorough JPEG validation
                if raw_data.startswith(b'\xff\xd8') and raw_data.endswith(b'\xff\xd9'):
                    # Additional validation - try to decode with OpenCV
                    try:
                        nparr = np.frombuffer(raw_data, np.uint8)
                        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                        if frame is None or frame.size == 0:
                            return {
                                "status": "error", 
                                "message": "Unable to decode JPEG data - image may be corrupt"
                            }
                        
                        # Valid JPEG data - update the frame
                        system.update_frame(device_id, raw_data)
                        
                        # Return motion detection status if available
                        motion_status = "unknown"
                        if device_id in system.motion_detected:
                            motion_status = "active" if system.motion_detected[device_id] else "inactive"
                        
                        return {
                            "status": "success", 
                            "message": f"JPEG frame received ({len(raw_data)} bytes)",
                            "timestamp": current_time.isoformat(),
                            "motion": motion_status
                        }
                    except Exception as decode_error:
                        logging.error(f"Error decoding JPEG: {str(decode_error)}")
                        return {
                            "status": "error", 
                            "message": f"JPEG decoding error: {str(decode_error)}"
                        }
                else:
                    return {
                        "status": "error", 
                        "message": "Invalid JPEG data (missing JPEG markers)"
                    }
            
            except Exception as e:
                logging.error(f"Error processing raw JPEG: {str(e)}")
                return {"status": "error", "message": f"Raw JPEG processing error: {str(e)}"}
        
        # Option 2: Handle multipart/form-data uploads
        elif "multipart/form-data" in content_type:
            # Implementation for multipart/form-data uploads (same as your original code)
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
                            
                            # Additional validation - try to decode with OpenCV
                            try:
                                nparr = np.frombuffer(jpeg_data, np.uint8)
                                frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                                if frame is not None and frame.size > 0:
                                    system.update_frame(device_id, jpeg_data)
                                    logging.info(f"Processed multipart frame: {len(jpeg_data)} bytes")
                                else:
                                    logging.warning(f"Discarded corrupted multipart frame")
                            except Exception as decode_error:
                                logging.error(f"Error decoding multipart JPEG: {str(decode_error)}")
                
                # Return motion detection status if available
                motion_status = "unknown"
                if device_id in system.motion_detected:
                    motion_status = "active" if system.motion_detected[device_id] else "inactive"
                
                return {
                    "status": "success", 
                    "message": "Multipart stream processed",
                    "timestamp": current_time.isoformat(),
                    "motion": motion_status
                }
                
            except Exception as e:
                logging.error(f"Error processing multipart data: {str(e)}")
                return {"status": "error", "message": f"Multipart processing error: {str(e)}"}
        
        # Unsupported content type
        else:
            return {
                "status": "error", 
                "message": f"Unsupported content-type: {content_type}. Expected 'image/jpeg' or 'multipart/form-data'"
            }
    
    except Exception as e:
        logging.error(f"Unhandled error in upload_stream: {str(e)}")
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
        device_id = device.device_id
        motion_active = "No"
        if device_id in system.motion_detected and system.motion_detected[device_id]:
            motion_active = "Yes"
        
        motion_enabled = "Enabled" if device.motion_detection else "Disabled"
        
        streams_html += f"""
        <div class="camera-card">
            <div class="camera-header">
                <h2>{name}</h2>
                <span class="badge status-badge">Status: {device.status}</span>
                <span class="badge motion-badge">Motion Detection: {motion_enabled}</span>
                <span class="badge {('motion-active' if motion_active == 'Yes' else 'motion-inactive')}">
                    Motion: {motion_active}
                </span>
            </div>
            <div class="stream-container">
                <div class="stream-box">
                    <h3>Regular Stream</h3>
                    <img src="/stream/{device_id}" class="stream-img" />
                </div>
                <div class="stream-box">
                    <h3>Motion Detection Stream</h3>
                    <img src="/stream/{device_id}/annotated" class="stream-img" />
                </div>
            </div>
            <div class="controls">
                <button onclick="toggleMotionDetection('{device_id}', {str(not device.motion_detection).lower()})">
                    {('Disable' if device.motion_detection else 'Enable')} Motion Detection
                </button>
            </div>
        </div>
        """
    
    html = f"""
    <html>
        <head>
            <title>Security Camera System</title>
            <style>
                body {{ font-family: Arial, sans-serif; background-color: #f4f4f4; margin: 0; padding: 20px; }}
                h1 {{ color: #333; text-align: center; margin-bottom: 30px; }}
                .camera-card {{ background: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); margin-bottom: 30px; padding: 20px; }}
                .camera-header {{ display: flex; align-items: center; flex-wrap: wrap; margin-bottom: 15px; }}
                .camera-header h2 {{ margin: 0; margin-right: 15px; }}
                .badge {{ padding: 5px 10px; border-radius: 15px; font-size: 12px; margin-right: 10px; }}
                .status-badge {{ background-color: #e3f2fd; color: #1976d2; }}
                .motion-badge {{ background-color: #e8f5e9; color: #388e3c; }}
                .motion-active {{ background-color: #ffebee; color: #d32f2f; animation: pulse 1.5s infinite; }}
                .motion-inactive {{ background-color: #f5f5f5; color: #757575; }}
                .stream-container {{ display: flex; flex-wrap: wrap; gap: 20px; margin-bottom: 15px; }}
                .stream-box {{ flex: 1; min-width: 300px; }}
                .stream-box h3 {{ margin-top: 0; font-size: 16px; color: #555; }}
                .stream-img {{ max-width: 100%; border-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
                .controls {{ display: flex; justify-content: flex-end; }}
                .controls button {{ padding: 8px 16px; border: none; border-radius: 4px; background: #1976d2; color: white; cursor: pointer; }}
                .controls button:hover {{ background: #1565c0; }}
                @keyframes pulse {{
                    0% {{ opacity: 1; }}
                    50% {{ opacity: 0.7; }}
                    100% {{ opacity: 1; }}
                }}
            </style>
            <script>
                // Auto-refresh the page every 60 seconds
                setTimeout(function() {{
                    location.reload();
                }}, 60000);
                
                // Toggle motion detection for a device
                function toggleMotionDetection(deviceId, enable) {{
                    fetch(`/device/${{deviceId}}/motion`, {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/json',
                        }},
                        body: JSON.stringify({{
                            enabled: enable,
                            pixel_threshold: 30,
                            motion_threshold: 0.01,
                            buffer_seconds: 3.0,
                            min_recording_time: 5.0,
                            fps: 15
                        }})
                    }})
                    .then(response => response.json())
                    .then(data => {{
                        if(data.status === 'success') {{
                            location.reload();
                        }} else {{
                            alert('Error: ' + data.message);
                        }}
                    }})
                    .catch(error => {{
                        console.error('Error:', error);
                        alert('Failed to update motion detection settings');
                    }});
                }}
            </script>
        </head>
        <body>
            <h1>Security Camera System with Motion Detection</h1>
            {streams_html}
        </body>
    </html>
    """
    return HTMLResponse(content=html)

@app.get("/stream/{device_id}")
async def stream_video(device_id: str, limit: int = None):
    """Stream raw video for a device"""
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

@app.get("/stream/{device_id}/annotated")
async def stream_annotated_video(device_id: str, limit: int = None):
    """Stream motion-annotated video for a device"""
    def generate():
        frame_count = 0
        while limit is None or frame_count < limit:
            # Get motion-annotated frame if available
            frame_data = system.get_annotated_frame(device_id)
            
            # Fall back to regular frame if annotated isn't available 
            if frame_data is None:
                frame_data = system.get_frame(device_id)
            
            # Generate blank frame if necessary
            if frame_data is None:
                blank = np.zeros((480, 640, 3), dtype=np.uint8)
                cv2.putText(
                    blank,
                    "No Motion Data Available",
                    (50, 240),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1.0,
                    (255, 255, 255),
                    2
                )
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

@app.post("/device/{device_id}/motion")
async def configure_motion_detection(device_id: str, config: MotionConfig):
    """Configure motion detection for a device"""
    if system.configure_motion_detection(device_id, config):
        motion_status = "enabled" if config.enabled else "disabled"
        return {
            "status": "success", 
            "message": f"Motion detection {motion_status} for {device_id}"
        }
    return {"status": "error", "message": "Device not found"}

@app.get("/motion/events")
async def list_motion_events(device_id: Optional[str] = None, date: Optional[str] = None):
    """List motion events (recordings triggered by motion detection)"""
    events = []
    try:
        base_path = system.recordings_dir
        
        # Filter by device if specified
        devices_to_check = [device_id] if device_id else [d.device_id for d in system.devices.values()]
        
        # Filter by date if specified or use today
        check_date = date or datetime.now().strftime("%Y-%m-%d")
        
        for dev_id in devices_to_check:
            motion_dir = base_path / dev_id / "motion" / check_date
            if not motion_dir.exists():
                continue
                
            # Find all MP4 files in the motion directory
            for mp4_file in motion_dir.glob("*.mp4"):
                file_stat = mp4_file.stat()
                events.append({
                    "device_id": dev_id,
                    "filename": mp4_file.name,
                    "path": str(mp4_file.relative_to(base_path)),
                    "size_kb": round(file_stat.st_size / 1024, 2),
                    "timestamp": datetime.fromtimestamp(file_stat.st_mtime).isoformat()
                })
        
        # Sort by timestamp, newest first
        events.sort(key=lambda e: e["timestamp"], reverse=True)
        
        return {
            "status": "success",
            "date": check_date,
            "device_id": device_id,
            "event_count": len(events),
            "events": events
        }
    except Exception as e:
        logging.error(f"Error retrieving motion events: {str(e)}")
        return {"status": "error", "message": f"Failed to retrieve motion events: {str(e)}"}

@app.get("/recording/{path:path}")
async def serve_recording(path: str):
    """Serve a recording file"""
    file_path = system.recordings_dir / path
    if not file_path.exists() or not file_path.is_file():
        return {"status": "error", "message": "Recording not found"}
    
    return StreamingResponse(
        open(file_path, "rb"),
        media_type="video/mp4"
    )

@app.post("/system/config")
async def update_system_config(config: SecuritySystemConfig):
    """Update the system configuration"""
    if system.set_system_config(config):
        return {
            "status": "success",
            "message": "System configuration updated",
            "config": config
        }
    return {"status": "error", "message": "Failed to update system configuration"}

@app.get("/system/config")
async def get_system_config():
    """Get the current system configuration"""
    return {
        "status": "success",
        "config": system.config
    }

@app.on_event("shutdown")
def shutdown_event():
    """Clean up resources when shutting down"""
    system.cleanup()
    logging.info("Application shutting down, released all resources")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=80, log_level="info")