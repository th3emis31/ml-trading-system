"""
JARVIS Voice Response System
Handles text-to-speech and voice feedback
Enhanced with voice pause/resume support
"""
import json
import threading
import logging
from pathlib import Path
from queue import Queue
from typing import Optional

logger = logging.getLogger("jarvis.voice_response")

# Try to import pyttsx3, but continue if it fails
try:
    import pyttsx3
    PYTTSX3_AVAILABLE = True
except:
    PYTTSX3_AVAILABLE = False
    logger.warning("pyttsx3 not available - voice output disabled")

# Try to import pause manager, but continue if it fails
try:
    from jarvis_voice_pause_manager import get_pause_manager
    PAUSE_MANAGER_AVAILABLE = True
except:
    PAUSE_MANAGER_AVAILABLE = False
    logger.warning("Pause manager not available - voice pause/resume disabled")

# ---------------------------------------------------------------- who speaks
# Three things can speak on this machine: this PC engine, the browser's own speech synthesis, and (historically) each
# page's own utterance. When more than one does, the owner hears two voices - a woman and a man - reading the same
# words. One setting decides, and every speaker on both sides checks it.
SPEAKER_CONFIG_PATH = Path(__file__).resolve().parent / "data" / "voice_config.json"
DEFAULT_SPEAKER = "pc"          # the PC voice always plays; a browser stays silent until the page is clicked


def speaker_choice() -> str:
    """'pc' (this engine speaks) or 'browser' (the page speaks and this engine stays quiet)."""
    try:
        choice = json.loads(SPEAKER_CONFIG_PATH.read_text(encoding="utf-8")).get("speaker")
    except (OSError, ValueError, AttributeError):
        return DEFAULT_SPEAKER
    return choice if choice in ("pc", "browser") else DEFAULT_SPEAKER


def set_speaker_choice(choice: str) -> dict:
    """Record which side speaks. Anything else is refused rather than silently ignored."""
    if choice not in ("pc", "browser"):
        raise ValueError(f"speaker must be 'pc' or 'browser', not {choice!r}")
    SPEAKER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    SPEAKER_CONFIG_PATH.write_text(json.dumps({"speaker": choice}, indent=1), encoding="utf-8")
    return {"speaker": choice}


class VoiceResponseEngine:
    def __init__(self):
        """Initialize text-to-speech engine"""
        self.engine = None
        self.queue = Queue()
        self.speaking = False
        
        if PYTTSX3_AVAILABLE:
            try:
                self.engine = pyttsx3.init()
                self.engine.setProperty('rate', 150)
                self.engine.setProperty('volume', 0.9)
                try:
                    voices = self.engine.getProperty('voices')
                    if len(voices) > 1:
                        self.engine.setProperty('voice', voices[1].id)
                except:
                    pass
            except Exception as e:
                logger.warning(f"Could not initialize pyttsx3: {e}")
                self.engine = None
        
        # Start background thread
        self.processor_thread = threading.Thread(target=self._speech_processor, daemon=True)
        self.processor_thread.start()
        
        logger.info("Voice Response Engine initialized")
    
    def _speech_processor(self):
        """Background thread that processes speech queue"""
        while True:
            try:
                text = self.queue.get()
                if text and self.engine:
                    # Notify pause manager speech is starting
                    if PAUSE_MANAGER_AVAILABLE:
                        pause_manager = get_pause_manager()
                        pause_manager.speaking_started(text)
                    
                    self.speaking = True
                    try:
                        self.engine.say(text)
                        self.engine.runAndWait()
                    except:
                        pass
                    self.speaking = False
                    
                    # Notify pause manager speech has ended
                    if PAUSE_MANAGER_AVAILABLE:
                        pause_manager = get_pause_manager()
                        pause_manager.speaking_ended(auto_resume=True)
                    
            except Exception as e:
                logger.error(f"Speech processor error: {e}")
                self.speaking = False
    
    def speak(self, text: str, wait: bool = False, pause_listening: bool = True):
        """Speak text asynchronously
        
        Args:
            text: Text to speak
            wait: Wait for speech to complete
            pause_listening: Pause voice listening while speaking (default True)
        """
        if not text or not self.engine:
            return
        if speaker_choice() != "pc":
            logger.debug("PC voice is off (the browser speaks); not saying: %s", str(text)[:40])
            return
        
        try:
            cleaned = str(text).strip()
            if len(cleaned) > 500:
                cleaned = cleaned[:500] + "..."
            
            # Request pause before speaking (if pause manager available)
            if pause_listening and PAUSE_MANAGER_AVAILABLE:
                pause_manager = get_pause_manager()
                pause_manager.request_pause(f"Speaking: {cleaned[:30]}")
            
            self.queue.put(cleaned)
            
            if wait:
                while self.speaking:
                    threading.Event().wait(0.1)
        except Exception as e:
            logger.error(f"Error queuing speech: {e}")
    
    def acknowledge(self):
        """Acknowledge command received"""
        self.speak("Understood", wait=False)
    
    def confirm(self):
        """Confirm action completed"""
        self.speak("Task complete", wait=False)
    
    def error(self, message: str = "Error occurred"):
        """Report error"""
        self.speak(f"Error: {message}", wait=False)
    
    def read_price(self, symbol: str, price: float, change: float):
        """Read price information"""
        direction = "up" if change >= 0 else "down"
        text = f"{symbol} is at {price:.2f}, {direction} {abs(change):.2f} percent"
        self.speak(text, wait=False)
    
    def read_signal(self, symbol: str, signal: str, confidence: float):
        """Read trading signal"""
        text = f"{symbol}: {signal} signal with {int(confidence*100)} percent confidence"
        self.speak(text, wait=False)
    
    def stop(self):
        """Stop speaking"""
        try:
            if self.engine:
                self.engine.stop()
            self.speaking = False
        except:
            pass

# Global voice engine
voice_engine = None

def get_voice_engine():
    """Get or create voice response engine"""
    global voice_engine
    if voice_engine is None:
        voice_engine = VoiceResponseEngine()
    return voice_engine
