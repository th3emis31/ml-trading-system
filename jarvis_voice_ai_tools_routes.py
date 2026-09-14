"""
JARVIS Voice + AI Tools Integration Routes
Connects wake-word system, AI tools, and voice commands
"""

from flask import Blueprint, request, jsonify
from jarvis_wakeword_ai import wake_word_ai, process_wake_word, reset_wakeword
from jarvis_ai_tools import jarvis_ai_tools, get_ai_tools_engine


def register_voice_ai_tools_routes(app):
    """Register all voice + AI tools routes to Flask app."""
    
    # ===== WAKE-WORD ROUTES =====
    @app.route('/api/voice/wakeword/process', methods=['POST'])
    def voice_wakeword_process():
        """Process transcript through wake-word system."""
        try:
            data = request.get_json() or {}
            transcript = data.get('transcript', '')
            confidence = data.get('confidence', 1.0)
            
            if not transcript:
                return jsonify({'status': 'error', 'message': 'No transcript provided'}), 400
            
            result = process_wake_word(transcript, confidence)
            
            return jsonify({
                'status': 'success',
                'wakeword_result': result,
                'timestamp': __import__('datetime').datetime.now().isoformat()
            })
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500
    
    @app.route('/api/voice/wakeword/status', methods=['GET'])
    def voice_wakeword_status():
        """Get wake-word system status."""
        try:
            status = wake_word_ai.get_status()
            return jsonify({
                'status': 'success',
                'wakeword_status': status
            })
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500
    
    @app.route('/api/voice/wakeword/reset', methods=['POST'])
    def voice_wakeword_reset():
        """Reset wake-word activation."""
        try:
            result = reset_wakeword()
            return jsonify({
                'status': 'success',
                'result': result
            })
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500
    
    # ===== AI TOOLS - P1: TRADING RECOMMENDATIONS =====
    @app.route('/api/ai/tools/trading/recommendations', methods=['POST'])
    def ai_trading_recommendations():
        """
        P1: Get AI trading recommendations.
        Optional: risk_tolerance (conservative/moderate/aggressive)
        """
        try:
            data = request.get_json() or {}
            risk_tolerance = data.get('risk_tolerance', 'moderate')
            
            recommendations = jarvis_ai_tools.get_trading_recommendations(risk_tolerance)
            
            return jsonify({
                'status': 'success',
                'recommendations': recommendations
            })
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500
    
    @app.route('/api/ai/tools/trading/analyze/<symbol>', methods=['GET'])
    def ai_trading_analyze_symbol(symbol):
        """Analyze specific symbol for trading opportunities."""
        try:
            analysis = jarvis_ai_tools.analyze_symbol_for_trade(symbol)
            
            return jsonify({
                'status': 'success',
                'analysis': analysis
            })
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500
    
    # ===== AI TOOLS - P2: MARKET ANALYSIS =====
    @app.route('/api/ai/tools/market/analysis', methods=['GET'])
    def ai_market_analysis():
        """P2: Get comprehensive market analysis across all assets."""
        try:
            analysis = jarvis_ai_tools.analyze_market_conditions()
            
            return jsonify({
                'status': 'success',
                'analysis': analysis
            })
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500
    
    @app.route('/api/ai/tools/market/research', methods=['POST'])
    def ai_market_research():
        """Market research on specific topics (volatility, trend, support, resistance)."""
        try:
            data = request.get_json() or {}
            keyword = data.get('keyword', '')
            
            if not keyword:
                return jsonify({'status': 'error', 'message': 'No keyword provided'}), 400
            
            research = jarvis_ai_tools.get_market_research(keyword)
            
            return jsonify({
                'status': 'success',
                'research': research
            })
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500
    
    # ===== AI TOOLS - P3: RISK ASSESSMENT =====
    @app.route('/api/ai/tools/risk/assess-trade', methods=['POST'])
    def ai_risk_assess_trade():
        """
        P3: Assess risk for a specific trade.
        Required: symbol, entry_price, stop_loss, take_profit, position_size
        """
        try:
            data = request.get_json() or {}
            
            required_fields = ['symbol', 'entry_price', 'stop_loss', 'take_profit', 'position_size']
            missing = [f for f in required_fields if f not in data]
            
            if missing:
                return jsonify({
                    'status': 'error',
                    'message': f'Missing required fields: {", ".join(missing)}'
                }), 400
            
            assessment = jarvis_ai_tools.assess_trade_risk(
                symbol=data['symbol'],
                entry_price=float(data['entry_price']),
                stop_loss=float(data['stop_loss']),
                take_profit=float(data['take_profit']),
                position_size=float(data['position_size'])
            )
            
            return jsonify({
                'status': 'success',
                'assessment': assessment
            })
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500
    
    @app.route('/api/ai/tools/risk/portfolio', methods=['POST'])
    def ai_risk_portfolio():
        """
        Assess total portfolio risk from multiple positions.
        Expected positions format: [{'symbol': 'EURUSD', 'entry': 1.085, 'stop_loss': 1.080, 'take_profit': 1.095, 'size': 1.0}, ...]
        """
        try:
            data = request.get_json() or {}
            positions = data.get('positions', [])
            
            if not positions:
                return jsonify({'status': 'error', 'message': 'No positions provided'}), 400
            
            assessment = jarvis_ai_tools.get_portfolio_risk(positions)
            
            return jsonify({
                'status': 'success',
                'assessment': assessment
            })
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500
    
    # ===== COMBINED VOICE COMMAND HANDLER =====
    @app.route('/api/voice/command', methods=['POST'])
    def voice_command_handler():
        """
        Combined handler for voice commands.
        Processes through wake-word first, then routes to appropriate AI tool.
        """
        try:
            data = request.get_json() or {}
            transcript = data.get('transcript', '')
            confidence = data.get('confidence', 1.0)
            command_type = data.get('command_type', 'auto')  # auto/direct
            
            if not transcript:
                return jsonify({'status': 'error', 'message': 'No transcript provided'}), 400
            
            # First, check wake-word
            wakeword_result = process_wake_word(transcript, confidence)
            
            if not wakeword_result['activated']:
                # Not activated yet
                return jsonify({
                    'status': 'listening',
                    'wakeword_result': wakeword_result,
                    'message': 'Waiting for wake-word...'
                })
            
            # Wake-word activated, now process command
            command_text = wakeword_result['command_text']
            command_lower = command_text.lower()
            
            response_data = {
                'status': 'success',
                'wakeword_recognized': True,
                'command': command_text,
                'confidence': confidence
            }
            
            # Route to appropriate AI tool based on command keywords
            if any(word in command_lower for word in ['recommend', 'suggest', 'what trade', 'best trade', 'should i buy']):
                # P1: Trading Recommendations
                tool_response = jarvis_ai_tools.get_trading_recommendations()
                response_data['tool'] = 'trading_recommendations'
                response_data['response'] = tool_response
                response_data['voice_response'] = tool_response['summary']
            
            elif any(word in command_lower for word in ['analyze', 'analysis', 'market', 'research', 'what about']):
                # P2: Market Analysis
                if 'analyze' in command_lower and any(sym in command_lower for sym in ['eurusd', 'gbpusd', 'xauusd', 'btcusd']):
                    # Analyze specific symbol
                    symbol = next((s for s in ['eurusd', 'gbpusd', 'xauusd', 'btcusd'] if s in command_lower), 'EURUSD')
                    tool_response = jarvis_ai_tools.analyze_symbol_for_trade(symbol.upper())
                else:
                    # General market analysis
                    tool_response = jarvis_ai_tools.analyze_market_conditions()
                
                response_data['tool'] = 'market_analysis'
                response_data['response'] = tool_response
                response_data['voice_response'] = tool_response.get('voice_summary', str(tool_response))
            
            elif any(word in command_lower for word in ['risk', 'check risk', 'is this risky', 'safe']):
                # P3: Risk Assessment
                response_data['tool'] = 'risk_assessment'
                response_data['response'] = {
                    'message': 'Risk assessment mode activated. Provide: entry price, stop loss, take profit, and position size.',
                    'needed_fields': ['entry_price', 'stop_loss', 'take_profit', 'position_size']
                }
                response_data['voice_response'] = 'Risk assessment mode ready. Tell me the entry price, stop loss, take profit, and position size.'
            
            else:
                # Default response
                response_data['response'] = {
                    'message': 'Command recognized but type unclear. Try: "Recommend trades", "Analyze market", "Check risk"'
                }
                response_data['voice_response'] = 'I understood you said: ' + command_text + '. Try asking for trading recommendations, market analysis, or risk assessment.'
            
            return jsonify(response_data)
        
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500
    
    @app.route('/api/voice/help', methods=['GET'])
    def voice_help():
        """Get voice command help and available commands."""
        help_text = """
JARVIS Voice AI - Available Commands:

🎯 TRADING RECOMMENDATIONS (P1):
  "Hey JARVIS, what should I trade?"
  "Hey JARVIS, recommend trades"
  "Hey JARVIS, best trades for today"
  
📊 MARKET ANALYSIS (P2):
  "Hey JARVIS, analyze the market"
  "Hey JARVIS, analyze EURUSD"
  "Hey JARVIS, research volatility"
  
⚠️ RISK ASSESSMENT (P3):
  "Hey JARVIS, check risk for this trade"
  "Hey JARVIS, is this risky?"
  "Hey JARVIS, portfolio risk assessment"

💡 SYSTEM:
  "Hey JARVIS, what can you do?"
  "Hey JARVIS, help"
  "Hey JARVIS, status"
        """
        
        return jsonify({
            'status': 'success',
            'help': help_text,
            'voice_commands': {
                'trading_recommendations': [
                    'what should I trade?',
                    'recommend trades',
                    'best trades for today'
                ],
                'market_analysis': [
                    'analyze the market',
                    'analyze EURUSD',
                    'research volatility'
                ],
                'risk_assessment': [
                    'check risk for this trade',
                    'is this risky?',
                    'portfolio risk assessment'
                ]
            }
        })
    
    print("[OK] Voice AI Tools routes registered successfully")
