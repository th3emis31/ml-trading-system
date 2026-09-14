"""
JARVIS Integration Layer
Connects Voice Response and Learning Systems to Flask
Add these new API endpoints to app.py
"""

def register_voice_learning_routes(app):
    """Register new voice and learning routes"""
    from flask import jsonify, request
    from jarvis_voice_response import get_voice_engine
    from jarvis_command_learner import get_command_learner
    import time
    
    voice_engine = get_voice_engine()
    learner = get_command_learner()
    
    # ========== VOICE RESPONSE ENDPOINTS ==========
    
    @app.route('/api/voice/speak', methods=['POST'])
    def voice_speak_api():
        """Make JARVIS speak text"""
        try:
            data = request.get_json(silent=True) or {}
            text = str(data.get('text', '')).strip()
            wait = bool(data.get('wait', False))
            
            if not text:
                return jsonify({'error': 'text required'}), 400
            
            voice_engine.speak(text, wait=wait)
            return jsonify({'success': True, 'text': text})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/voice/acknowledge', methods=['POST'])
    def voice_acknowledge_api():
        """JARVIS acknowledges command"""
        voice_engine.acknowledge()
        return jsonify({'success': True})
    
    @app.route('/api/voice/confirm', methods=['POST'])
    def voice_confirm_api():
        """JARVIS confirms completion"""
        voice_engine.confirm()
        return jsonify({'success': True})
    
    @app.route('/api/voice/error', methods=['POST'])
    def voice_error_api():
        """JARVIS reports error"""
        try:
            data = request.get_json(silent=True) or {}
            message = str(data.get('message', 'Error occurred')).strip()
            voice_engine.error(message)
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/voice/price', methods=['POST'])
    def voice_price_api():
        """JARVIS reads price"""
        try:
            data = request.get_json(silent=True) or {}
            symbol = str(data.get('symbol', 'UNKNOWN')).strip().upper()
            price = float(data.get('price', 0))
            change = float(data.get('change', 0))
            
            voice_engine.read_price(symbol, price, change)
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/voice/signal', methods=['POST'])
    def voice_signal_api():
        """JARVIS reads signal"""
        try:
            data = request.get_json(silent=True) or {}
            symbol = str(data.get('symbol', 'UNKNOWN')).strip().upper()
            signal = str(data.get('signal', 'unknown')).strip()
            confidence = float(data.get('confidence', 0))
            
            voice_engine.read_signal(symbol, signal, confidence)
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/voice/stop', methods=['POST'])
    def voice_stop_api():
        """Stop JARVIS speaking"""
        voice_engine.stop()
        return jsonify({'success': True})
    
    # ========== LEARNING ENDPOINTS ==========
    
    @app.route('/api/learning/log-command', methods=['POST'])
    def learning_log_command():
        """Log command for learning"""
        try:
            data = request.get_json(silent=True) or {}
            start_time = data.get('_start_time', time.time())
            response_time = time.time() - start_time
            
            learner.log_command(
                user_input=str(data.get('user_input', '')).strip(),
                intent=str(data.get('intent', 'general')).strip(),
                action=str(data.get('action', '')).strip(),
                success=bool(data.get('success', False)),
                symbol=data.get('symbol'),
                result=str(data.get('result', '')).strip()[:200],
                response_time=response_time
            )
            
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/learning/insights')
    def learning_insights():
        """Get learning insights"""
        try:
            insights = learner.generate_insights()
            return jsonify(insights)
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/learning/report')
    def learning_report():
        """Get learning report"""
        try:
            report = learner.get_learning_report()
            return jsonify({'report': report})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/learning/top-commands')
    def learning_top_commands():
        """Get most used commands"""
        try:
            hours = int(request.args.get('hours', 24))
            commands = learner.get_command_frequency(hours=hours)
            return jsonify(commands)
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/learning/top-symbols')
    def learning_top_symbols():
        """Get most traded symbols"""
        try:
            hours = int(request.args.get('hours', 24))
            symbols = learner.get_most_traded_symbols(hours=hours)
            return jsonify({'symbols': symbols})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/learning/stats')
    def learning_stats():
        """Get learning statistics"""
        try:
            hours = int(request.args.get('hours', 24))
            stats = {
                'success_rate': learner.get_success_rate(hours=hours),
                'peak_times': learner.get_peak_usage_times(hours=hours),
                'top_symbols': learner.get_most_traded_symbols(hours=hours, limit=5),
                'top_commands': learner.get_command_frequency(hours=hours),
            }
            return jsonify(stats)
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/learning/preference', methods=['POST'])
    def learning_set_preference():
        """Store user preference"""
        try:
            data = request.get_json(silent=True) or {}
            key = str(data.get('key', '')).strip()
            value = str(data.get('value', '')).strip()
            
            if not key:
                return jsonify({'error': 'key required'}), 400
            
            learner.set_preference(key, value)
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/learning/preference/<key>')
    def learning_get_preference(key):
        """Get user preference"""
        try:
            value = learner.get_preference(key)
            return jsonify({'key': key, 'value': value})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    print("[OK] Voice and Learning routes registered!")
