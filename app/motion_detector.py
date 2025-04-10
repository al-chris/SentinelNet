# motion_detector.py

import cv2
import numpy as np
import os
from datetime import datetime
import time
from collections import deque
import threading
import concurrent.futures
import json
from pathlib import Path
from typing import Optional, List, Tuple, Deque, Dict, Any

class MotionDetector:
    def __init__(
        self,
        pixel_threshold: int = 30,
        motion_threshold: float = 0.01,
        buffer_seconds: float = 3.0,
        fps: int = 15,
        save_dir: str = "motion_captures",
        min_recording_time: float = 5.0,
        config_path: str = "system_config.json"
    ):
        """
        Initialize motion detector with background subtraction.
        
        Args:
            pixel_threshold: Threshold for pixel difference detection
            motion_threshold: Percentage of frame that must change to trigger motion
            buffer_seconds: Seconds of video to save before/after motion
            fps: Frames per second of the video stream
            save_dir: Directory to save motion clips
            min_recording_time: Minimum recording time in seconds
            config_path: Path to system configuration file
        """
        # Load system configuration
        self.config = self._load_config(config_path)
        
        # Create background subtractors - we'll use two methods for better results
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=500, varThreshold=16, detectShadows=False
        )
        self.bg_subtractor_knn = cv2.createBackgroundSubtractorKNN(
            history=500, dist2Threshold=400.0, detectShadows=False
        )
        
        # Parameters
        self.pixel_threshold = pixel_threshold
        self.motion_threshold = motion_threshold
        self.buffer_seconds = buffer_seconds
        self.buffer_frames = int(buffer_seconds * fps)
        self.min_recording_frames = int(min_recording_time * fps)
        self.fps = fps
        
        # Create directory for saving
        self.save_dir = save_dir
        os.makedirs(save_dir, exist_ok=True)
        
        # Internal state
        self.prev_frame = None
        self.frame_buffer = []  # Store all frames in segment
        self.motion_frames = []  # Track which frames have motion
        
        # Set up segment recording
        self.segment_minutes = self.config.get("recording_segment_minutes", 1)
        self.segment_frames = int(self.segment_minutes * 60 * fps)
        self.frame_count = 0
        
        # Thread pool for background processing
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self.lock = threading.Lock()
    
    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Load system configuration from JSON file."""
        try:
            with open(config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading config: {e}")
            return {"recording_segment_minutes": 1}
    
    def detect_motion_pixel_diff(self, frame: np.ndarray) -> Tuple[bool, np.ndarray]:
        """
        Detect motion by comparing pixel values between consecutive frames.
        
        Args:
            frame: Current frame
        
        Returns:
            Tuple of (motion_detected, difference_mask)
        """
        # Convert to grayscale
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)
        
        # Initialize prev_frame if it's the first frame
        if self.prev_frame is None:
            self.prev_frame = gray
            return False, np.zeros_like(gray)
        
        # Calculate absolute difference between current and previous frame
        frame_delta = cv2.absdiff(self.prev_frame, gray)
        
        # Apply threshold to delta
        thresh = cv2.threshold(frame_delta, self.pixel_threshold, 255, cv2.THRESH_BINARY)[1]
        
        # Dilate threshold image to fill in holes
        thresh = cv2.dilate(thresh, None, iterations=2)
        
        # Update previous frame
        self.prev_frame = gray
        
        # Calculate percentage of changed pixels
        changed_pixels = np.count_nonzero(thresh)
        total_pixels = thresh.size
        motion_percent = changed_pixels / total_pixels
        
        # Determine if motion is detected
        motion_detected = motion_percent > self.motion_threshold
        
        return motion_detected, thresh
    
    def detect_motion_bg_subtraction(self, frame: np.ndarray) -> Tuple[bool, np.ndarray]:
        """
        Detect motion using background subtraction models.
        
        Args:
            frame: Current frame
        
        Returns:
            Tuple of (motion_detected, foreground_mask)
        """
        # Apply background subtraction
        fg_mask_mog2 = self.bg_subtractor.apply(frame)
        fg_mask_knn = self.bg_subtractor_knn.apply(frame)
        
        # Combine masks
        combined_mask = cv2.bitwise_or(fg_mask_mog2, fg_mask_knn)
        
        # Apply threshold to remove noise
        thresh = cv2.threshold(combined_mask, 128, 255, cv2.THRESH_BINARY)[1]
        
        # Apply morphological operations to clean up the mask
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
        
        # Calculate percentage of foreground pixels
        foreground_pixels = np.count_nonzero(thresh)
        total_pixels = thresh.size
        motion_percent = foreground_pixels / total_pixels
        
        # Determine if motion is detected
        motion_detected = motion_percent > self.motion_threshold
        
        return motion_detected, thresh
    
    def process_segment(self, frames: List[np.ndarray], motion_frames: List[bool], timestamp: str) -> None:
        """
        Process a complete segment in the background and extract motion clips.
        
        Args:
            frames: List of frames in the segment
            motion_frames: List of booleans indicating motion for each frame
            timestamp: Timestamp for the segment
        """
        print(f"Processing segment from {timestamp} in background thread...")
        
        # If no motion detected in entire segment, we can skip processing
        if not any(motion_frames):
            print("No motion detected in segment, skipping processing.")
            return
        
        # Find motion ranges with buffer
        motion_ranges = self._find_motion_ranges(motion_frames)
        
        # Create motion clips for each range
        for i, (start_idx, end_idx) in enumerate(motion_ranges):
            clip_timestamp = f"{timestamp}_clip{i+1}"
            self._create_motion_clip(frames, start_idx, end_idx, clip_timestamp)
    
    def _find_motion_ranges(self, motion_frames: List[bool]) -> List[Tuple[int, int]]:
        """
        Find start and end indices of motion with buffer frames.
        
        Args:
            motion_frames: List of booleans indicating motion for each frame
        
        Returns:
            List of (start_idx, end_idx) tuples for each motion sequence
        """
        ranges = []
        in_motion = False
        start_idx = 0
        
        for i, has_motion in enumerate(motion_frames):
            if has_motion and not in_motion:
                # Start of motion - go back buffer_frames if possible
                in_motion = True
                start_idx = max(0, i - self.buffer_frames)
            elif not has_motion and in_motion:
                # Check if we've had no motion for buffer_frames
                no_motion_window = min(self.buffer_frames, i)
                if i >= no_motion_window and not any(motion_frames[i-no_motion_window:i]):
                    in_motion = False
                    # End motion sequence, include buffer after
                    end_idx = min(len(motion_frames) - 1, i + self.buffer_frames)
                    # Only save if long enough
                    motion_duration = end_idx - start_idx + 1
                    if motion_duration >= self.min_recording_frames:
                        ranges.append((start_idx, end_idx))
        
        # Handle case where motion continues until the end of the segment
        if in_motion:
            end_idx = len(motion_frames) - 1
            motion_duration = end_idx - start_idx + 1
            if motion_duration >= self.min_recording_frames:
                ranges.append((start_idx, end_idx))
        
        # Merge overlapping ranges
        if ranges:
            merged_ranges = [ranges[0]]
            for current_start, current_end in ranges[1:]:
                prev_start, prev_end = merged_ranges[-1]
                
                if current_start <= prev_end:
                    # Overlapping ranges, merge them
                    merged_ranges[-1] = (prev_start, max(prev_end, current_end))
                else:
                    # Non-overlapping, add as new range
                    merged_ranges.append((current_start, current_end))
            
            return merged_ranges
        
        return []
    
    def _create_motion_clip(self, frames: List[np.ndarray], start_idx: int, end_idx: int, timestamp: str) -> None:
        """
        Create a video clip from the specified range of frames.
        
        Args:
            frames: List of all frames
            start_idx: Starting frame index
            end_idx: Ending frame index
            timestamp: Timestamp identifier
        """
        if not frames or start_idx >= len(frames) or start_idx > end_idx:
            print(f"Invalid frame range: {start_idx}-{end_idx}, total frames: {len(frames)}")
            return
            
        video_path = os.path.join(self.save_dir, f"motion_{timestamp}.mp4")
        
        # Get frame dimensions
        h, w = frames[0].shape[:2]
        
        # Create video writer
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        video_writer = cv2.VideoWriter(video_path, fourcc, self.fps, (w, h))
        
        # Write frames in the range
        for i in range(start_idx, min(end_idx + 1, len(frames))):
            video_writer.write(frames[i])
        
        # Release writer
        video_writer.release()
        
        # Calculate duration
        duration = (end_idx - start_idx + 1) / self.fps
        print(f"Created motion clip {video_path} - {duration:.1f} seconds")
    
    def process_frame(self, frame: np.ndarray) -> Tuple[np.ndarray, bool]:
        """
        Process a frame from the video stream.
        
        Args:
            frame: Video frame to process
        
        Returns:
            Tuple of (annotated_frame, motion_detected)
        """
        # Create a copy for annotation
        annotated_frame = frame.copy()
        
        # Detect motion using both methods
        motion_pixel, diff_mask = self.detect_motion_pixel_diff(frame)
        motion_bg, bg_mask = self.detect_motion_bg_subtraction(frame)
        
        # Combine results (motion detected if either method detects it)
        current_motion = motion_pixel or motion_bg
        
        # Add frame to buffer and track motion state
        with self.lock:
            self.frame_buffer.append(frame.copy())
            self.motion_frames.append(current_motion)
            self.frame_count += 1
        
        # Annotate frame with motion info
        cv2.putText(
            annotated_frame,
            f"Pixel Motion: {'Yes' if motion_pixel else 'No'}",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 0, 255) if motion_pixel else (0, 255, 0),
            2
        )
        
        cv2.putText(
            annotated_frame,
            f"BG Motion: {'Yes' if motion_bg else 'No'}",
            (10, 60),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 0, 255) if motion_bg else (0, 255, 0),
            2
        )
        
        if current_motion:
            # Draw rectangle to indicate motion
            cv2.putText(
                annotated_frame,
                "Motion Detected",
                (10, 90),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 0, 255),
                2
            )
        
        # Show frame count and segment information
        cv2.putText(
            annotated_frame,
            f"Segment: {self.frame_count}/{self.segment_frames}",
            (10, 120),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 0, 0),
            2
        )
        
        # Check if we've reached the segment size to process
        if self.frame_count >= self.segment_frames:
            self._process_current_segment()
        
        return annotated_frame, current_motion
    
    def _process_current_segment(self) -> None:
        """Process the current segment in a background thread."""
        with self.lock:
            # Make copies of our buffers
            frames_copy = self.frame_buffer.copy()
            motion_frames_copy = self.motion_frames.copy()
            
            # Reset the buffers
            self.frame_buffer = []
            self.motion_frames = []
            self.frame_count = 0
        
        # Generate timestamp for this segment
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Submit processing task to thread pool
        self.executor.submit(self.process_segment, frames_copy, motion_frames_copy, timestamp)
    
    def __del__(self):
        """Clean up resources."""
        self.executor.shutdown()

def create_motion_detector(
    pixel_threshold: int = 30,
    motion_threshold: float = 0.01,
    buffer_seconds: float = 3.0,
    fps: int = 15,
    save_dir: str = "motion_captures",
    min_recording_time: float = 5.0,
    config_path: str = "system_config.json"
) -> MotionDetector:
    """
    Create and return a configured MotionDetector instance.
    
    This function can be used to create the detector that will be
    integrated with a FastAPI endpoint.
    """
    return MotionDetector(
        pixel_threshold=pixel_threshold,
        motion_threshold=motion_threshold,
        buffer_seconds=buffer_seconds,
        fps=fps,
        save_dir=save_dir,
        min_recording_time=min_recording_time,
        config_path=config_path
    )