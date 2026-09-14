#!/usr/bin/env python3
"""
JARVIS Voice Enhancement Integration Examples
Shows how to use the new voice enhancements in your code
"""

from jarvis_voice_enhancement import (
    VoiceEnhancementSystem,
    VoiceQualityAnalyzer,
    VoiceCommandParser,
    VoiceHistoryTracker
)
import json
import time


def example_1_basic_usage():
    """Example 1: Basic voice enhancement"""
    print("\n" + "="*60)
    print("EXAMPLE 1: Basic Voice Enhancement")
    print("="*60)
    
    system = VoiceEnhancementSystem()
    
    # Simulate voice input
    audio_data = {
        'transcript': 'open a trade on eurusd',
        'confidence': 0.92,
        'noise_level': 0.1,
        'duration': 2.3,
        'clarity': 0.95
    }
    
    # Process voice input
    result = system.process_voice_input(audio_data)
    
    print(f"\n📊 Quality Analysis:")
    print(f"  Score: {result['quality']['quality_score']:.1f}/100")
    print(f"  Level: {result['quality']['quality_level']}")
    
    print(f"\n🎤 Command Parsing:")
    print(f"  Original: {result['command']['original']}")
    print(f"  Command: {result['command']['command']}")
    
    print(f"\n💬 Response:")
    print(f"  {result['response']['visual_indicator']} {result['response']['message']}")
    
    if result['command']['error_corrections']:
        print(f"\n✓ Corrections:")
        for correction in result['command']['error_corrections']:
            print(f"  - {correction}")


def example_2_quality_analysis():
    """Example 2: Detailed quality analysis"""
    print("\n" + "="*60)
    print("EXAMPLE 2: Voice Quality Analysis")
    print("="*60)
    
    analyzer = VoiceQualityAnalyzer()
    
    # Test various quality levels
    test_cases = [
        {
            'name': 'Perfect Recording',
            'data': {
                'transcript': 'open trade',
                'confidence': 0.98,
                'noise_level': 0.05,
                'duration': 1.5,
                'clarity': 0.99
            }
        },
        {
            'name': 'Noisy Recording',
            'data': {
                'transcript': 'open trade',
                'confidence': 0.65,
                'noise_level': 0.65,
                'duration': 1.5,
                'clarity': 0.60
            }
        },
        {
            'name': 'Too Short',
            'data': {
                'transcript': 'trade',
                'confidence': 0.75,
                'noise_level': 0.15,
                'duration': 0.3,
                'clarity': 0.85
            }
        }
    ]
    
    for test in test_cases:
        print(f"\n🎯 {test['name']}:")
        analysis = analyzer.analyze_quality(test['data'])
        
        print(f"  Quality Score: {analysis['quality_score']:.1f}/100 ({analysis['quality_level']})")
        
        if analysis['issues']:
            print(f"  Issues:")
            for issue in analysis['issues']:
                print(f"    ⚠️  {issue}")
        
        if analysis['suggestions']:
            print(f"  Suggestions:")
            for suggestion in analysis['suggestions']:
                print(f"    💡 {suggestion}")
    
    print(f"\n📈 Average Quality (all tests): {analyzer.get_average_quality():.1f}%")


def example_3_command_parsing():
    """Example 3: Advanced command parsing"""
    print("\n" + "="*60)
    print("EXAMPLE 3: Command Parsing with Error Recovery")
    print("="*60)
    
    parser = VoiceCommandParser()
    
    test_commands = [
        "open a trade on eurusd",
        "um, can you like, open tray on usdcad",  # Fillers + typo
        "what's the status",
        "check market analysis please",
        "set risk to medium",
    ]
    
    for cmd in test_commands:
        print(f"\n📝 Input: '{cmd}'")
        parsed = parser.parse_command(cmd, 0.85)
        
        print(f"  Cleaned: '{parsed['cleaned']}'")
        print(f"  Command: {parsed['command']}")
        
        if parsed['error_corrections']:
            print(f"  Corrections:")
            for correction in parsed['error_corrections']:
                print(f"    ✓ {correction}")


def example_4_history_tracking():
    """Example 4: Voice command history and statistics"""
    print("\n" + "="*60)
    print("EXAMPLE 4: Voice History Tracking")
    print("="*60)
    
    tracker = VoiceHistoryTracker("example_voice_history.json")
    
    # Simulate some commands
    commands = [
        ("open_trade", "open trade on eurusd", True, 0.234),
        ("check_status", "what's the status", True, 0.156),
        ("open_trade", "open a trade on usdcad", True, 0.267),
        ("check_status", "current status please", True, 0.178),
        ("open_trade", "open trade", False, 0.089),  # Failed due to noise
        ("set_risk", "set risk to high", True, 0.145),
    ]
    
    print("\n📋 Adding commands to history...")
    for cmd, transcript, success, exec_time in commands:
        tracker.add_command(cmd, transcript, success, exec_time)
        print(f"  ✓ {cmd}: {transcript[:40]}...")
    
    # Get statistics
    print("\n📊 Command Statistics:")
    stats = tracker.get_command_stats()
    
    print(f"  Total Commands: {stats['total_commands']}")
    print(f"  Success Rate: {stats['success_rate']:.1f}%")
    print(f"  Avg Execution Time: {stats['average_execution_time_ms']:.1f}ms")
    
    print(f"\n  🏆 Most Common Commands:")
    for cmd, count in stats['most_common_commands']:
        print(f"    - {cmd}: {count} times")


def example_5_full_pipeline():
    """Example 5: Complete voice processing pipeline"""
    print("\n" + "="*60)
    print("EXAMPLE 5: Complete Processing Pipeline")
    print("="*60)
    
    system = VoiceEnhancementSystem()
    
    # Simulate realistic user interaction
    print("\n🎤 User speaks: 'um, like, open a tray on eur usd please'")
    
    audio_data = {
        'transcript': 'um, like, open a tray on eur usd please',
        'confidence': 0.82,
        'noise_level': 0.18,
        'duration': 3.2,
        'clarity': 0.88
    }
    
    print("\n⏳ Processing...")
    start_time = time.time()
    
    result = system.process_voice_input(audio_data)
    
    process_time = (time.time() - start_time) * 1000
    
    print(f"\n✅ Processing complete in {process_time:.1f}ms\n")
    
    print(f"📊 Quality Analysis:")
    print(f"  Score: {result['quality']['quality_score']:.0f}/100 ({result['quality']['quality_level']})")
    for metric, value in result['quality']['metrics'].items():
        print(f"  - {metric}: {value}")
    
    print(f"\n🎯 Command Recognized:")
    print(f"  Original: '{result['command']['original']}'")
    print(f"  Cleaned:  '{result['command']['cleaned']}'")
    print(f"  Command:  {result['command']['command']}")
    
    if result['command']['error_corrections']:
        print(f"  Corrections:")
        for corr in result['command']['error_corrections']:
            print(f"    - {corr}")
    
    print(f"\n💬 System Response:")
    print(f"  {result['response']['visual_indicator']}")
    print(f"  {result['response']['message']}")
    
    print(f"\n📈 Diagnostics:")
    diag = system.get_voice_diagnostics()
    print(f"  Average Quality: {diag['average_quality']:.1f}%")
    print(f"  Commands Processed: {diag['command_stats'].get('total_commands', 0)}")


def example_6_integration_tips():
    """Example 6: Integration tips for your app"""
    print("\n" + "="*60)
    print("EXAMPLE 6: Integration Tips")
    print("="*60)
    
    print("""
🔧 HOW TO INTEGRATE INTO YOUR APP:

1. IN YOUR FLASK APP (app.py):
   
   from jarvis_voice_enhancement import VoiceEnhancementSystem
   
   enhancement_system = VoiceEnhancementSystem()
   
   @app.route('/api/voice/process', methods=['POST'])
   def process_voice():
       audio_data = request.json
       result = enhancement_system.process_voice_input(audio_data)
       return jsonify(result)

2. IN YOUR VOICE HANDLER:
   
   def handle_voice_input(transcript, confidence):
       audio_data = {
           'transcript': transcript,
           'confidence': confidence,
           'noise_level': measure_noise(),
           'duration': get_duration(),
           'clarity': measure_clarity()
       }
       result = enhancement_system.process_voice_input(audio_data)
       return result

3. DISPLAY QUALITY FEEDBACK:
   
   quality_score = result['quality']['quality_score']
   quality_level = result['quality']['quality_level']
   
   if quality_score < 70:
       show_warning("Quality: " + quality_level)
   else:
       show_success("Quality: " + quality_level)

4. SHOW DIAGNOSTICS ENDPOINT:
   
   @app.route('/api/voice/diagnostics', methods=['GET'])
   def get_diagnostics():
       return jsonify(enhancement_system.get_voice_diagnostics())

5. MONITOR PERFORMANCE:
   
   Track execution time:
   start = time.time()
   result = enhancement_system.process_voice_input(audio_data)
   exec_time = time.time() - start
    """)


if __name__ == "__main__":
    print("\n[MICROPHONE] JARVIS Voice Enhancement Examples")
    print("=" * 60)
    
    example_1_basic_usage()
    example_2_quality_analysis()
    example_3_command_parsing()
    example_4_history_tracking()
    example_5_full_pipeline()
    example_6_integration_tips()
    
    print("\n" + "="*60)
    print("✅ All examples complete!")
    print("="*60 + "\n")
