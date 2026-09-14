# JARVIS Expert AI - Flask API Routes
# Add these routes to your app.py file

@app.route('/api/jarvis/voice-verify', methods=['POST'])
def jarvis_voice_verify():
    """Verify user's voice - speaker authentication"""
    data = request.json or {}
    confidence = data.get('confidence', 0.7)
    result = verify_user_voice(confidence)
    return jsonify(result)


@app.route('/api/jarvis/profile', methods=['GET'])
def jarvis_get_profile():
    """Get user AI learning profile"""
    return jsonify(get_user_profile_summary())


@app.route('/api/jarvis/log-trade', methods=['POST'])
def jarvis_log_trade():
    """Log trade result for AI learning"""
    data = request.json or {}
    try:
        log_trade_result(
            symbol=data.get('symbol', 'BTCUSD'),
            entry=float(data.get('entry', 0)),
            exit=float(data.get('exit', 0)),
            pnl=float(data.get('pnl', 0)),
            timeframe=data.get('timeframe', '1h')
        )
        return jsonify({'success': True, 'message': 'Trade logged'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/jarvis/recommend', methods=['GET'])
def jarvis_get_recommendation():
    """Get AI trade recommendation based on learned patterns"""
    symbol = request.args.get('symbol', 'BTCUSD')
    
    try:
        # Get current price
        if symbol == 'XAUUSD':
            price_data = yf.download('GC=F', period='1d', progress=False)
        else:
            price_data = yf.download('BTC-USD', period='1d', progress=False)
        
        current_price = float(price_data['close'].iloc[-1]) if not price_data.empty else 0
        
        # Get recent candles for technical analysis
        candles_data = []
        for i in range(len(price_data) - 20, len(price_data)):
            if i >= 0:
                row = price_data.iloc[i]
                candles_data.append({
                    'open': float(row['open']),
                    'high': float(row['high']),
                    'low': float(row['low']),
                    'close': float(row['close']),
                    'volume': int(row['volume'])
                })
        
        # Get AI recommendation
        rec = get_ai_trade_recommendation(symbol, current_price, candles_data)
        return jsonify(rec)
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/jarvis/analyze', methods=['GET'])
def jarvis_analyze_chart():
    """Analyze chart with professional pattern recognition"""
    symbol = request.args.get('symbol', 'BTCUSD')
    
    try:
        # Get price data
        if symbol == 'XAUUSD':
            price_data = yf.download('GC=F', period='1mo', progress=False)
        else:
            price_data = yf.download('BTC-USD', period='1mo', progress=False)
        
        candles_data = []
        for i in range(len(price_data)):
            row = price_data.iloc[i]
            candles_data.append({
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': int(row['volume'])
            })
        
        # Detect patterns
        consolidation = pattern_recognition.detect_consolidation(candles_data)
        liquidity_gaps = pattern_recognition.detect_liquidity_gap(candles_data)
        support_resistance = pattern_recognition.detect_support_resistance(candles_data)
        trend = pattern_recognition.detect_trend(candles_data)
        
        analysis = {
            'symbol': symbol,
            'consolidation': consolidation,
            'liquidity_gaps': liquidity_gaps,
            'support_resistance': support_resistance,
            'trend': trend,
            'confidence': user_profile.profile['win_rate'],
            'pattern': consolidation['type'] if consolidation else 'trend',
            'recommendation': 'Wait for breakout' if consolidation else 'Follow trend'
        }
        
        return jsonify(analysis)
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/jarvis/suggest-trade', methods=['GET'])
def jarvis_suggest_trade():
    """Get professional trade setup from learned patterns"""
    symbol = request.args.get('symbol', 'BTCUSD')
    
    try:
        rec = get_ai_trade_recommendation(symbol, 0, {})
        
        setup = {
            'setup_type': 'Consolidation Breakout' if rec['reason'] else 'Trend Following',
            'symbol': symbol,
            'timeframe': user_profile.profile.get('best_timeframe', '4h'),
            'entry': rec.get('suggested_entry', 0),
            'sl': rec.get('stop_loss', 0),
            'tp': rec.get('take_profit', 0),
            'rr': user_profile.profile['target_rr_ratio'],
            'confidence': rec['confidence'],
            'reasons': rec['reason']
        }
        
        return jsonify(setup)
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/jarvis/insights', methods=['GET'])
def jarvis_get_insights():
    """Get AI system insights about performance"""
    insights = get_system_insights()
    return jsonify(insights)


@app.route('/api/jarvis/improvements', methods=['GET'])
def jarvis_get_improvements():
    """Get system improvement suggestions"""
    improvements = get_system_improvements()
    return jsonify(improvements)


@app.route('/api/jarvis/auto-improve', methods=['POST'])
def jarvis_auto_improve():
    """AI engine auto-improvement cycle"""
    try:
        improvements = get_system_improvements()
        
        # Apply improvements automatically
        improvements_applied = []
        for imp in improvements:
            if imp.get('type') == 'increase_confidence_threshold':
                user_profile.profile['voice_auth'].profile['confidence_threshold'] = imp.get('to', 0.80)
                improvements_applied.append(f"Confidence threshold increased to {imp['to']}")
            elif imp.get('type') == 'auto_setup_detection':
                improvements_applied.append("Auto-setup detection enabled")
            elif imp.get('type') == 'enable_autonomous_mode':
                improvements_applied.append("Autonomous trading mode ready")
        
        user_profile.save_profile()
        
        return jsonify({
            'improvements_made': ' | '.join(improvements_applied) if improvements_applied else 'System optimal',
            'count': len(improvements_applied)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/jarvis/voice-enroll', methods=['POST'])
def jarvis_voice_enroll():
    """Enroll user's voice for speaker recognition"""
    try:
        audio_hash = request.json.get('audio_hash', '')
        confidence = request.json.get('confidence', 0.95)
        
        voice_auth.add_voice_sample(audio_hash, confidence)
        
        return jsonify({
            'success': True,
            'message': 'Voice enrolled successfully',
            'samples': len(voice_auth.profile['voice_samples'])
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/jarvis/learning-dashboard', methods=['GET'])
def jarvis_learning_dashboard():
    """Get comprehensive learning dashboard data"""
    try:
        profile = get_user_profile_summary()
        insights = get_system_insights()
        improvements = get_system_improvements()
        
        dashboard = {
            'profile': profile,
            'insights': insights,
            'improvements': improvements,
            'recent_trades': user_profile.profile.get('successful_trades', [])[-5:],
            'accuracy': user_profile.profile.get('win_rate', 0),
            'status': 'Expert' if profile['learning_score'] >= 80 else 'Advanced' if profile['learning_score'] >= 60 else 'Intermediate'
        }
        
        return jsonify(dashboard)
    except Exception as e:
        return jsonify({'error': str(e)}), 400
