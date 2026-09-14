#!/usr/bin/env python3
"""
JARVIS Voice Enhancement Module
Safely adds improvements to voice recognition and processing
WITHOUT modifying existing code - completely additive
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(log_dir / "jarvis_voice_enhancement.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("jarvis.voice.enhancement")


class VoiceQualityAnalyzer:
    """Analyzes voice quality and provides feedback"""
    
    def __init__(self):
        self.quality_history = []
        self.quality_threshold = 0.7
        
    def analyze_quality(self, audio_data: Dict) -> Dict:
        """
        Analyze audio quality metrics
        Returns: quality score, suggestions, issues
        """
        quality_score = 0.0
        issues = []
        suggestions = []
        metrics = {}
        
        # Check confidence level
        confidence = audio_data.get('confidence', 0)
        metrics['confidence'] = confidence
        if confidence < 0.5:
            issues.append("Low confidence - speak clearly")
            suggestions.append("Increase microphone volume or speak louder")
        elif confidence < 0.7:
            suggestions.append("Voice confidence could be improved")
        
        quality_score += min(confidence, 1.0) * 30  # 30 points max
        
        # Check for background noise
        noise_level = audio_data.get('noise_level', 0)
        metrics['noise_level'] = noise_level
        if noise_level > 0.5:
            issues.append("High background noise detected")
            suggestions.append("Move to a quieter location")
        
        quality_score += (1.0 - min(noise_level, 1.0)) * 20  # 20 points max
        
        # Check audio duration
        duration = audio_data.get('duration', 0)
        metrics['duration'] = duration
        if duration < 0.5:
            issues.append("Audio too short")
            suggestions.append("Speak a complete sentence or command")
        elif duration > 30:
            issues.append("Audio too long")
            suggestions.append("Keep commands concise")
        else:
            quality_score += 20  # Good duration
        
        # Check clarity/articulation
        clarity = audio_data.get('clarity', 0.8)
        metrics['clarity'] = clarity
        quality_score += clarity * 30  # 30 points max
        
        # Store history
        self.quality_history.append({
            'timestamp': datetime.now().isoformat(),
            'score': quality_score,
            'metrics': metrics
        })
        
        return {
            'quality_score': min(quality_score, 100),
            'quality_level': self._get_quality_level(quality_score),
            'issues': issues,
            'suggestions': suggestions,
            'metrics': metrics
        }
    
    def _get_quality_level(self, score: float) -> str:
        """Return quality level description"""
        if score >= 85:
            return "Excellent"
        elif score >= 70:
            return "Good"
        elif score >= 50:
            return "Fair"
        else:
            return "Poor"
    
    def get_average_quality(self, last_n: int = 10) -> float:
        """Get average quality from last N recordings"""
        if not self.quality_history:
            return 0.0
        
        recent = self.quality_history[-last_n:]
        return sum(q['score'] for q in recent) / len(recent)


class VoiceCommandParser:
    """Enhanced command parsing with better error handling"""
    
    def __init__(self):
        self.command_aliases = {
            "trade": ["open trade", "start trade", "new trade", "execute trade"],
            "stop": ["end trade", "close trade", "exit trade", "stop trading"],
            "status": ["what's happening", "current status", "system status", "show status"],
            "analysis": ["analyze", "market analysis", "technical analysis"],
            "risk": ["risk level", "set risk", "adjust risk", "risk management"],
            "voice": ["voice settings", "configure voice", "voice options"],
        }
        
        self.confidence_boost = {}
        self.error_recovery_enabled = True
        
    def parse_command(self, transcript: str, confidence: float) -> Dict:
        """
        Parse voice command with improved error recovery
        """
        original_transcript = transcript
        transcript_lower = transcript.lower().strip()
        
        # Remove filler words
        fillers = ["um", "uh", "like", "you know", "so", "basically"]
        for filler in fillers:
            transcript_lower = transcript_lower.replace(f" {filler} ", " ").replace(f"{filler} ", "")
        
        result = {
            'original': original_transcript,
            'cleaned': transcript_lower,
            'command': None,
            'parameters': {},
            'confidence_adjusted': confidence,
            'error_corrections': []
        }
        
        # Try to match commands
        best_match = self._find_best_match(transcript_lower)
        
        if best_match:
            result['command'] = best_match['command']
            result['parameters'] = best_match.get('parameters', {})
            result['confidence_adjusted'] = confidence * 1.1  # Slight boost for matched
        else:
            # Try typo correction if enabled
            if self.error_recovery_enabled:
                corrected, corrections = self._attempt_correction(transcript_lower)
                if corrected:
                    result['cleaned'] = corrected
                    result['error_corrections'] = corrections
                    best_match = self._find_best_match(corrected)
                    if best_match:
                        result['command'] = best_match['command']
        
        return result
    
    def _find_best_match(self, transcript: str) -> Optional[Dict]:
        """Find matching command"""
        for main_cmd, aliases in self.command_aliases.items():
            if transcript == main_cmd or any(alias in transcript for alias in aliases):
                return {'command': main_cmd}
        return None
    
    def _attempt_correction(self, transcript: str) -> Tuple[Optional[str], List[str]]:
        """Attempt to correct common mistakes"""
        corrections = []
        corrected = transcript
        
        # Common voice recognition errors
        replacements = {
            "trade": ["tray", "try", "dry"],
            "status": ["statues", "status", "stat"],
            "risk": ["risk", "disk"],
        }
        
        for correct, errors in replacements.items():
            for error in errors:
                if error in corrected:
                    corrected = corrected.replace(error, correct)
                    corrections.append(f"Corrected '{error}' → '{correct}'")
        
        if corrections:
            return corrected, corrections
        return None, []


class VoiceResponseQualityController:
    """Ensures quality responses to voice commands"""
    
    def __init__(self):
        self.response_cache = {}
        self.performance_metrics = []
        
    def generate_response(self, command: str, status: Dict, quality_score: float) -> Dict:
        """
        Generate contextually appropriate response
        """
        response = {
            'command': command,
            'message': "",
            'visual_indicator': "",
            'follow_up': None,
            'timestamp': datetime.now().isoformat()
        }
        
        # Adjust response based on quality
        if quality_score < 50:
            response['message'] = f"I didn't quite catch that. Could you repeat?"
            response['visual_indicator'] = "⚠️ Low confidence - asking for repeat"
            response['follow_up'] = "listening"
        elif quality_score < 70:
            response['message'] = f"I heard '{command}' but I'm not entirely sure. Proceed?"
            response['visual_indicator'] = "🤔 Clarification needed"
        else:
            response['message'] = f"Executing: {command}"
            response['visual_indicator'] = "✅ Command accepted"
        
        return response
    
    def log_performance(self, response_data: Dict, execution_time: float):
        """Track response performance"""
        self.performance_metrics.append({
            'timestamp': datetime.now().isoformat(),
            'command': response_data.get('command'),
            'execution_time_ms': execution_time * 1000,
            'quality': response_data.get('visual_indicator')
        })


class VoiceHistoryTracker:
    """Track voice commands and patterns for improvement"""
    
    def __init__(self, history_file: str = "jarvis_voice_history.json"):
        self.history_file = Path(history_file)
        self.history = self._load_history()
        self.max_history = 1000  # Keep last 1000 commands
        
    def _load_history(self) -> List[Dict]:
        """Load command history"""
        if self.history_file.exists():
            try:
                with open(self.history_file) as f:
                    return json.load(f)
            except:
                return []
        return []
    
    def save_history(self):
        """Save history to file"""
        # Keep only recent history
        history_to_save = self.history[-self.max_history:]
        
        with open(self.history_file, 'w') as f:
            json.dump(history_to_save, f, indent=2, default=str)
    
    def add_command(self, command: str, transcript: str, success: bool, execution_time: float):
        """Record a voice command"""
        self.history.append({
            'timestamp': datetime.now().isoformat(),
            'command': command,
            'transcript': transcript,
            'success': success,
            'execution_time_ms': execution_time * 1000
        })
        
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]
        
        self.save_history()
    
    def get_command_stats(self) -> Dict:
        """Get statistics about voice commands"""
        if not self.history:
            return {}
        
        total = len(self.history)
        successful = sum(1 for h in self.history if h.get('success'))
        avg_time = sum(h.get('execution_time_ms', 0) for h in self.history) / total
        
        # Most common commands
        commands = {}
        for h in self.history:
            cmd = h.get('command', 'unknown')
            commands[cmd] = commands.get(cmd, 0) + 1
        
        return {
            'total_commands': total,
            'successful': successful,
            'success_rate': (successful / total * 100) if total > 0 else 0,
            'average_execution_time_ms': avg_time,
            'most_common_commands': sorted(commands.items(), key=lambda x: x[1], reverse=True)[:5]
        }


class VoiceEnhancementSystem:
    """Main enhancement system - ties everything together"""
    
    def __init__(self):
        self.quality_analyzer = VoiceQualityAnalyzer()
        self.command_parser = VoiceCommandParser()
        self.response_controller = VoiceResponseQualityController()
        self.history_tracker = VoiceHistoryTracker()
        
        logger.info("JARVIS Voice Enhancement System initialized")
    
    def process_voice_input(self, audio_data: Dict) -> Dict:
        """
        Full voice input processing pipeline
        """
        # Analyze quality
        quality_analysis = self.quality_analyzer.analyze_quality(audio_data)
        
        # Parse command
        transcript = audio_data.get('transcript', '')
        confidence = audio_data.get('confidence', 0)
        
        parsed_command = self.command_parser.parse_command(
            transcript,
            confidence
        )
        
        # Generate response
        response = self.response_controller.generate_response(
            parsed_command.get('command', 'unknown'),
            quality_analysis,
            quality_analysis['quality_score']
        )
        
        # Log to history
        self.history_tracker.add_command(
            parsed_command.get('command', 'unknown'),
            transcript,
            quality_analysis['quality_score'] >= 70,
            0.0  # Will be filled in by caller
        )
        
        return {
            'quality': quality_analysis,
            'command': parsed_command,
            'response': response,
            'success': quality_analysis['quality_score'] >= 70
        }
    
    def get_voice_diagnostics(self) -> Dict:
        """Get system diagnostics"""
        return {
            'average_quality': self.quality_analyzer.get_average_quality(),
            'command_stats': self.history_tracker.get_command_stats(),
            'timestamp': datetime.now().isoformat()
        }


if __name__ == "__main__":
    # Test the enhancement system
    system = VoiceEnhancementSystem()
    
    # Simulate voice input
    test_audio = {
        'transcript': 'open a trade on eur usd',
        'confidence': 0.85,
        'noise_level': 0.2,
        'duration': 2.5,
        'clarity': 0.9
    }
    
    result = system.process_voice_input(test_audio)
    logger.info(f"Test result: {result}")
    logger.info(f"Diagnostics: {system.get_voice_diagnostics()}")
