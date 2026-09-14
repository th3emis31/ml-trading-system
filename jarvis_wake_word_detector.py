"""
JARVIS Wake Word Detection - Phase D
Listen for "Hey Jarvis" wake word activation
"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# State persistence
STATE_FILE = Path('data/wake_word_state.json')
STATE_FILE.parent.mkdir(exist_ok=True)


class WakeWordDetector:
    """Manages wake word detection and background listening"""
    
    def __init__(self):
        self.state = self._load_state()
        self.lock = threading.Lock()
        self.detection_count = 0
        self.last_detection = None
        self.is_listening = False
        
    def _load_state(self):
        """Load wake word state from file"""
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load state: {e}")
        
        return {
            'enabled': True,
            'wake_words': ['hey jarvis', 'jarvis'],
            'sensitivity': 0.8,
            'background_listening': True,
            'detection_count': 0,
            'last_detection': None,
            'detection_history': [],
        }
    
    def _save_state(self):
        """Save wake word state to file"""
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(self.state, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Could not save state: {e}")
    
    def enable(self):
        """Enable wake word detection"""
        with self.lock:
            self.state['enabled'] = True
            self.is_listening = True
            self._save_state()
            logger.info("Wake word detection enabled")
            return True
    
    def disable(self):
        """Disable wake word detection"""
        with self.lock:
            self.state['enabled'] = False
            self.is_listening = False
            self._save_state()
            logger.info("Wake word detection disabled")
            return True
    
    def set_sensitivity(self, sensitivity):
        """Set detection sensitivity (0.5 - 1.0)"""
        with self.lock:
            if sensitivity < 0.5 or sensitivity > 1.0:
                raise ValueError("Sensitivity must be between 0.5 and 1.0")
            
            self.state['sensitivity'] = sensitivity
            self._save_state()
            logger.info(f"Wake word sensitivity set to {sensitivity}")
            return True
    
    def add_wake_word(self, word):
        """Add new wake word"""
        with self.lock:
            word = word.lower().strip()
            if word not in self.state['wake_words']:
                self.state['wake_words'].append(word)
                self._save_state()
                logger.info(f"Wake word added: {word}")
            return True
    
    def remove_wake_word(self, word):
        """Remove wake word"""
        with self.lock:
            word = word.lower().strip()
            if word in self.state['wake_words']:
                self.state['wake_words'].remove(word)
                self._save_state()
                logger.info(f"Wake word removed: {word}")
            return True
    
    def record_detection(self, wake_word, confidence):
        """Record wake word detection"""
        with self.lock:
            self.detection_count += 1
            self.last_detection = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'wake_word': wake_word,
                'confidence': float(confidence),
            }
            
            self.state['detection_count'] = self.detection_count
            self.state['last_detection'] = self.last_detection
            
            # Keep last 20 detections in history
            self.state['detection_history'].append(self.last_detection)
            if len(self.state['detection_history']) > 20:
                self.state['detection_history'] = self.state['detection_history'][-20:]
            
            self._save_state()
            logger.info(f"Wake word detected: {wake_word} (confidence: {confidence:.2%})")
            
            return self.last_detection
    
    def get_status(self):
        """Get wake word detector status"""
        with self.lock:
            return {
                'enabled': self.state.get('enabled', True),
                'is_listening': self.is_listening,
                'wake_words': self.state.get('wake_words', []),
                'sensitivity': self.state.get('sensitivity', 0.8),
                'background_listening': self.state.get('background_listening', True),
                'detection_count': self.state.get('detection_count', 0),
                'last_detection': self.state.get('last_detection'),
            }
    
    def get_detection_history(self):
        """Get detection history"""
        with self.lock:
            return self.state.get('detection_history', [])
    
    def set_background_listening(self, enabled):
        """Enable/disable background listening via Service Worker"""
        with self.lock:
            self.state['background_listening'] = enabled
            self._save_state()
            logger.info(f"Background listening {'enabled' if enabled else 'disabled'}")
            return True
    
    def reset_stats(self):
        """Reset detection statistics"""
        with self.lock:
            self.state['detection_count'] = 0
            self.state['detection_history'] = []
            self.state['last_detection'] = None
            self.detection_count = 0
            self.last_detection = None
            self._save_state()
            logger.info("Wake word statistics reset")
    
    def get_activation_keywords(self):
        """Get activation keywords for frontend"""
        with self.lock:
            return self.state.get('wake_words', ['hey jarvis'])
    
    def validate_detection(self, text, confidence):
        """Validate if text is a valid wake word detection"""
        try:
            text = text.lower().strip()
            sensitivity = self.state.get('sensitivity', 0.8)
            
            # Check against wake words
            for wake_word in self.state.get('wake_words', []):
                if wake_word in text or text in wake_word:
                    # Check confidence meets threshold
                    if float(confidence) >= sensitivity:
                        return True, wake_word
            
            return False, None
        
        except Exception as e:
            logger.error(f"Validation error: {e}")
            return False, None


# Global detector instance
_detector_instance = None
_detector_lock = threading.Lock()


def get_wake_word_detector():
    """Get or create wake word detector instance"""
    global _detector_instance
    
    if _detector_instance is None:
        with _detector_lock:
            if _detector_instance is None:
                _detector_instance = WakeWordDetector()
    
    return _detector_instance


# Convenience functions
def enable_wake_word_detection():
    """Enable wake word detection"""
    return get_wake_word_detector().enable()


def disable_wake_word_detection():
    """Disable wake word detection"""
    return get_wake_word_detector().disable()


def record_wake_word_detection(wake_word, confidence):
    """Record a wake word detection"""
    return get_wake_word_detector().record_detection(wake_word, confidence)


def get_detector_status():
    """Get detector status"""
    return get_wake_word_detector().get_status()
