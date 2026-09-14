#!/usr/bin/env python3
"""
JARVIS Auto-Voice Listener
- Automatically listens for voice commands
- Auto-restarts when recognition ends
- Manages listening state and reconnection logic
- NO DELETIONS - Safe additive enhancement
"""

import logging
import json
from datetime import datetime
from typing import Dict, Optional, Callable
from pathlib import Path
import threading
import time

logger = logging.getLogger("jarvis.auto_voice_listener")

class AutoVoiceListener:
    """
    Manages automatic voice listening without user intervention
    - Auto-starts on initialization
    - Auto-restarts after each recognition session
    - Tracks listening state persistently
    - Handles reconnection with exponential backoff
    """
    
    def __init__(self, state_file: str = "data/voice_auto_state.json"):
        """Initialize auto voice listener"""
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(exist_ok=True)
        
        self.listening_enabled = True
        self.is_currently_listening = False
        self.auto_restart_enabled = True
        self.restart_attempts = 0
        self.max_restart_attempts = 5
        self.reconnect_delay = 1.0  # Start with 1 second
        self.max_reconnect_delay = 30.0  # Max 30 seconds
        
        self.listening_start_time = None
        self.last_command_time = None
        self.total_listening_sessions = 0
        self.total_commands_recognized = 0
        
        self.on_listening_start: Optional[Callable] = None
        self.on_listening_stop: Optional[Callable] = None
        self.on_auto_restart: Optional[Callable] = None
        self.on_reconnect_failed: Optional[Callable] = None
        
        self._load_state()
        logger.info("Auto Voice Listener initialized")
    
    def _load_state(self):
        """Load persistent listening state"""
        try:
            if self.state_file.exists():
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                    self.listening_enabled = state.get('listening_enabled', True)
                    self.auto_restart_enabled = state.get('auto_restart_enabled', True)
                    self.total_listening_sessions = state.get('total_listening_sessions', 0)
                    self.total_commands_recognized = state.get('total_commands_recognized', 0)
                    logger.info(f"Loaded state: {self.total_listening_sessions} sessions, {self.total_commands_recognized} commands")
        except Exception as e:
            logger.warning(f"Could not load state: {e}")
    
    def _save_state(self):
        """Save persistent listening state"""
        try:
            state = {
                'listening_enabled': self.listening_enabled,
                'auto_restart_enabled': self.auto_restart_enabled,
                'total_listening_sessions': self.total_listening_sessions,
                'total_commands_recognized': self.total_commands_recognized,
                'last_saved': datetime.now().isoformat()
            }
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Could not save state: {e}")
    
    def on_recognition_start(self):
        """Called when voice recognition starts"""
        self.is_currently_listening = True
        self.listening_start_time = datetime.now()
        self.total_listening_sessions += 1
        self.restart_attempts = 0  # Reset on successful start
        self.reconnect_delay = 1.0  # Reset reconnection delay
        
        logger.info(f"🎤 Listening started (Session #{self.total_listening_sessions})")
        
        if self.on_listening_start:
            self.on_listening_start()
        
        self._save_state()
    
    def on_recognition_end(self):
        """Called when voice recognition ends naturally"""
        self.is_currently_listening = False
        
        if self.listening_start_time:
            duration = (datetime.now() - self.listening_start_time).total_seconds()
            logger.info(f"⏹️ Listening ended (Duration: {duration:.1f}s)")
        
        if self.on_listening_stop:
            self.on_listening_stop()
        
        # Trigger auto-restart if enabled
        if self.listening_enabled and self.auto_restart_enabled:
            self._schedule_auto_restart()
    
    def on_command_recognized(self):
        """Called when a voice command is recognized"""
        self.total_commands_recognized += 1
        self.last_command_time = datetime.now()
        logger.info(f"✓ Command recognized (Total: {self.total_commands_recognized})")
        self._save_state()
    
    def _schedule_auto_restart(self):
        """Schedule automatic restart of listening"""
        if not self.listening_enabled or not self.auto_restart_enabled:
            return
        
        if self.restart_attempts >= self.max_restart_attempts:
            logger.error(f"Max restart attempts ({self.max_restart_attempts}) reached")
            if self.on_reconnect_failed:
                self.on_reconnect_failed()
            return
        
        # Exponential backoff
        delay = min(self.reconnect_delay, self.max_reconnect_delay)
        self.restart_attempts += 1
        
        logger.info(f"⟳ Scheduling auto-restart in {delay:.1f}s (Attempt {self.restart_attempts}/{self.max_restart_attempts})")
        
        def restart_listening():
            time.sleep(delay)
            if self.listening_enabled and self.auto_restart_enabled:
                logger.info("🔄 Attempting auto-restart...")
                if self.on_auto_restart:
                    self.on_auto_restart()
        
        # Run in background thread
        thread = threading.Thread(target=restart_listening, daemon=True)
        thread.start()
        
        # Increase delay for next attempt (exponential backoff)
        self.reconnect_delay = min(delay * 1.5, self.max_reconnect_delay)
    
    def enable_listening(self):
        """Enable automatic voice listening"""
        self.listening_enabled = True
        logger.info("✓ Auto-listening ENABLED")
        self._save_state()
    
    def disable_listening(self):
        """Disable automatic voice listening"""
        self.listening_enabled = False
        self.is_currently_listening = False
        logger.info("✗ Auto-listening DISABLED")
        self._save_state()
    
    def toggle_auto_restart(self):
        """Toggle auto-restart feature"""
        self.auto_restart_enabled = not self.auto_restart_enabled
        state = "ENABLED" if self.auto_restart_enabled else "DISABLED"
        logger.info(f"⟳ Auto-restart {state}")
        self._save_state()
    
    def get_status(self) -> Dict:
        """Get current listening status"""
        return {
            'listening_enabled': self.listening_enabled,
            'is_currently_listening': self.is_currently_listening,
            'auto_restart_enabled': self.auto_restart_enabled,
            'restart_attempts': self.restart_attempts,
            'total_sessions': self.total_listening_sessions,
            'total_commands': self.total_commands_recognized,
            'listening_started': self.listening_start_time.isoformat() if self.listening_start_time else None,
            'last_command': self.last_command_time.isoformat() if self.last_command_time else None
        }
    
    def reset_stats(self):
        """Reset listening statistics"""
        self.total_listening_sessions = 0
        self.total_commands_recognized = 0
        self.last_command_time = None
        self.restart_attempts = 0
        logger.info("🔄 Statistics reset")
        self._save_state()


# Global instance
_auto_listener: Optional[AutoVoiceListener] = None

def get_auto_listener() -> AutoVoiceListener:
    """Get or create global auto voice listener"""
    global _auto_listener
    if _auto_listener is None:
        _auto_listener = AutoVoiceListener()
    return _auto_listener

def init_auto_listener():
    """Initialize global auto voice listener"""
    global _auto_listener
    _auto_listener = AutoVoiceListener()
    return _auto_listener
