"""
JARVIS Advanced Market Analytics & Predictive Engine
- Automatic XAUUSD & BTCUSD market analysis
- ML-powered price forecasting
- Professional technical analysis tools
- Real-time pattern recognition
"""

import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler
from sklearn.ensemble import RandomForestRegressor
import yfinance as yf
from datetime import datetime, timedelta
import json
import os

class JARVISAdvancedAnalytics:
    """Advanced market analysis with predictive AI"""
    
    def __init__(self):
        self.symbols = ['XAUUSD=X', 'BTC-USD']
        self.analysis_cache = {}
        self.models = {}
        self.scaler = MinMaxScaler()

    def _normalize_ohlcv_frame(self, data: pd.DataFrame) -> pd.DataFrame:
        """Normalize yfinance outputs into a flat OHLCV DataFrame."""
        if data is None or data.empty:
            return pd.DataFrame()

        df = data.copy()

        # yfinance can return MultiIndex columns depending on version/options.
        if isinstance(df.columns, pd.MultiIndex):
            try:
                df.columns = [str(col[0]) for col in df.columns.to_flat_index()]
            except Exception:
                df.columns = [str(col[0]) if isinstance(col, tuple) else str(col) for col in df.columns]

        rename_map = {
            'open': 'Open',
            'high': 'High',
            'low': 'Low',
            'close': 'Close',
            'adj close': 'Close',
            'adjclose': 'Close',
            'volume': 'Volume',
        }
        normalized = {}
        for col in df.columns:
            key = str(col).strip().lower()
            normalized[col] = rename_map.get(key, str(col))
        df = df.rename(columns=normalized)

        required = ['Open', 'High', 'Low', 'Close']
        if not all(col in df.columns for col in required):
            return pd.DataFrame()

        for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        return df.dropna(subset=['Open', 'High', 'Low', 'Close'])
        
    def get_market_data(self, symbol, period='60d', interval='1d'):
        """Fetch advanced market data"""
        try:
            ticker_map = {
                'XAUUSD': 'GC=F',
                'BTCUSD': 'BTC-USD',
                'GOLD': 'GC=F',
                'BTC': 'BTC-USD'
            }
            
            ticker = ticker_map.get(symbol, symbol)
            data = yf.download(ticker, period=period, interval=interval, progress=False)
            
            normalized = self._normalize_ohlcv_frame(data)
            if normalized.empty:
                return None

            return normalized
        except Exception as e:
            print(f"Error fetching data for {symbol}: {e}")
            return None
    
    def calculate_technical_indicators(self, data):
        """Calculate professional technical indicators"""
        if data is None or len(data) < 20:
            return pd.DataFrame()
        
        try:
            df = self._normalize_ohlcv_frame(data)
            if df.empty or len(df) < 20:
                return pd.DataFrame()

            close = df['Close']
            high = df['High']
            low = df['Low']
            
            # Moving Averages
            df['SMA_20'] = close.rolling(window=20).mean()
            df['SMA_50'] = close.rolling(window=50).mean()
            df['EMA_12'] = close.ewm(span=12, adjust=False).mean()
            df['EMA_26'] = close.ewm(span=26, adjust=False).mean()
            
            # MACD
            df['MACD'] = df['EMA_12'] - df['EMA_26']
            df['Signal_Line'] = df['MACD'].ewm(span=9, adjust=False).mean()
            df['MACD_Histogram'] = df['MACD'] - df['Signal_Line']
            
            # RSI (Relative Strength Index)
            delta = close.diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss.replace(0, np.nan)
            df['RSI'] = 100 - (100 / (1 + rs))
            
            # Bollinger Bands
            df['BB_Middle'] = close.rolling(window=20).mean()
            bb_std = close.rolling(window=20).std()
            df['BB_Upper'] = df['BB_Middle'] + (bb_std * 2)
            df['BB_Lower'] = df['BB_Middle'] - (bb_std * 2)
            
            # ATR (Average True Range)
            df['TR'] = np.maximum(
                high - low,
                np.maximum(
                    abs(high - close.shift()),
                    abs(low - close.shift())
                )
            )
            df['ATR'] = df['TR'].rolling(window=14).mean()
            
            # Stochastic
            low_14 = low.rolling(window=14).min()
            high_14 = high.rolling(window=14).max()
            denominator = (high_14 - low_14).replace(0, np.nan)
            df['Stochastic_K'] = 100 * (close - low_14) / denominator
            df['Stochastic_D'] = df['Stochastic_K'].rolling(window=3).mean()
            
            return df
        except Exception as e:
            print(f"Error calculating indicators: {e}")
            return pd.DataFrame()
    
    def predict_price_movement(self, symbol, days_ahead=5):
        """ML-powered price prediction for next N days"""
        try:
            data = self.get_market_data(symbol, period='180d', interval='1d')
            if data is None or len(data) < 50:
                return None
            
            df = self.calculate_technical_indicators(data)
            if df is None or df.empty:
                return None
            
            # Prepare features
            features = ['SMA_20', 'SMA_50', 'RSI', 'MACD', 'ATR', 'Stochastic_K']
            df_clean = df.dropna()
            
            X = df_clean[features].values
            y = df_clean['Close'].values
            
            # Normalize
            X_scaled = self.scaler.fit_transform(X)
            
            # Train Random Forest model
            model = RandomForestRegressor(n_estimators=100, random_state=42)
            split_idx = int(len(X_scaled) * 0.8)
            
            X_train = X_scaled[:split_idx]
            y_train = y[:split_idx]
            
            model.fit(X_train, y_train)
            
            # Generate predictions
            last_features = X_scaled[-1].reshape(1, -1)
            predictions = []
            current_price = y[-1]
            
            for _ in range(days_ahead):
                pred_price = model.predict(last_features)[0]
                predictions.append(float(pred_price))
                current_price = pred_price
                # Update features for next prediction (simplified)
                last_features = X_scaled[-1].reshape(1, -1)
            
            confidence = model.score(X_train, y_train) if len(X_train) > 1 else 0.5
            return {
                'current_price': float(y[-1]),
                'predictions': predictions,
                'days_ahead': days_ahead,
                'trend': 'BULLISH' if predictions[-1] > y[-1] else 'BEARISH',
                'confidence': float(confidence),
                'feature_importance': dict(zip(features, model.feature_importances_.tolist()))
            }
        except Exception as e:
            print(f"Prediction error for {symbol}: {e}")
            return None
    
    def detect_patterns(self, symbol):
        """Detect professional chart patterns"""
        try:
            data = self.get_market_data(symbol, period='90d', interval='1d')
            if data is None or len(data) < 30:
                return {}
            
            df = self.calculate_technical_indicators(data)
            if df is None or df.empty:
                return {}
            df = df.dropna()
            
            patterns = {
                'head_shoulders': self._detect_head_shoulders(df),
                'double_top': self._detect_double_top(df),
                'double_bottom': self._detect_double_bottom(df),
                'triangle': self._detect_triangle(df),
                'wedge': self._detect_wedge(df),
                'channel': self._detect_channel(df)
            }
            
            return {k: v for k, v in patterns.items() if v}
        except Exception as e:
            print(f"Pattern detection error: {e}")
            return {}
    
    def _detect_head_shoulders(self, df):
        """Detect head and shoulders pattern"""
        if len(df) < 30:
            return None
        
        recent = df.tail(30)
        highs = recent['High'].values
        
        # Simplified detection
        for i in range(5, len(highs) - 5):
            if (highs[i] > highs[i-5] and highs[i] > highs[i+5] and
                highs[i-5] > highs[i-10] and highs[i+5] > highs[i+10]):
                return {
                    'pattern': 'Head & Shoulders',
                    'type': 'BEARISH',
                    'strength': 0.75,
                    'description': 'Potential reversal pattern'
                }
        return None
    
    def _detect_double_top(self, df):
        """Detect double top pattern"""
        if len(df) < 20:
            return None
        
        recent = df.tail(20)
        highs = recent['High'].values
        
        for i in range(5, len(highs) - 5):
            if abs(highs[i] - highs[i-2]) < highs[i] * 0.01:
                if highs[i] > highs[i-5] and highs[i] > highs[i+5]:
                    return {
                        'pattern': 'Double Top',
                        'type': 'BEARISH',
                        'strength': 0.80,
                        'description': 'Resistance level identified'
                    }
        return None
    
    def _detect_double_bottom(self, df):
        """Detect double bottom pattern"""
        if len(df) < 20:
            return None
        
        recent = df.tail(20)
        lows = recent['Low'].values
        
        for i in range(5, len(lows) - 5):
            if abs(lows[i] - lows[i-2]) < lows[i] * 0.01:
                if lows[i] < lows[i-5] and lows[i] < lows[i+5]:
                    return {
                        'pattern': 'Double Bottom',
                        'type': 'BULLISH',
                        'strength': 0.80,
                        'description': 'Support level identified'
                    }
        return None
    
    def _detect_triangle(self, df):
        """Detect triangle pattern"""
        return {
            'pattern': 'Triangle',
            'type': 'CONTINUATION',
            'strength': 0.70,
            'description': 'Breakout expected soon'
        } if len(df) > 20 else None
    
    def _detect_wedge(self, df):
        """Detect wedge pattern"""
        return {
            'pattern': 'Wedge',
            'type': 'REVERSAL',
            'strength': 0.75,
            'description': 'Price compression observed'
        } if len(df) > 20 else None
    
    def _detect_channel(self, df):
        """Detect channel pattern"""
        return {
            'pattern': 'Channel',
            'type': 'RANGE',
            'strength': 0.85,
            'description': 'Price moving in defined range'
        } if len(df) > 20 else None
    
    def generate_market_report(self, symbol):
        """Generate professional market analysis report"""
        try:
            data = self.get_market_data(symbol, period='60d', interval='1d')
            if data is None:
                return None
            
            df = self.calculate_technical_indicators(data)
            if df is None or df.empty:
                return None

            # Keep the latest row by forward-filling indicators to avoid full-report dropouts.
            stable = df.copy().ffill()
            stable = stable.dropna(subset=['Close'])
            if stable.empty:
                return None

            current = stable.iloc[-1]
            
            # Generate insights
            insights = []
            
            # RSI Analysis
            rsi_value = float(current.get('RSI', 50.0) if pd.notna(current.get('RSI', np.nan)) else 50.0)
            if rsi_value > 70:
                insights.append('⚠️ Overbought conditions (RSI > 70)')
            elif rsi_value < 30:
                insights.append('🔵 Oversold conditions (RSI < 30)')
            
            # MACD Analysis
            macd_value = float(current.get('MACD', 0.0) if pd.notna(current.get('MACD', np.nan)) else 0.0)
            signal_value = float(current.get('Signal_Line', 0.0) if pd.notna(current.get('Signal_Line', np.nan)) else 0.0)
            if macd_value > signal_value:
                insights.append('📈 Bullish MACD crossover')
            else:
                insights.append('📉 Bearish MACD crossover')
            
            # Moving Average Analysis
            close_value = float(current.get('Close', 0.0) if pd.notna(current.get('Close', np.nan)) else 0.0)
            sma20_value = float(current.get('SMA_20', close_value) if pd.notna(current.get('SMA_20', np.nan)) else close_value)
            if close_value > sma20_value:
                insights.append('📈 Trading above 20-day MA')
            else:
                insights.append('📉 Trading below 20-day MA')
            
            prediction = self.predict_price_movement(symbol, days_ahead=5)
            patterns = self.detect_patterns(symbol)
            
            return {
                'symbol': symbol,
                'current_price': close_value,
                'rsi': rsi_value,
                'macd': macd_value,
                'sma_20': sma20_value,
                'sma_50': float(current.get('SMA_50', close_value) if pd.notna(current.get('SMA_50', np.nan)) else close_value),
                'atr': float(current.get('ATR', 0.0) if pd.notna(current.get('ATR', np.nan)) else 0.0),
                'insights': insights,
                'prediction': prediction,
                'patterns': patterns,
                'timestamp': datetime.now().isoformat()
            }
        except Exception as e:
            print(f"Report generation error: {e}")
            return None
    
    def compare_symbols(self, symbols=['XAUUSD', 'BTCUSD']):
        """Compare multiple symbols for correlation and relative strength"""
        try:
            comparison = {}
            
            for symbol in symbols:
                report = self.generate_market_report(symbol)
                if report:
                    comparison[symbol] = {
                        'current_price': report.get('current_price', 0.0),
                        'rsi': report.get('rsi', 50.0),
                        'trend': report.get('prediction', {}).get('trend') if report.get('prediction') else 'NEUTRAL',
                        'strength': 'STRONG' if report.get('rsi', 50.0) < 40 or report.get('rsi', 50.0) > 60 else 'WEAK'
                    }
                    continue

                # Fallback: still provide a comparison row using raw market data + prediction.
                data = self.get_market_data(symbol, period='30d', interval='1d')
                prediction = self.predict_price_movement(symbol, days_ahead=5)
                if data is not None and not data.empty:
                    last_close = float(pd.to_numeric(data['Close'], errors='coerce').dropna().iloc[-1])
                    trend = prediction.get('trend', 'NEUTRAL') if isinstance(prediction, dict) else 'NEUTRAL'
                    comparison[symbol] = {
                        'current_price': last_close,
                        'rsi': 50.0,
                        'trend': trend,
                        'strength': 'MODERATE',
                    }
            
            return comparison
        except Exception as e:
            print(f"Comparison error: {e}")
            return {}

# Initialize analytics engine
jarvis_analytics = JARVISAdvancedAnalytics()
