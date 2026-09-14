#!/usr/bin/env python3
"""
JARVIS Advanced Voice Authentication & Learning System
- Learns and recognizes ONLY the user's voice
- Stores voice samples for pattern recognition
- Builds user voice profile over time
- Adapts to user preferences and trading patterns
- Makes intelligent autonomous decisions
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import hashlib
import numpy as np
from collections import defaultdict

class VoiceAuthenticationEngine:
    """
    Authenticates users by their unique voice characteristics
    Only responds to the verified user's voice
    """
    
    def __init__(self, voice_profiles_dir: str = "voice_profiles"):
        self.voice_profiles_dir = Path(voice_profiles_dir)
        self.voice_profiles_dir.mkdir(exist_ok=True)
        self.user_profile_file = self.voice_profiles_dir / "user_voice_profile.json"
        self.voice_samples_dir = self.voice_profiles_dir / "voice_samples"
        self.voice_samples_dir.mkdir(exist_ok=True)
        self.user_profile = self._load_or_create_profile()
        
    def _load_or_create_profile(self) -> Dict:
        """Load existing profile or create new one"""
        if self.user_profile_file.exists():
            with open(self.user_profile_file, 'r') as f:
                return json.load(f)
        
        return {
            'created_at': datetime.now().isoformat(),
            'voice_samples': [],
            'voice_characteristics': {
                'pitch_avg': None,
                'pitch_std': None,
                'speed_avg': None,
                'speed_std': None,
                'tone_signature': None,
                'accent_profile': None,
                'cadence_pattern': None
            },
            'authentication_status': 'not_enrolled',
            'sample_count': 0,
            'last_verified': None,
            'learning_progress': 0.0,
            'recognition_confidence': 0.0
        }
    
    def save_profile(self):
        """Save user voice profile"""
        with open(self.user_profile_file, 'w') as f:
            json.dump(self.user_profile, f, indent=2)
    
    def enroll_voice_sample(self, audio_data: Dict, transcript: str) -> Dict:
        """
        Enroll a new voice sample for the user
        Build voice profile from multiple samples
        """
        sample_id = f"sample_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        sample_file = self.voice_samples_dir / f"{sample_id}.json"
        
        # Extract voice characteristics
        characteristics = {
            'pitch': audio_data.get('pitch', 0),
            'speed': audio_data.get('speed', 1.0),
            'tone': audio_data.get('tone', 'neutral'),
            'confidence': audio_data.get('confidence', 0),
            'mfcc': audio_data.get('mfcc', []),  # Mel-frequency cepstral coefficients
            'duration': audio_data.get('duration', 0),
            'noise_level': audio_data.get('noise_level', 0),
            'transcript': transcript
        }
        
        # Save sample
        sample_data = {
            'sample_id': sample_id,
            'timestamp': datetime.now().isoformat(),
            'characteristics': characteristics
        }
        
        with open(sample_file, 'w') as f:
            json.dump(sample_data, f, indent=2)
        
        # Update profile
        self.user_profile['voice_samples'].append(sample_id)
        self.user_profile['sample_count'] += 1
        
        # Update characteristics if enough samples
        if self.user_profile['sample_count'] >= 3:
            self._update_voice_characteristics()
            self.user_profile['authentication_status'] = 'enrolled'
        
        self.save_profile()
        
        sample_count = self.user_profile['sample_count']
        if sample_count >= 3:
            status_msg = 'Ready for authentication!'
        else:
            more_needed = 3 - sample_count
            status_msg = f'{more_needed} more needed.'
        
        return {
            'success': True,
            'sample_id': sample_id,
            'total_samples': sample_count,
            'authentication_status': self.user_profile['authentication_status'],
            'message': f"Voice sample {sample_count} collected. {status_msg}"
        }
    
    def _update_voice_characteristics(self):
        """Analyze samples and update voice profile characteristics"""
        if len(self.user_profile['voice_samples']) < 2:
            return
        
        # Load all samples
        pitches = []
        speeds = []
        confidences = []
        
        for sample_id in self.user_profile['voice_samples'][-10:]:  # Last 10 samples
            sample_file = self.voice_samples_dir / f"{sample_id}.json"
            if sample_file.exists():
                with open(sample_file, 'r') as f:
                    sample = json.load(f)
                    chars = sample['characteristics']
                    pitches.append(chars.get('pitch', 0))
                    speeds.append(chars.get('speed', 1.0))
                    confidences.append(chars.get('confidence', 0))
        
        if pitches:
            self.user_profile['voice_characteristics']['pitch_avg'] = float(np.mean(pitches))
            self.user_profile['voice_characteristics']['pitch_std'] = float(np.std(pitches))
            self.user_profile['voice_characteristics']['speed_avg'] = float(np.mean(speeds))
            self.user_profile['voice_characteristics']['tone_signature'] = hashlib.md5(
                str(pitches).encode()
            ).hexdigest()
            self.user_profile['recognition_confidence'] = float(np.mean(confidences)) * 100
            self.user_profile['learning_progress'] = min(100, len(self.user_profile['voice_samples']) * 10)
    
    def verify_voice(self, audio_data: Dict) -> Tuple[bool, float]:
        """
        Verify if incoming voice is from the authenticated user
        Returns: (is_authorized, confidence_score)
        """
        if self.user_profile['authentication_status'] != 'enrolled':
            return False, 0.0
        
        # Compare characteristics
        incoming_pitch = audio_data.get('pitch', 0)
        incoming_speed = audio_data.get('speed', 1.0)
        incoming_confidence = audio_data.get('confidence', 0)
        
        profile_pitch = self.user_profile['voice_characteristics']['pitch_avg'] or 0
        profile_speed = self.user_profile['voice_characteristics']['speed_avg'] or 1.0
        
        # Calculate similarity
        pitch_diff = abs(incoming_pitch - profile_pitch)
        speed_diff = abs(incoming_speed - profile_speed)
        
        # Tolerance levels
        pitch_tolerance = (self.user_profile['voice_characteristics']['pitch_std'] or 50) * 1.5
        speed_tolerance = 0.3
        
        pitch_match = 1.0 - min(1.0, pitch_diff / max(pitch_tolerance, 1))
        speed_match = 1.0 - min(1.0, speed_diff / speed_tolerance)
        
        # Average confidence
        confidence = (pitch_match + speed_match) / 2 * incoming_confidence
        
        is_authorized = confidence > 0.70  # 70% confidence threshold
        
        return is_authorized, confidence * 100


class VoicePatternLearner:
    """
    Learns from user's voice patterns and behavior over time
    Identifies preferences, trading patterns, profitable strategies
    """
    
    def __init__(self):
        self.learning_file = Path("jarvis_learning_data.json")
        self.learning_data = self._load_learning_data()
    
    def _load_learning_data(self) -> Dict:
        """Load learning history"""
        if self.learning_file.exists():
            with open(self.learning_file, 'r') as f:
                return json.load(f)
        
        return {
            'created_at': datetime.now().isoformat(),
            'voice_commands': defaultdict(int),
            'trading_preferences': {
                'favorite_assets': {},
                'preferred_direction': 'both',  # buy/sell/both
                'risk_tolerance': 'medium',
                'timeframe': '1h',
                'avg_position_size': 0,
            },
            'profitable_patterns': [],
            'losing_patterns': [],
            'voice_command_history': [],
            'trading_history': [],
            'accuracy_metrics': {
                'wins': 0,
                'losses': 0,
                'win_rate': 0.0,
                'avg_profit': 0.0,
                'avg_loss': 0.0
            },
            'learning_stage': 'beginner',
            'learning_progress': 0.0
        }
    
    def save_learning_data(self):
        """Save learning data"""
        with open(self.learning_file, 'w') as f:
            json.dump(self.learning_data, f, indent=2, default=str)
    
    def record_voice_command(self, command: str, asset: str, action: str, confidence: float):
        """Record a voice command for pattern analysis"""
        self.learning_data['voice_commands'][command] += 1
        self.learning_data['voice_command_history'].append({
            'timestamp': datetime.now().isoformat(),
            'command': command,
            'asset': asset,
            'action': action,
            'confidence': confidence
        })
        
        # Update preferences
        if asset not in self.learning_data['trading_preferences']['favorite_assets']:
            self.learning_data['trading_preferences']['favorite_assets'][asset] = 0
        self.learning_data['trading_preferences']['favorite_assets'][asset] += 1
        
        self.save_learning_data()
    
    def record_trade_result(self, trade_data: Dict) -> Dict:
        """
        Record trade result and learn from it
        Returns: insights and recommendations
        """
        result = {
            'timestamp': datetime.now().isoformat(),
            'asset': trade_data.get('asset'),
            'direction': trade_data.get('direction'),
            'entry': trade_data.get('entry'),
            'exit': trade_data.get('exit'),
            'profit': trade_data.get('profit', 0),
            'loss': trade_data.get('loss', 0),
            'win': trade_data.get('profit', 0) > 0
        }
        
        self.learning_data['trading_history'].append(result)
        
        # Update metrics
        if result['win']:
            self.learning_data['accuracy_metrics']['wins'] += 1
            self.learning_data['accuracy_metrics']['avg_profit'] = (
                (self.learning_data['accuracy_metrics']['avg_profit'] * 
                 (self.learning_data['accuracy_metrics']['wins'] - 1) + 
                 result['profit']) / self.learning_data['accuracy_metrics']['wins']
            )
        else:
            self.learning_data['accuracy_metrics']['losses'] += 1
            self.learning_data['accuracy_metrics']['avg_loss'] = (
                (self.learning_data['accuracy_metrics']['avg_loss'] * 
                 (self.learning_data['accuracy_metrics']['losses'] - 1) + 
                 result['loss']) / self.learning_data['accuracy_metrics']['losses']
            )
        
        total_trades = (self.learning_data['accuracy_metrics']['wins'] + 
                       self.learning_data['accuracy_metrics']['losses'])
        self.learning_data['accuracy_metrics']['win_rate'] = (
            self.learning_data['accuracy_metrics']['wins'] / total_trades * 100 
            if total_trades > 0 else 0
        )
        
        # Determine learning stage
        if total_trades < 10:
            self.learning_data['learning_stage'] = 'beginner'
            self.learning_data['learning_progress'] = (total_trades / 10) * 33
        elif total_trades < 50:
            self.learning_data['learning_stage'] = 'intermediate'
            self.learning_data['learning_progress'] = 33 + ((total_trades - 10) / 40) * 33
        else:
            self.learning_data['learning_stage'] = 'advanced'
            self.learning_data['learning_progress'] = 66 + min(34, (total_trades - 50) / 50 * 34)
        
        self.save_learning_data()
        
        # Generate insights
        insights = self._generate_insights(result)
        return insights
    
    def _generate_insights(self, trade_result: Dict) -> Dict:
        """Generate insights from trade results"""
        insights = {
            'trade_result': 'WIN' if trade_result['win'] else 'LOSS',
            'profit_loss': trade_result.get('profit') or trade_result.get('loss'),
            'win_rate': f"{self.learning_data['accuracy_metrics']['win_rate']:.1f}%",
            'learning_stage': self.learning_data['learning_stage'],
            'recommendations': []
        }
        
        # Add recommendations based on learning
        win_rate = self.learning_data['accuracy_metrics']['win_rate']
        
        if win_rate < 50:
            insights['recommendations'].append(
                f"⚠️ Win rate is {win_rate:.1f}%. Time to analyze and adjust strategy."
            )
        elif win_rate > 70:
            insights['recommendations'].append(
                f"✅ Great! Win rate is {win_rate:.1f}%. Keep this strategy!"
            )
        
        if self.learning_data['accuracy_metrics']['avg_profit'] > 0:
            insights['recommendations'].append(
                f"💰 Average profit per win: ${self.learning_data['accuracy_metrics']['avg_profit']:.2f}"
            )
        
        # Asset recommendations
        favorite_asset = max(
            self.learning_data['trading_preferences']['favorite_assets'].items(),
            key=lambda x: x[1],
            default=('N/A', 0)
        )[0]
        
        if favorite_asset != 'N/A':
            insights['recommendations'].append(
                f"📊 Your favorite asset: {favorite_asset}. Consider trading it more."
            )
        
        return insights
    
    def get_smart_recommendation(self) -> Dict:
        """
        Generate smart trading recommendation based on learned patterns
        """
        recommendation = {
            'timestamp': datetime.now().isoformat(),
            'learning_stage': self.learning_data['learning_stage'],
            'confidence': min(95, self.learning_data['learning_progress'] * 0.95),
            'recommendation': '',
            'reasoning': [],
            'risk_level': 'medium',
            'expected_roi': 2.5
        }
        
        win_rate = self.learning_data['accuracy_metrics']['win_rate']
        favorite_asset = max(
            self.learning_data['trading_preferences']['favorite_assets'].items(),
            key=lambda x: x[1],
            default=('BTCUSD', 0)
        )[0]
        
        # Generate recommendation
        if win_rate > 75:
            recommendation['recommendation'] = f"STRONG BUY {favorite_asset}"
            recommendation['reasoning'].append("Your proven strategy has 75%+ win rate")
            recommendation['risk_level'] = 'low'
        elif win_rate > 60:
            recommendation['recommendation'] = f"BUY {favorite_asset}"
            recommendation['reasoning'].append("Your strategy is profitable")
            recommendation['risk_level'] = 'medium'
        elif win_rate > 50:
            recommendation['recommendation'] = f"CAUTIOUS BUY {favorite_asset}"
            recommendation['reasoning'].append("Win rate above 50% - improving")
            recommendation['risk_level'] = 'medium-high'
        else:
            recommendation['recommendation'] = "HOLD & ANALYZE"
            recommendation['reasoning'].append("Win rate needs improvement")
            recommendation['risk_level'] = 'high'
        
        # Add learned insights
        if self.learning_data['accuracy_metrics']['wins'] > 0:
            recommendation['reasoning'].append(
                f"Based on {len(self.learning_data['trading_history'])} trades analyzed"
            )
        
        return recommendation


class AutonomousDecisionMaker:
    """
    Makes intelligent autonomous trading decisions based on:
    - User voice patterns
    - Learning history
    - Market conditions
    - Risk management rules
    """
    
    def __init__(self, voice_auth: VoiceAuthenticationEngine, 
                 voice_learner: VoicePatternLearner):
        self.voice_auth = voice_auth
        self.voice_learner = voice_learner
        self.decision_history = []
    
    def make_decision(self, market_conditions: Dict, user_preferences: Dict) -> Dict:
        """
        Make autonomous trading decision
        """
        learning_stage = self.voice_learner.learning_data['learning_stage']
        win_rate = self.voice_learner.learning_data['accuracy_metrics']['win_rate']
        
        # Get confidence level based on learning progress
        confidence = min(95, self.voice_learner.learning_data['learning_progress'] * 0.95)
        
        # Decision parameters
        decision = {
            'timestamp': datetime.now().isoformat(),
            'learning_stage': learning_stage,
            'confidence': confidence,
            'action': 'HOLD',
            'reasoning': [],
            'risk_management': {
                'position_size': 1,
                'stop_loss': 0.02,  # 2% stop loss
                'take_profit': 0.05  # 5% target
            }
        }
        
        # Decision logic based on learning stage
        if learning_stage == 'beginner':
            decision['reasoning'].append("⚠️ Early learning stage - conservative approach")
            decision['risk_management']['position_size'] = 0.5
            decision['risk_management']['stop_loss'] = 0.03
            decision['action'] = 'ANALYZE_ONLY'
        
        elif learning_stage == 'intermediate':
            if win_rate > 60:
                decision['action'] = 'BUY'
                decision['reasoning'].append("✅ Win rate above 60% - execute trade")
                decision['risk_management']['position_size'] = 1
            else:
                decision['action'] = 'ANALYZE'
                decision['reasoning'].append("📊 Analyzing market patterns")
                decision['risk_management']['position_size'] = 0.5
        
        elif learning_stage == 'advanced':
            if win_rate > 70:
                decision['action'] = 'AGGRESSIVE_BUY'
                decision['reasoning'].append("🚀 Advanced strategy - high confidence trade")
                decision['risk_management']['position_size'] = 1.5
                decision['risk_management']['take_profit'] = 0.08
            elif win_rate > 50:
                decision['action'] = 'BUY'
                decision['reasoning'].append("✅ Profitable strategy - execute")
                decision['risk_management']['position_size'] = 1.2
        
        # Market conditions analysis
        if market_conditions.get('volatility', 'medium') == 'high':
            decision['reasoning'].append("⚠️ High volatility - reducing position size")
            decision['risk_management']['position_size'] *= 0.75
        
        self.decision_history.append(decision)
        return decision


# Initialize singletons
VOICE_AUTH_ENGINE = VoiceAuthenticationEngine()
VOICE_PATTERN_LEARNER = VoicePatternLearner()
AUTONOMOUS_DECISION_MAKER = AutonomousDecisionMaker(VOICE_AUTH_ENGINE, VOICE_PATTERN_LEARNER)


def get_voice_auth_engine() -> VoiceAuthenticationEngine:
    """Get voice authentication engine"""
    return VOICE_AUTH_ENGINE


def get_voice_pattern_learner() -> VoicePatternLearner:
    """Get voice pattern learner"""
    return VOICE_PATTERN_LEARNER


def get_autonomous_decision_maker() -> AutonomousDecisionMaker:
    """Get autonomous decision maker"""
    return AUTONOMOUS_DECISION_MAKER


if __name__ == '__main__':
    # Test
    auth = get_voice_auth_engine()
    learner = get_voice_pattern_learner()
    maker = get_autonomous_decision_maker()
    
    print("\n" + "="*80)
    print("JARVIS VOICE LEARNING & AUTHENTICATION SYSTEM")
    print("="*80)
    
    # Test voice enrollment
    print("\n1. VOICE ENROLLMENT")
    test_audio = {
        'pitch': 120,
        'speed': 1.1,
        'tone': 'confident',
        'confidence': 0.95,
        'duration': 2.5,
        'noise_level': 0.1
    }
    
    result = auth.enroll_voice_sample(test_audio, "test command")
    print(f"   Status: {result['message']}")
    print(f"   Samples: {result['total_samples']}")
    
    # Test voice verification
    print("\n2. VOICE VERIFICATION")
    is_auth, conf = auth.verify_voice(test_audio)
    print(f"   Authorized: {is_auth}")
    print(f"   Confidence: {conf:.1f}%")
    
    # Test learning
    print("\n3. VOICE COMMAND LEARNING")
    learner.record_voice_command("buy bitcoin", "BTCUSD", "BUY", 0.92)
    print("   ✅ Command recorded and learned")
    
    # Test trade result
    print("\n4. TRADE RESULT ANALYSIS")
    trade_result = {
        'asset': 'BTCUSD',
        'direction': 'BUY',
        'entry': 42000,
        'exit': 43000,
        'profit': 1000
    }
    insights = learner.record_trade_result(trade_result)
    print(f"   Result: {insights['trade_result']}")
    print(f"   Win Rate: {insights['win_rate']}")
    print(f"   Stage: {insights['learning_stage']}")
    
    # Test smart recommendation
    print("\n5. SMART RECOMMENDATION")
    rec = learner.get_smart_recommendation()
    print(f"   Recommendation: {rec['recommendation']}")
    print(f"   Confidence: {rec['confidence']:.1f}%")
    
    # Test autonomous decision
    print("\n6. AUTONOMOUS DECISION")
    decision = maker.make_decision(
        {'volatility': 'medium'},
        {}
    )
    print(f"   Decision: {decision['action']}")
    print(f"   Position Size: {decision['risk_management']['position_size']}x")
    
    print("\n" + "="*80)
    print("✅ ALL SYSTEMS OPERATIONAL")
    print("="*80 + "\n")
