#!/usr/bin/env python3
"""
JARVIS Voice Pause/Resume Manager
- Pauses listening when JARVIS speaks
- Resumes listening after response completes
- Prevents voice overlap and misrecognition
- NO DELETIONS - Safe additive enhancement
"""

import logging
import threading
import time
from typing import Callable, Optional
from datetime import datetime
from pathlib import Path
import json

logger = logging.getLogger("jarvis.voice_pause_manager")

class VoicePauseManager:
    """
    Manages pause/resume of voice listening
    Prevents overlapping voice input/output
    """
    
    def __init__(self, state_file: str = "data/voice_pause_state.json"):
        """Initialize voice pause manager"""
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(exist_ok=True)
        
        self.is_paused = False
        self.is_speaking = False
        self.pause_start_time = None
        self.speak_start_time = None
        self.total_pause_duration = 0
        self.total_speak_duration = 0
        self.pause_count = 0
        self.speak_count = 0
        
        # Callbacks
        self.on_pause_requested: Optional[Callable] = None
        self.on_resume_requested: Optional[Callable] = None
        self.on_speaking_started: Optional[Callable] = None
        self.on_speaking_ended: Optional[Callable] = None
        
        self._load_state()
        logger.info("Voice Pause Manager initialized")
    
    def _load_state(self):
        """Load persistent pause state"""
        try:
            if self.state_file.exists():
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                    self.total_pause_duration = state.get('total_pause_duration', 0)
                    self.total_speak_duration = state.get('total_speak_duration', 0)
                    self.pause_count = state.get('pause_count', 0)
                    self.speak_count = state.get('speak_count', 0)
                    logger.info(f"Loaded pause state: {self.pause_count} pauses, {self.speak_count} speaks")
        except Exception as e:
            logger.warning(f"Could not load pause state: {e}")
    
    def _save_state(self):
        """Save persistent pause state"""
        try:
            state = {
                'total_pause_duration': self.total_pause_duration,
                'total_speak_duration': self.total_speak_duration,
                'pause_count': self.pause_count,
                'speak_count': self.speak_count,
                'last_saved': datetime.now().isoformat()
            }
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Could not save pause state: {e}")
    
    def request_pause(self, reason: str = "JARVIS speaking"):
        """Request voice listening pause"""
        if self.is_paused:
            logger.warning("Already paused")
            return False
        
        self.is_paused = True
        self.pause_start_time = datetime.now()
        self.pause_count += 1
        
        logger.info(f"🤐 Voice paused: {reason}")
        
        if self.on_pause_requested:
            self.on_pause_requested(reason)
        
        self._save_state()
        return True
    
    def request_resume(self, reason: str = "Response complete"):
        """Request voice listening resume"""
        if not self.is_paused:
            logger.warning("Not paused - cannot resume")
            return False
        
        if self.pause_start_time:
            pause_duration = (datetime.now() - self.pause_start_time).total_seconds()
            self.total_pause_duration += pause_duration
            logger.info(f"🎤 Voice resumed: {reason} (Paused for {pause_duration:.2f}s)")
        else:
            logger.info(f"🎤 Voice resumed: {reason}")
        
        self.is_paused = False
        self.pause_start_time = None
        
        if self.on_resume_requested:
            self.on_resume_requested(reason)
        
        self._save_state()
        return True
    
    def speaking_started(self, text: str = ""):
        """Called when JARVIS starts speaking"""
        self.is_speaking = True
        self.speak_start_time = datetime.now()
        self.speak_count += 1
        
        # Request pause to prevent overlap
        self.request_pause(f"JARVIS speaking: {text[:50]}")
        
        logger.info(f"🔊 Speaking started: {text[:50]}")
        
        if self.on_speaking_started:
            self.on_speaking_started(text)
        
        self._save_state()
    
    def speaking_ended(self, auto_resume: bool = True):
        """Called when JARVIS finishes speaking"""
        self.is_speaking = False
        
        if self.speak_start_time:
            speak_duration = (datetime.now() - self.speak_start_time).total_seconds()
            self.total_speak_duration += speak_duration
            logger.info(f"🔇 Speaking ended (Duration: {speak_duration:.2f}s)")
        else:
            logger.info("🔇 Speaking ended")
        
        if self.on_speaking_ended:
            self.on_speaking_ended()
        
        # Auto-resume listening with small delay
        if auto_resume:
            self._schedule_resume()
        
        self._save_state()
    
    def _schedule_resume(self, delay_ms: int = 500):
        """Schedule resume after small delay"""
        def resume():
            time.sleep(delay_ms / 1000.0)
            self.request_resume("Speech complete - resuming listen")
        
        thread = threading.Thread(target=resume, daemon=True)
        thread.start()
    
    def get_pause_status(self) -> dict:
        """Get current pause status"""
        return {
            'is_paused': self.is_paused,
            'is_speaking': self.is_speaking,
            'pause_start': self.pause_start_time.isoformat() if self.pause_start_time else None,
            'speak_start': self.speak_start_time.isoformat() if self.speak_start_time else None,
            'total_pause_duration': round(self.total_pause_duration, 2),
            'total_speak_duration': round(self.total_speak_duration, 2),
            'pause_count': self.pause_count,
            'speak_count': self.speak_count,
            'pause_percentage': round(
                (self.total_pause_duration / (self.total_pause_duration + self.total_speak_duration) * 100)
                if (self.total_pause_duration + self.total_speak_duration) > 0 else 0,
                2
            )
        }
    
    def force_resume(self):
        """Force resume listening immediately"""
        if self.is_paused:
            logger.warning("Force resume - may interrupt speaking")
            self.request_resume("Force resumed")
            return True
        return False
    
    def reset_stats(self):
        """Reset pause/speak statistics"""
        self.total_pause_duration = 0
        self.total_speak_duration = 0
        self.pause_count = 0
        self.speak_count = 0
        logger.info("Pause/speak statistics reset")
        self._save_state()


# Global instance
_pause_manager: Optional[VoicePauseManager] = None

def get_pause_manager() -> VoicePauseManager:
    """Get or create global pause manager"""
    global _pause_manager
    if _pause_manager is None:
        _pause_manager = VoicePauseManager()
    return _pause_manager

def init_pause_manager():
    """Initialize global pause manager"""
    global _pause_manager
    _pause_manager = VoicePauseManager()
    return _pause_manager
