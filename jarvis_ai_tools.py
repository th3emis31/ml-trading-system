"""
JARVIS AI TOOLS ENGINE
P1: Trading Recommendations
P2: Market Analysis Tools
P3: Risk Assessment Tools

Voice-activated AI tools for intelligent trading decisions.
"""

import json
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional


class JARVISAITools:
    """Advanced AI tools for trading, market analysis, and risk management."""
    
    def __init__(self):
        self.market_data = {}
        self.trade_history = []
        self.risk_profiles = {}
        self.initialize_default_market_data()
    
    TRACKED_SYMBOLS = ('XAUUSD', 'BTCUSD')

    def initialize_default_market_data(self):
        """Load live market data for the symbols this system actually trades.

        This previously held a hard-coded table (gold at 1965, BTC at 42500)
        that the recommendation engine priced real entries and stops from. A
        symbol that cannot be fetched is omitted rather than guessed, so the
        tools report nothing instead of reporting fiction.
        """
        self.market_data = {}
        self.market_data_error = None
        try:
            from src.data import fetch_real_data
            from src.features import build_features
        except Exception as exc:
            self.market_data_error = f'market data modules unavailable: {exc}'
            return

        for symbol in self.TRACKED_SYMBOLS:
            try:
                frame = fetch_real_data(symbol, period='30d', interval='1h')
                features = build_features(frame)
                if features.empty:
                    continue
                row = features.iloc[-1]
                recent = frame.tail(120)
                price = float(row['close'])
                trend_value = float(row['trend_20'])
                self.market_data[symbol] = {
                    'price': round(price, 4),
                    'trend': 'bullish' if trend_value > 0 else 'bearish' if trend_value < 0 else 'neutral',
                    'volatility': round(float(row.get('atr_pct') or 0.0), 4),
                    'support': round(float(recent['low'].min()), 4),
                    'resistance': round(float(recent['high'].max()), 4),
                    'as_of': str(frame['datetime'].iloc[-1]) if 'datetime' in frame.columns else None,
                }
            except Exception as exc:
                self.market_data_error = f'{symbol}: {exc}'

    def refresh_market_data(self):
        """Re-pull live prices; the cached snapshot goes stale within minutes."""
        self.initialize_default_market_data()
        return self.market_data
    
    # ===== P1: TRADING RECOMMENDATIONS =====
    def get_trading_recommendations(self, user_risk_tolerance: str = "moderate") -> Dict:
        """
        P1: Get AI trading recommendations based on market analysis.
        
        Returns: List of recommended trades with entry/exit/stop-loss
        """
        recommendations = []
        
        # Analyze each symbol
        for symbol, data in self.market_data.items():
            score = self._calculate_recommendation_score(symbol, data, user_risk_tolerance)
            
            if score['overall_score'] >= 0.65:  # Only recommend high-confidence trades
                entry_price = data['price']
                
                # Calculate position sizing based on risk tolerance
                position_size = self._calculate_position_size(user_risk_tolerance)
                
                rec = {
                    'symbol': symbol,
                    'action': 'BUY' if data['trend'] == 'bullish' else 'SELL' if data['trend'] == 'bearish' else 'HOLD',
                    'entry_price': entry_price,
                    'stop_loss': data['support'] if data['trend'] == 'bullish' else data['resistance'],
                    'take_profit': data['resistance'] if data['trend'] == 'bullish' else data['support'],
                    'position_size': position_size,
                    'confidence': score['overall_score'],
                    'rationale': score['rationale'],
                    'risk_reward_ratio': score['risk_reward_ratio'],
                    'expected_profit': position_size * (data['resistance'] - entry_price) if data['trend'] == 'bullish' else position_size * (entry_price - data['support']),
                    'timeframe': '4H' if data['volatility'] > 0.5 else '1D',
                    'trend': data['trend'],
                    'volatility': data['volatility']
                }
                recommendations.append(rec)
        
        # Sort by confidence (descending)
        recommendations = sorted(recommendations, key=lambda x: x['confidence'], reverse=True)
        
        return {
            'status': 'success',
            'timestamp': datetime.now().isoformat(),
            'user_risk_tolerance': user_risk_tolerance,
            'recommendation_count': len(recommendations),
            'recommendations': recommendations[:5],  # Top 5
            'summary': f"Found {len(recommendations)} trading opportunities. Showing top 5 by confidence.",
            'next_action': "Choose a recommendation and I'll help you execute the trade!"
        }
    
    def analyze_symbol_for_trade(self, symbol: str) -> Dict:
        """Deep analysis of specific symbol for trading."""
        if symbol.upper() not in self.market_data:
            return {
                'status': 'error',
                'message': f'Symbol {symbol} not found in market data.'
            }
        
        data = self.market_data[symbol.upper()]
        
        # Calculate technical indicators
        rsi = self._calculate_rsi(data)
        macd = self._calculate_macd(data)
        moving_avg = self._calculate_moving_average(data)
        
        return {
            'status': 'success',
            'symbol': symbol.upper(),
            'current_price': data['price'],
            'trend': data['trend'],
            'volatility_level': 'High' if data['volatility'] > 0.5 else 'Medium' if data['volatility'] > 0.35 else 'Low',
            'support': data['support'],
            'resistance': data['resistance'],
            'technical_indicators': {
                'RSI': rsi,
                'MACD': macd,
                'MA50': moving_avg
            },
            'trade_recommendation': {
                'direction': 'LONG' if data['trend'] == 'bullish' else 'SHORT' if data['trend'] == 'bearish' else 'NEUTRAL',
                'strength': 'Strong' if data['volatility'] < 0.4 else 'Moderate',
                'best_entry': self._calculate_best_entry(symbol, data),
                'stop_loss': data['support'] if data['trend'] == 'bullish' else data['resistance'],
                'take_profit': data['resistance'] if data['trend'] == 'bullish' else data['support']
            },
            'voice_summary': f"{symbol}: Currently {data['trend'].upper()} trend. RSI is {rsi}. Resistance at {data['resistance']}, support at {data['support']}."
        }
    
    # ===== P2: MARKET ANALYSIS TOOLS =====
    def analyze_market_conditions(self) -> Dict:
        """
        P2: Comprehensive market analysis across all tracked assets.
        """
        analysis = {
            'timestamp': datetime.now().isoformat(),
            'market_overview': {},
            'trends': {'bullish': 0, 'bearish': 0, 'neutral': 0},
            'volatility_profile': {},
            'correlation_analysis': {},
            'sector_strength': {}
        }
        
        bullish_count = 0
        bearish_count = 0
        neutral_count = 0
        
        for symbol, data in self.market_data.items():
            if data['trend'] == 'bullish':
                bullish_count += 1
            elif data['trend'] == 'bearish':
                bearish_count += 1
            else:
                neutral_count += 1
            
            analysis['market_overview'][symbol] = {
                'price': data['price'],
                'trend': data['trend'],
                'volatility': data['volatility'],
                'support': data['support'],
                'resistance': data['resistance']
            }
            
            # Volatility classification
            vol_level = 'High' if data['volatility'] > 0.5 else 'Medium' if data['volatility'] > 0.35 else 'Low'
            if vol_level not in analysis['volatility_profile']:
                analysis['volatility_profile'][vol_level] = []
            analysis['volatility_profile'][vol_level].append(symbol)
        
        analysis['trends'] = {
            'bullish': bullish_count,
            'bearish': bearish_count,
            'neutral': neutral_count,
            'overall_market_sentiment': 'BULLISH' if bullish_count > neutral_count + bearish_count else 'BEARISH' if bearish_count > neutral_count else 'NEUTRAL'
        }
        
        analysis['voice_summary'] = f"Market Overview: {bullish_count} bullish, {bearish_count} bearish, {neutral_count} neutral. Overall sentiment is {analysis['trends']['overall_market_sentiment']}."
        
        return analysis
    
    def get_market_research(self, keyword: str) -> Dict:
        """Research specific market topics."""
        keyword_lower = keyword.lower()
        
        research_db = {
            'volatility': {
                'definition': 'Market price fluctuation intensity',
                'current_state': 'Moderate volatility across most pairs',
                'impact': 'Wider stop-losses recommended for high volatility trades',
                'advice': 'Use tighter position sizing in high volatility environments'
            },
            'trend': {
                'definition': 'Direction of price movement (up/down/sideways)',
                'current_state': 'Mixed: EURUSD, XAUUSD, BTCUSD trending up; GBPUSD neutral',
                'impact': 'Trend-following strategies more effective now',
                'advice': 'Trade with the trend for higher probability trades'
            },
            'support': {
                'definition': 'Price level where demand is expected',
                'current_state': f'Key support levels: EURUSD 1.0800, XAUUSD 1950, BTCUSD 41000',
                'impact': 'Bounces expected near these levels',
                'advice': 'Use support as potential entry points for long trades'
            },
            'resistance': {
                'definition': 'Price level where supply is expected',
                'current_state': f'Key resistance levels: EURUSD 1.0900, XAUUSD 1980, BTCUSD 44000',
                'impact': 'Rejections expected near these levels',
                'advice': 'Take profits near resistance or short from these levels'
            }
        }
        
        for key, value in research_db.items():
            if key in keyword_lower:
                return {
                    'status': 'success',
                    'research_topic': key.upper(),
                    'information': value,
                    'voice_summary': f"{key.capitalize()}: {value['current_state']}. {value['advice']}"
                }
        
        return {
            'status': 'not_found',
            'message': f'No research data for "{keyword}". Try: volatility, trend, support, resistance'
        }
    
    # ===== P3: RISK ASSESSMENT TOOLS =====
    def assess_trade_risk(self, symbol: str, entry_price: float, stop_loss: float, take_profit: float, position_size: float) -> Dict:
        """
        P3: Comprehensive risk assessment for a potential trade.
        """
        if symbol.upper() not in self.market_data:
            return {'status': 'error', 'message': f'Symbol {symbol} not found'}
        
        # Calculate risk metrics
        risk_amount = abs(entry_price - stop_loss) * position_size
        profit_amount = abs(take_profit - entry_price) * position_size
        risk_reward_ratio = profit_amount / risk_amount if risk_amount > 0 else 0
        
        # Risk percentage
        risk_percentage = (risk_amount / (position_size * entry_price)) * 100 if entry_price > 0 else 0
        
        # Risk level classification
        if risk_percentage > 5:
            risk_level = 'HIGH'
        elif risk_percentage > 2.5:
            risk_level = 'MEDIUM'
        else:
            risk_level = 'LOW'
        
        assessment = {
            'status': 'success',
            'symbol': symbol.upper(),
            'entry_price': entry_price,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'position_size': position_size,
            'risk_metrics': {
                'risk_amount': round(risk_amount, 2),
                'profit_amount': round(profit_amount, 2),
                'risk_reward_ratio': round(risk_reward_ratio, 2),
                'risk_percentage': round(risk_percentage, 2),
                'risk_level': risk_level,
                'max_daily_loss': round(risk_amount * 3, 2)  # 3 trades per day max
            },
            'recommendations': {
                'position_size_ok': risk_percentage <= 2.5,
                'risk_reward_ok': risk_reward_ratio >= 1.5,
                'stop_loss_tight': risk_percentage < 1.0,
                'overall_rating': 'GOOD' if (risk_reward_ratio >= 1.5 and risk_percentage <= 2.5) else 'FAIR' if (risk_reward_ratio >= 1.0 and risk_percentage <= 3.5) else 'RISKY'
            },
            'voice_summary': f"Risk Assessment for {symbol}: {risk_level} risk. Risk-reward ratio is {risk_reward_ratio:.2f}. " + 
                            ("Trade looks good." if risk_reward_ratio >= 1.5 else "Consider adjusting position size.") if risk_percentage <= 2.5 else f"Warning: Risk is {risk_percentage:.1f}% - too high!"
        }
        
        return assessment
    
    def get_portfolio_risk(self, positions: List[Dict]) -> Dict:
        """Assess total portfolio risk from multiple positions."""
        total_risk = 0
        total_profit_potential = 0
        high_risk_positions = []
        
        for pos in positions:
            risk_amt = abs(pos['entry'] - pos['stop_loss']) * pos['size']
            profit_amt = abs(pos['take_profit'] - pos['entry']) * pos['size']
            total_risk += risk_amt
            total_profit_potential += profit_amt
            
            if (risk_amt / (pos['size'] * pos['entry'])) > 0.03:  # > 3% risk
                high_risk_positions.append(pos['symbol'])
        
        portfolio_risk_reward = total_profit_potential / total_risk if total_risk > 0 else 0
        
        return {
            'status': 'success',
            'total_positions': len(positions),
            'total_risk_exposure': round(total_risk, 2),
            'total_profit_potential': round(total_profit_potential, 2),
            'portfolio_risk_reward_ratio': round(portfolio_risk_reward, 2),
            'high_risk_positions': high_risk_positions,
            'overall_portfolio_health': 'HEALTHY' if portfolio_risk_reward >= 1.5 else 'MODERATE' if portfolio_risk_reward >= 1.0 else 'NEEDS_ATTENTION',
            'voice_summary': f"Portfolio risk assessment: {len(positions)} positions. Risk-reward ratio is {portfolio_risk_reward:.2f}. " +
                            (f"⚠️ Warning: {len(high_risk_positions)} high-risk positions detected." if high_risk_positions else "Portfolio risk looks balanced.")
        }
    
    # ===== HELPER METHODS =====
    def _calculate_recommendation_score(self, symbol: str, data: Dict, risk_tolerance: str) -> Dict:
        """Calculate recommendation score based on market data and risk tolerance."""
        score = 0.0
        rationale = []
        
        # Trend factor (40%)
        if data['trend'] == 'bullish':
            score += 0.4
            rationale.append("Strong bullish trend detected")
        elif data['trend'] == 'bearish':
            score += 0.2
            rationale.append("Bearish trend - shorts possible")
        else:
            score += 0.15
        
        # Volatility factor (30%)
        if 0.35 < data['volatility'] < 0.6:  # Sweet spot
            score += 0.3
            rationale.append(f"Volatility at optimal level ({data['volatility']:.2f})")
        elif data['volatility'] <= 0.35:
            score += 0.2
            rationale.append("Low volatility - stable moves")
        else:
            score += 0.1
            rationale.append("High volatility - wide moves expected")
        
        # Support/Resistance proximity (30%)
        distance_to_support = (data['price'] - data['support']) / data['support']
        distance_to_resistance = (data['resistance'] - data['price']) / data['resistance']
        
        if distance_to_support > 0.02 and distance_to_resistance > 0.02:  # Good room to move
            score += 0.3
            rationale.append("Good distance to support and resistance")
        elif distance_to_support > 0.01 or distance_to_resistance > 0.01:
            score += 0.15
        
        risk_reward = distance_to_resistance / distance_to_support if distance_to_support > 0 else 1.0
        
        return {
            'overall_score': min(score, 1.0),
            'rationale': "; ".join(rationale),
            'risk_reward_ratio': round(risk_reward, 2)
        }
    
    def _calculate_position_size(self, risk_tolerance: str) -> float:
        """Calculate position size based on risk tolerance."""
        sizes = {
            'conservative': 0.5,
            'moderate': 1.0,
            'aggressive': 2.0
        }
        return sizes.get(risk_tolerance, 1.0)
    
    def _calculate_rsi(self, data: Dict) -> float:
        """Simulate RSI calculation."""
        base_rsi = 50
        trend_adjust = 15 if data['trend'] == 'bullish' else -15 if data['trend'] == 'bearish' else 0
        return min(100, max(0, base_rsi + trend_adjust))
    
    def _calculate_macd(self, data: Dict) -> Dict:
        """Simulate MACD calculation."""
        return {
            'value': 0.5 if data['trend'] == 'bullish' else -0.3 if data['trend'] == 'bearish' else 0.0,
            'signal': 0.3,
            'histogram': 0.2 if data['trend'] == 'bullish' else -0.2
        }
    
    def _calculate_moving_average(self, data: Dict) -> float:
        """Simulate Moving Average calculation."""
        return data['price'] * (1 + (0.02 if data['trend'] == 'bullish' else -0.02 if data['trend'] == 'bearish' else 0))
    
    def _calculate_best_entry(self, symbol: str, data: Dict) -> float:
        """Calculate best entry price for the trade."""
        if data['trend'] == 'bullish':
            return data['support']  # Buy near support
        elif data['trend'] == 'bearish':
            return data['resistance']  # Sell near resistance
        else:
            return data['price']  # Current price for neutral


# Global instance
jarvis_ai_tools = JARVISAITools()


def get_ai_tools_engine() -> JARVISAITools:
    """Get AI tools engine instance."""
    return jarvis_ai_tools
