from __future__ import annotations

import re
from typing import Any
from datetime import datetime
from collections import deque


class ConversationContext:
    """Tracks conversation context for coherent multi-turn responses."""
    def __init__(self, max_history: int = 10):
        self.message_history = deque(maxlen=max_history)
        self.active_symbol = None  # XAUUSD, BTCUSD, etc.
        self.conversation_topic = None  # 'analysis', 'coding', 'risk', etc.
        self.last_update = datetime.now()
    
    def add_message(self, role: str, content: str):
        """Add message to history."""
        self.message_history.append({
            'role': role,
            'content': content,
            'timestamp': datetime.now()
        })
    
    def get_context_summary(self) -> dict:
        """Get current conversation context."""
        return {
            'active_symbol': self.active_symbol,
            'topic': self.conversation_topic,
            'message_count': len(self.message_history),
            'last_10_messages': list(self.message_history)[-10:]
        }
    
    def update_active_symbol(self, symbol: str | None):
        """Update active symbol being discussed."""
        if symbol in ['XAUUSD', 'BTCUSD', 'EURUSD', 'GOLD', 'BTC']:
            self.active_symbol = symbol if symbol in ['XAUUSD', 'BTCUSD', 'EURUSD'] else (
                'XAUUSD' if symbol == 'GOLD' else 'BTCUSD'
            )
    
    def update_topic(self, topic: str | None):
        """Update conversation topic."""
        if topic in ['analysis', 'coding', 'risk', 'mql5', 'pine', 'strategy']:
            self.conversation_topic = topic


class TradingChatEngine:
    def __init__(self):
        self.context = ConversationContext()
    
    @staticmethod
    def _extract_request(text: str) -> str:
        """Strip a wrapping system prompt down to the user's actual request.

        Callers such as the voice pipeline send the whole persona prompt as the
        message. That prompt contains words like "risk", so naive keyword
        matching classified every single turn as a risk question.
        """
        marker = "user request:"
        lowered = text.lower()
        if marker in lowered:
            return text[lowered.rindex(marker) + len(marker):].strip()
        return text

    @staticmethod
    def _mentions(text: str, *words: str) -> bool:
        """Whole-word match.

        Substring matching made "hi" fire inside "this" and "which", so
        ordinary sentences were answered as greetings.
        """
        return any(re.search(rf"\b{re.escape(word)}\b", text) for word in words)

    def reply(self, message: str, market_snapshot: list[dict[str, Any]] | None = None) -> dict:
        raw = str(message or "").strip()
        text = self._extract_request(raw)
        lowered = text.lower()

        # Store user message
        self.context.add_message('user', text)

        if not text:
            return self._format_response(
                "Please type a question about setup, risk, XAUUSD, BTCUSD, MQL5, or Pine Script.",
                topic='greeting'
            )

        # Specific intents are resolved before the generic keyword branches
        # below, so a question that merely mentions risk in passing still gets
        # the analysis it asked for.
        if self._mentions(lowered, "gold", "xau", "xauusd"):
            self.context.update_active_symbol('XAUUSD')
            return self._symbol_analysis("XAUUSD", market_snapshot)

        if self._mentions(lowered, "btc", "bitcoin", "btcusd"):
            self.context.update_active_symbol('BTCUSD')
            return self._symbol_analysis("BTCUSD", market_snapshot)

        # Greeting
        if self._mentions(lowered, "hello", "hi", "hey", "yo"):
            self.context.update_topic('greeting')
            return self._format_response(
                "Ready. Ask for analysis, a trading plan, or coding help for MQL5/Pine Script.",
                topic='greeting'
            )

        # MQL5 coding
        if "mql5" in lowered:
            self.context.update_topic('mql5')
            return self._format_response(
                "## MQL5 Assistant Mode\n\n"
                "I can help with:\n"
                "- EA skeleton generation\n"
                "- Order execution logic\n"
                "- Risk management modules\n"
                "- Performance optimization\n\n"
                "**Tell me your strategy rules** (entry conditions, SL, TP, lot sizing), and I'll build a professional template.",
                format='markdown',
                topic='coding',
                symbol=self.context.active_symbol
            )

        # Pine Script coding
        if "pine" in lowered or "tradingview" in lowered:
            self.context.update_topic('pine')
            return self._format_response(
                "## Pine Script Assistant Mode\n\n"
                "I can help with:\n"
                "- Strategy templates (v5)\n"
                "- Indicator creation\n"
                "- Alert setup\n"
                "- Backtesting optimization\n\n"
                "**Describe your conditions** (entry logic, filters, timeframe) and I'll generate production-ready code.",
                format='markdown',
                topic='coding',
                symbol=self.context.active_symbol
            )

        # Risk management
        if self._mentions(lowered, "risk", "drawdown", "position", "sizing", "lot"):
            self.context.update_topic('risk')
            return self._format_response(
                "## Risk Management Guidelines\n\n"
                "**Fixed Risk Per Trade:**\n"
                "- Keep risk percentage consistent (0.5% - 1% per trade)\n\n"
                "**Risk Reward Ratio:**\n"
                "- Minimum RR >= 1.5:1\n"
                "- Optimal RR >= 2:1\n"
                "- Conservative RR >= 3:1\n\n"
                "**Correlation:**\n"
                "- Avoid stacking highly correlated entries\n"
                "- Max 2 positions in same currency pair direction\n\n"
                "**Drawdown Protection:**\n"
                "- Max daily loss: 2% account\n"
                "- Max weekly loss: 5% account",
                format='markdown',
                topic='risk'
            )

        # XAUUSD analysis
        if "gold" in lowered or "xau" in lowered:
            self.context.update_active_symbol('XAUUSD')
            return self._symbol_analysis("XAUUSD", market_snapshot)

        # BTCUSD analysis
        if "btc" in lowered:
            self.context.update_active_symbol('BTCUSD')
            return self._symbol_analysis("BTCUSD", market_snapshot)

        # Market overview / entry analysis
        if "plan" in lowered or "entry" in lowered or "analysis" in lowered:
            self.context.update_topic('analysis')
            return self._market_overview(market_snapshot)

        # Default response
        return self._format_response(
            "I can help with:\n\n"
            "**Market Analysis** - Ask for 'XAUUSD analysis' or 'BTC plan'\n"
            "**Risk Management** - Ask 'How to manage risk?'\n"
            "**Coding Help** - Ask 'Generate MQL5 strategy' or 'Pine Script template'\n\n"
            "What would you like help with?",
            format='markdown',
            topic='general'
        )


    def _format_response(self, content: str, format: str = 'markdown', topic: str = 'general', symbol: str | None = None, has_code: bool = False) -> dict:
        """Format response with metadata."""
        response = {
            'content': content,
            'format': format,
            'topic': topic,
            'symbol': symbol or self.context.active_symbol,
            'has_code': has_code,
            'context': self.context.get_context_summary()
        }
        # Store AI response
        self.context.add_message('assistant', content)
        return response

    def _symbol_analysis(self, symbol: str, market_snapshot: list[dict[str, Any]] | None) -> dict:
        """Provide detailed symbol analysis."""
        if not market_snapshot:
            return self._format_response(
                f"**{symbol} Analysis**\n\n"
                f"No recent cached signal available. Please refresh signals and ask again for updated analysis.",
                topic='analysis',
                symbol=symbol
            )
        
        match = next((item for item in market_snapshot if str(item.get("symbol", "")).upper() == symbol), None)
        if not match:
            return self._format_response(
                f"**{symbol}**\n\n{symbol} not found in current snapshot. Please check symbol availability.",
                topic='analysis',
                symbol=symbol
            )
        
        signal = match.get('signal', 'n/a')
        confidence = match.get('confidence', 'n/a')
        bias = match.get('bias', 'n/a')
        reason = match.get('reason', 'n/a')
        
        content = (
            f"## {symbol} Analysis\n\n"
            f"**Signal:** `{signal}`\n"
            f"**Confidence:** {confidence}\n"
            f"**Bias:** {bias}\n\n"
            f"**Reason:** {reason}"
        )
        
        return self._format_response(content, format='markdown', topic='analysis', symbol=symbol)


    def _market_overview(self, market_snapshot: list[dict[str, Any]] | None) -> dict:
        """Provide comprehensive market overview."""
        if not market_snapshot:
            return self._format_response(
                "**Market Overview**\n\n"
                "No cached market snapshot available. Please refresh signals for up-to-date entry guidance.",
                topic='analysis'
            )
        
        lines = ["## Current Market Overview\n"]
        for item in market_snapshot[:2]:
            symbol = item.get('symbol', 'N/A')
            signal = item.get('signal', 'n/a')
            confidence = item.get('confidence', 'n/a')
            bias = item.get('bias', 'n/a')
            lines.append(f"**{symbol}:** Signal `{signal}` | Confidence: {confidence} | Bias: {bias}")
        
        lines.append("\n\nAsk for specific symbol analysis or risk guidance for entry setup.")
        
        return self._format_response('\n'.join(lines), format='markdown', topic='analysis')
