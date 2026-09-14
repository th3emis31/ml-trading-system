"""
JARVIS Smart Learning System
Learns from user commands and patterns
"""
import json
import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from collections import defaultdict, Counter

logger = logging.getLogger("jarvis.learning")

class CommandLearner:
    """Tracks and learns from user commands"""
    
    def __init__(self, db_path: str = "jarvis_command_history.db"):
        self.db_path = db_path
        self._init_db()
        logger.info("Command Learner initialized")
    
    def _init_db(self):
        """Initialize database"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS commands (
                    id INTEGER PRIMARY KEY,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    user_input TEXT,
                    intent TEXT,
                    action TEXT,
                    success BOOLEAN,
                    symbol TEXT,
                    result_summary TEXT,
                    response_time FLOAT
                )
            ''')
            
            conn.execute('''
                CREATE TABLE IF NOT EXISTS user_preferences (
                    preference_key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.execute('''
                CREATE TABLE IF NOT EXISTS learning_insights (
                    id INTEGER PRIMARY KEY,
                    insight_type TEXT,
                    data JSON,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
    
    def log_command(self, user_input: str, intent: str, action: str, 
                   success: bool, symbol: Optional[str] = None, 
                   result: Optional[str] = None, response_time: float = 0):
        """Log a command execution"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    INSERT INTO commands 
                    (user_input, intent, action, success, symbol, result_summary, response_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (user_input, intent, action, success, symbol, result, response_time))
                conn.commit()
            
            logger.info(f"Logged command: {intent} - {action} - {'✅' if success else '❌'}")
        except Exception as e:
            logger.error(f"Error logging command: {e}")
    
    def get_command_frequency(self, hours: int = 24) -> Dict[str, int]:
        """Get most frequent commands in last N hours"""
        try:
            cutoff = datetime.now() - timedelta(hours=hours)
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute('''
                    SELECT intent, COUNT(*) as count
                    FROM commands
                    WHERE timestamp > ? AND success = 1
                    GROUP BY intent
                    ORDER BY count DESC
                ''', (cutoff,))
                return dict(cursor.fetchall())
        except Exception as e:
            logger.error(f"Error getting frequency: {e}")
            return {}
    
    def get_most_traded_symbols(self, hours: int = 24, limit: int = 10) -> List[str]:
        """Get symbols user trades most"""
        try:
            cutoff = datetime.now() - timedelta(hours=hours)
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute('''
                    SELECT symbol, COUNT(*) as count
                    FROM commands
                    WHERE timestamp > ? AND symbol IS NOT NULL AND success = 1
                    GROUP BY symbol
                    ORDER BY count DESC
                    LIMIT ?
                ''', (cutoff, limit))
                return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting symbols: {e}")
            return []
    
    def get_success_rate(self, hours: int = 24) -> float:
        """Get command success rate"""
        try:
            cutoff = datetime.now() - timedelta(hours=hours)
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute('''
                    SELECT 
                        CAST(SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as float) / 
                        COUNT(*) * 100 as success_rate
                    FROM commands
                    WHERE timestamp > ?
                ''', (cutoff,))
                result = cursor.fetchone()
                return result[0] if result[0] is not None else 0
        except Exception as e:
            logger.error(f"Error getting success rate: {e}")
            return 0
    
    def get_peak_usage_times(self, hours: int = 24) -> List[str]:
        """Get peak usage times"""
        try:
            cutoff = datetime.now() - timedelta(hours=hours)
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute('''
                    SELECT strftime('%H:00', timestamp) as hour, COUNT(*) as count
                    FROM commands
                    WHERE timestamp > ?
                    GROUP BY hour
                    ORDER BY count DESC
                    LIMIT 5
                ''', (cutoff,))
                return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting peak times: {e}")
            return []
    
    def set_preference(self, key: str, value: str):
        """Store user preference"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    INSERT OR REPLACE INTO user_preferences (preference_key, value, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                ''', (key, value))
                conn.commit()
        except Exception as e:
            logger.error(f"Error setting preference: {e}")
    
    def get_preference(self, key: str) -> Optional[str]:
        """Get user preference"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    'SELECT value FROM user_preferences WHERE preference_key = ?', (key,)
                )
                result = cursor.fetchone()
                return result[0] if result else None
        except Exception as e:
            logger.error(f"Error getting preference: {e}")
            return None
    
    def generate_insights(self) -> Dict:
        """Generate learning insights"""
        insights = {
            "timestamp": datetime.now().isoformat(),
            "command_frequency": self.get_command_frequency(),
            "top_symbols": self.get_most_traded_symbols(),
            "success_rate": self.get_success_rate(),
            "peak_times": self.get_peak_usage_times(),
            "total_commands": self._get_total_commands(),
        }
        return insights
    
    def _get_total_commands(self) -> int:
        """Get total number of commands logged"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute('SELECT COUNT(*) FROM commands')
                return cursor.fetchone()[0]
        except:
            return 0
    
    def get_learning_report(self) -> str:
        """Generate human-readable learning report"""
        insights = self.generate_insights()
        
        report = "📊 JARVIS Learning Report\n"
        report += "=" * 50 + "\n\n"
        
        report += f"📈 Total Commands: {insights['total_commands']}\n"
        report += f"✅ Success Rate: {insights['success_rate']:.1f}%\n\n"
        
        report += "🎯 Top Commands:\n"
        for intent, count in list(insights['command_frequency'].items())[:5]:
            report += f"  • {intent}: {count} times\n"
        
        report += "\n💰 Top Symbols:\n"
        for symbol in insights['top_symbols'][:5]:
            report += f"  • {symbol}\n"
        
        report += "\n⏰ Peak Usage Times:\n"
        for time in insights['peak_times']:
            report += f"  • {time}\n"
        
        return report

    def get_learning_context(self, hours: int = 72, limit: int = 5) -> Dict:
        """
        Build a compact learning context for the voice layer.

        This keeps voice interpretation aligned with the user's actual command
        history without changing any existing trade or learning records.
        """
        try:
            cutoff = datetime.now() - timedelta(hours=hours)
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row

                top_commands_cursor = conn.execute(
                    '''
                    SELECT intent, COUNT(*) AS count
                    FROM commands
                    WHERE timestamp > ? AND success = 1
                    GROUP BY intent
                    ORDER BY count DESC, intent ASC
                    LIMIT ?
                    ''',
                    (cutoff, limit)
                )
                top_commands = [
                    {'intent': row['intent'], 'count': int(row['count'] or 0)}
                    for row in top_commands_cursor.fetchall()
                ]

                top_symbols_cursor = conn.execute(
                    '''
                    SELECT symbol, COUNT(*) AS count
                    FROM commands
                    WHERE timestamp > ? AND success = 1 AND symbol IS NOT NULL AND TRIM(symbol) != ''
                    GROUP BY symbol
                    ORDER BY count DESC, symbol ASC
                    LIMIT ?
                    ''',
                    (cutoff, limit)
                )
                top_symbols = [
                    {'symbol': row['symbol'], 'count': int(row['count'] or 0)}
                    for row in top_symbols_cursor.fetchall()
                ]

                phrase_cursor = conn.execute(
                    '''
                    SELECT intent, user_input, COUNT(*) AS count
                    FROM commands
                    WHERE timestamp > ? AND success = 1 AND user_input IS NOT NULL AND TRIM(user_input) != ''
                    GROUP BY intent, user_input
                    ORDER BY intent ASC, count DESC, user_input ASC
                    ''',
                    (cutoff,)
                )
                intent_phrases: Dict[str, List[str]] = {}
                for row in phrase_cursor.fetchall():
                    intent = str(row['intent'] or '').strip()
                    phrase = str(row['user_input'] or '').strip()
                    if not intent or not phrase:
                        continue
                    bucket = intent_phrases.setdefault(intent, [])
                    if phrase not in bucket and len(bucket) < limit:
                        bucket.append(phrase)

            preferred_symbol = top_symbols[0]['symbol'] if top_symbols else None
            preferred_intent = top_commands[0]['intent'] if top_commands else None

            return {
                'hours': hours,
                'total_commands': self._get_total_commands(),
                'success_rate': self.get_success_rate(hours=hours),
                'peak_times': self.get_peak_usage_times(hours=hours),
                'top_commands': top_commands,
                'top_symbols': top_symbols,
                'preferred_symbol': preferred_symbol,
                'preferred_intent': preferred_intent,
                'intent_phrases': intent_phrases,
                'preferred_phrases': {
                    intent: phrases[:limit]
                    for intent, phrases in intent_phrases.items()
                },
            }
        except Exception as e:
            logger.error(f"Error building learning context: {e}")
            return {
                'hours': hours,
                'total_commands': self._get_total_commands(),
                'success_rate': self.get_success_rate(hours=hours),
                'peak_times': self.get_peak_usage_times(hours=hours),
                'top_commands': [],
                'top_symbols': [],
                'preferred_symbol': None,
                'preferred_intent': None,
                'intent_phrases': {},
                'preferred_phrases': {},
            }

# Global learner
command_learner = None

def get_command_learner():
    """Get or create command learner"""
    global command_learner
    if command_learner is None:
        command_learner = CommandLearner()
    return command_learner
