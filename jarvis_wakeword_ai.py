"""
JARVIS Wake-Word AI System
Auto-activates listening when page loads, waits for "Hey JARVIS" wake-word,
then processes subsequent voice commands.
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path


class WakeWordAI:
    """Intelligent wake-word detection with auto-activation."""
    
    def __init__(self):
        self.state = {
            "listening": False,
            "activated": False,
            "wakeword": "hey jarvis",
            "last_activation": None,
            "command_queue": [],
            "confidence_threshold": 0.6
        }
        self._lock = threading.Lock()
    
    def is_wake_word(self, transcript: str) -> bool:
        """Check if transcript contains wake word."""
        text = str(transcript or "").lower().strip()
        wakeword = self.state["wakeword"].lower()
        return wakeword in text
    
    def process_transcript(self, transcript: str, confidence: float = 1.0) -> dict:
        """
        Process voice transcript for wake-word or command.
        Returns: {activated: bool, is_command: bool, command_text: str, confidence: float}
        """
        with self._lock:
            text = str(transcript or "").lower().strip()
            wakeword = self.state["wakeword"].lower()
            
            # Check if wake-word detected
            if wakeword in text:
                self.state["listening"] = True
                self.state["activated"] = True
                self.state["last_activation"] = datetime.now(timezone.utc).isoformat()
                
                # Extract command after wake-word
                command_text = text.replace(wakeword, "").strip()
                
                return {
                    "activated": True,
                    "is_command": bool(command_text),
                    "command_text": command_text,
                    "confidence": confidence,
                    "message": "Wake-word recognized! Listening for command..." if not command_text else f"Command received: {command_text}"
                }
            
            # If already activated, treat as command
            if self.state["activated"]:
                return {
                    "activated": True,
                    "is_command": True,
                    "command_text": text,
                    "confidence": confidence,
                    "message": f"Processing command: {text}"
                }
            
            # Not activated yet
            return {
                "activated": False,
                "is_command": False,
                "command_text": text,
                "confidence": confidence,
                "message": f"Listening for wake-word... (heard: {text[:30]})"
            }
    
    def reset_activation(self):
        """Reset activation for next wake-word cycle."""
        with self._lock:
            self.state["activated"] = False
            self.state["listening"] = False
            self.state["command_queue"] = []
    
    def get_status(self) -> dict:
        """Get current wake-word system status."""
        with self._lock:
            return {
                "listening": self.state["listening"],
                "activated": self.state["activated"],
                "wakeword": self.state["wakeword"],
                "last_activation": self.state["last_activation"],
                "confidence_threshold": self.state["confidence_threshold"]
            }


# Global instance
wake_word_ai = WakeWordAI()


def initialize_wakeword():
    """Initialize wake-word system."""
    return wake_word_ai


def process_wake_word(transcript: str, confidence: float = 1.0) -> dict:
    """Process transcript for wake-word detection."""
    return wake_word_ai.process_transcript(transcript, confidence)


def get_wakeword_status() -> dict:
    """Get wake-word status."""
    return wake_word_ai.get_status()


def reset_wakeword():
    """Reset wake-word activation."""
    wake_word_ai.reset_activation()
    return {"status": "reset", "message": "Wake-word system reset. Say 'Hey JARVIS' to activate."}
