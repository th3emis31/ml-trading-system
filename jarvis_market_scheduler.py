"""
JARVIS Market Scheduler - Phase B
Automatic market analysis every 2 hours with voice announcements
"""

import json
import logging
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import yfinance as yf

logger = logging.getLogger(__name__)

# State persistence
STATE_FILE = Path('data/market_scheduler_state.json')
STATE_FILE.parent.mkdir(exist_ok=True)


class MarketScheduler:
    """Manages automatic market analysis scheduling and execution"""
    
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.state = self._load_state()
        self.lock = threading.Lock()
        self.voice_engine = None
        self.last_analysis = None
        self.analysis_count = 0
        
    def _load_state(self):
        """Load scheduler state from file"""
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load state: {e}")
        
        return {
            'enabled': True,
            'interval_hours': 2,
            'last_execution': None,
            'total_analyses': 0,
            'next_execution': None,
            'is_running': False,
            'analysis_history': [],
        }
    
    def _save_state(self):
        """Save scheduler state to file"""
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(self.state, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Could not save state: {e}")
    
    def set_voice_engine(self, voice_engine):
        """Set the voice engine for announcements"""
        self.voice_engine = voice_engine
    
    def start(self):
        """Start the market scheduler"""
        with self.lock:
            if self.scheduler.running:
                logger.info("Scheduler already running")
                return
            
            try:
                # Add scheduled job for market analysis every N hours
                interval_hours = self.state.get('interval_hours', 2)
                
                self.scheduler.add_job(
                    func=self._execute_market_analysis,
                    trigger=IntervalTrigger(hours=interval_hours),
                    id='market_analysis_job',
                    name='Market Analysis',
                    replace_existing=True,
                    max_instances=1,  # Only one instance at a time
                )
                
                self.scheduler.start()
                self.state['is_running'] = True
                
                # Calculate next execution
                self._update_next_execution()
                self._save_state()
                
                logger.info(f"Market scheduler started (interval: {interval_hours}h)")
                return True
                
            except Exception as e:
                logger.error(f"Failed to start scheduler: {e}")
                self.state['is_running'] = False
                self._save_state()
                return False
    
    def stop(self):
        """Stop the market scheduler"""
        with self.lock:
            if not self.scheduler.running:
                logger.info("Scheduler not running")
                return
            
            try:
                self.scheduler.shutdown(wait=False)
                self.state['is_running'] = False
                self._save_state()
                logger.info("Market scheduler stopped")
                return True
            except Exception as e:
                logger.error(f"Failed to stop scheduler: {e}")
                return False
    
    def _execute_market_analysis(self):
        """Execute market analysis job"""
        try:
            logger.info("Executing scheduled market analysis...")
            
            # Perform market analysis
            analysis = self._analyze_market()
            
            if analysis:
                with self.lock:
                    self.last_analysis = analysis
                    self.analysis_count += 1
                    
                    # Update state
                    self.state['last_execution'] = datetime.now(timezone.utc).isoformat()
                    self.state['total_analyses'] = self.analysis_count
                    
                    # Keep last 10 analyses in history
                    self.state['analysis_history'].append({
                        'timestamp': analysis.get('timestamp'),
                        'ticker': analysis.get('ticker'),
                        'signal': analysis.get('signal'),
                        'confidence': analysis.get('confidence'),
                    })
                    
                    if len(self.state['analysis_history']) > 10:
                        self.state['analysis_history'] = self.state['analysis_history'][-10:]
                    
                    self._update_next_execution()
                    self._save_state()
                
                # Announce via voice
                self._announce_analysis(analysis)
            
        except Exception as e:
            logger.error(f"Market analysis failed: {e}")
    
    def _analyze_market(self):
        """Analyze market and return trading signals"""
        try:
            # Analyse the instruments this system actually trades. It previously
            # scanned EURUSD/GBPUSD/USDJPY, none of which the models cover, so
            # the scheduled signals had nothing to do with the dashboard.
            tickers = ['GC=F', 'BTC-USD']
            
            analysis_results = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'tickers': {},
                'best_signal': None,
                'best_ticker': None,
                'best_confidence': 0,
                'entry_points': [],
            }
            
            for ticker in tickers:
                try:
                    # Fetch market data
                    data = yf.download(ticker, period='5d', interval='1h', progress=False)
                    
                    if data.empty or len(data) < 3:
                        continue
                    
                    # Simple technical analysis
                    close = data['Close']
                    
                    # Calculate simple moving averages
                    sma_short = close.rolling(window=3).mean().iloc[-1]
                    sma_long = close.rolling(window=10).mean().iloc[-1]
                    
                    current_price = close.iloc[-1]
                    
                    # Determine signal
                    if sma_short > sma_long:
                        signal = 'BUY'
                        confidence = min(0.95, ((sma_short - sma_long) / sma_long) * 100)
                    elif sma_short < sma_long:
                        signal = 'SELL'
                        confidence = min(0.95, ((sma_long - sma_short) / sma_long) * 100)
                    else:
                        signal = 'HOLD'
                        confidence = 0.5
                    
                    analysis_results['tickers'][ticker] = {
                        'price': float(current_price),
                        'signal': signal,
                        'confidence': float(confidence),
                        'sma_short': float(sma_short),
                        'sma_long': float(sma_long),
                    }
                    
                    # Track best signal
                    if float(confidence) > analysis_results['best_confidence'] and signal != 'HOLD':
                        analysis_results['best_signal'] = signal
                        analysis_results['best_ticker'] = ticker
                        analysis_results['best_confidence'] = float(confidence)
                        analysis_results['entry_points'].append({
                            'ticker': ticker,
                            'price': float(current_price),
                            'signal': signal,
                            'confidence': float(confidence),
                        })
                
                except Exception as e:
                    logger.warning(f"Could not analyze {ticker}: {e}")
            
            return analysis_results
            
        except Exception as e:
            logger.error(f"Market analysis error: {e}")
            return None
    
    def _announce_analysis(self, analysis):
        """Announce analysis results via voice"""
        try:
            if not self.voice_engine:
                logger.debug("Voice engine not available for announcement")
                return
            
            # Build announcement text
            best_ticker = analysis.get('best_ticker', 'Unknown')
            best_signal = analysis.get('best_signal', 'HOLD')
            best_confidence = analysis.get('best_confidence', 0)
            
            if best_ticker and best_signal != 'HOLD':
                # Clean ticker name
                ticker_display = best_ticker.replace('=X', '').replace('USD', '')
                
                announcement = f"Market analysis update. {ticker_display} signal is {best_signal.lower()} with {best_confidence:.0%} confidence."
                
                logger.info(f"Announcing: {announcement}")
                
                # Speak announcement (non-blocking)
                try:
                    self.voice_engine.speak(announcement, wait=False, pause_listening=True)
                except Exception as e:
                    logger.warning(f"Voice announcement failed: {e}")
            else:
                logger.debug("No significant signals to announce")
        
        except Exception as e:
            logger.error(f"Announcement failed: {e}")
    
    def _update_next_execution(self):
        """Calculate and update next execution time"""
        interval_hours = self.state.get('interval_hours', 2)
        self.state['next_execution'] = (
            datetime.now(timezone.utc) + timedelta(hours=interval_hours)
        ).isoformat()
    
    def get_status(self):
        """Get scheduler status"""
        with self.lock:
            return {
                'is_running': self.state.get('is_running', False),
                'enabled': self.state.get('enabled', True),
                'interval_hours': self.state.get('interval_hours', 2),
                'last_execution': self.state.get('last_execution'),
                'next_execution': self.state.get('next_execution'),
                'total_analyses': self.state.get('total_analyses', 0),
                'last_analysis': self.last_analysis,
                'scheduler_active': self.scheduler.running,
            }
    
    def get_analysis_history(self):
        """Get history of analyses"""
        with self.lock:
            return self.state.get('analysis_history', [])
    
    def set_interval(self, hours):
        """Set scheduler interval in hours"""
        with self.lock:
            if hours < 0.5 or hours > 24:
                raise ValueError("Interval must be between 0.5 and 24 hours")
            
            self.state['interval_hours'] = hours
            self._save_state()
            
            # Update scheduler if running
            if self.scheduler.running:
                try:
                    self.scheduler.remove_job('market_analysis_job')
                    self.scheduler.add_job(
                        func=self._execute_market_analysis,
                        trigger=IntervalTrigger(hours=hours),
                        id='market_analysis_job',
                        name='Market Analysis',
                    )
                    logger.info(f"Scheduler interval updated to {hours} hours")
                except Exception as e:
                    logger.error(f"Failed to update interval: {e}")
            
            return True
    
    def force_execution(self):
        """Force immediate market analysis"""
        logger.info("Force executing market analysis...")
        self._execute_market_analysis()
        return self.last_analysis
    
    def reset_stats(self):
        """Reset statistics"""
        with self.lock:
            self.state['total_analyses'] = 0
            self.state['analysis_history'] = []
            self.state['last_execution'] = None
            self._save_state()


# Global scheduler instance
_scheduler_instance = None
_scheduler_lock = threading.Lock()


def get_market_scheduler():
    """Get or create market scheduler instance"""
    global _scheduler_instance
    
    if _scheduler_instance is None:
        with _scheduler_lock:
            if _scheduler_instance is None:
                _scheduler_instance = MarketScheduler()
    
    return _scheduler_instance


# Convenience functions
def start_market_scheduler():
    """Start market scheduler"""
    return get_market_scheduler().start()


def stop_market_scheduler():
    """Stop market scheduler"""
    return get_market_scheduler().stop()


def get_scheduler_status():
    """Get scheduler status"""
    return get_market_scheduler().get_status()
