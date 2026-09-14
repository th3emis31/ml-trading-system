from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
import re


class VoiceCommandService:
    """Simple wakeword + command parser with persisted settings."""

    def __init__(self, state_path: Path, wakeword: str = "hey jarvis"):
        self._state_path = Path(state_path)
        self._lock = threading.Lock()
        self._state = {
            "enabled": True,
            "wakeword": wakeword,
            "last_event": None,
        }
        self._load_state()

    def _load_state(self):
        if not self._state_path.exists():
            return
        try:
            loaded = json.loads(self._state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self._state.update(loaded)
        except Exception:
            return

    def _save_state(self):
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps(self._state, indent=2), encoding="utf-8")

    def _now(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def _normalize_text(self, text: str) -> str:
        clean = str(text or "").lower()
        clean = re.sub(r"[^\w\s]", " ", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean

    def status(self) -> dict:
        with self._lock:
            return {
                "enabled": bool(self._state.get("enabled", True)),
                "wakeword": str(self._state.get("wakeword") or "hey jarvis"),
                "last_event": self._state.get("last_event"),
            }

    def configure(self, enabled: bool | None = None, wakeword: str | None = None) -> dict:
        with self._lock:
            if enabled is not None:
                self._state["enabled"] = bool(enabled)
            if wakeword is not None:
                clean = str(wakeword).strip().lower()
                if clean:
                    self._state["wakeword"] = clean
            self._state["last_event"] = {
                "type": "config_update",
                "at": self._now(),
            }
            self._save_state()
            return self.status()

    def process_text(self, transcript: str) -> dict:
        raw_text = str(transcript or "").strip()
        text = self._normalize_text(raw_text)
        with self._lock:
            enabled = bool(self._state.get("enabled", True))
            wakeword = self._normalize_text(self._state.get("wakeword") or "hey jarvis")

            if not raw_text:
                return {
                    "accepted": False,
                    "reason": "empty_transcript",
                    "wakeword": wakeword,
                }

            if not enabled:
                return {
                    "accepted": False,
                    "reason": "voice_disabled",
                    "wakeword": wakeword,
                }

            has_wakeword = wakeword in text or text.startswith("jarvis ")
            command_text = text.replace(wakeword, "").strip() if wakeword in text else text
            command_text = re.sub(r"^(hey|yo|ok|okay|please)\s+", "", command_text).strip()

            direct_command_starts = (
                "stop",
                "start",
                "begin",
                "buy",
                "sell",
                "analyze",
                "analysis",
                "market",
                "trend",
                "price",
                "predict",
                "forecast",
                "risk",
                "recommend",
                "account",
                "balance",
                "performance",
                "status",
                "help",
                "learn",
                "update",
                "trade",
            )
            is_direct_command = any(
                text == prefix or text.startswith(prefix + " ")
                for prefix in direct_command_starts
            )

            if not has_wakeword:
                if is_direct_command:
                    has_wakeword = True
                    command_text = text
                else:
                    return {
                        "accepted": False,
                        "reason": "wakeword_missing",
                        "wakeword": wakeword,
                    }

            if not command_text:
                command_text = text

            intent = "general"
            action = "respond"
            symbol = "XAUUSD" if "xau" in command_text or "gold" in command_text else "BTCUSD" if "btc" in command_text or "bitcoin" in command_text else "EURUSD" if "eur" in command_text or "euro" in command_text else "GBPUSD" if "gbp" in command_text or "pound" in command_text else None

            # Direct stop commands should work immediately, even without wakeword.
            if command_text == "stop" or command_text.startswith("stop "):
                intent = "stop_trade"
                action = "stop_auto_trade"
            # Trading commands
            elif (
                "prepare trade" in command_text
                or "start trade" in command_text
                or "begin trading" in command_text
                or "execute trade" in command_text
                or "open trade" in command_text
                or "open a trade" in command_text
                or "open position" in command_text
                or "open a position" in command_text
                or "place trade" in command_text
                or "place order" in command_text
                or "trade now" in command_text
                or "enter trade" in command_text
                or "prepare position" in command_text
                or "prepare order" in command_text
                or ((command_text.startswith("buy ") or command_text.startswith("sell ")) and symbol)
            ):
                intent = "start_trade"
                action = "start_auto_trade"
            elif "quick trade" in command_text or "scalp" in command_text:
                intent = "quick_trade"
                action = "execute_quick_trade"
            elif "buy signal" in command_text or "generate signal" in command_text:
                intent = "buy_signal"
                action = "generate_buy_signal"
            elif "stop trade" in command_text or "halt trading" in command_text or "stop all" in command_text:
                intent = "stop_trade"
                action = "stop_auto_trade"
            
            # Chart and technical analysis commands
            elif "analyze chart" in command_text or "chart analysis" in command_text or "read chart" in command_text:
                intent = "analyze_chart"
                action = "run_chart_analysis"
            elif "market trend" in command_text or "trend analysis" in command_text or "what is trend" in command_text:
                intent = "market_trend"
                action = "analyze_market_trend"
            elif "price prediction" in command_text or "predict price" in command_text or "forecast" in command_text:
                intent = "price_prediction"
                action = "generate_prediction"
            elif "risk assessment" in command_text or "risk check" in command_text or "what is risk" in command_text:
                intent = "risk_assessment"
                action = "assess_risk"
            
            # AI and learning commands
            elif "recommendation" in command_text or "recommend" in command_text or "best trade" in command_text:
                intent = "recommendations"
                action = "get_recommendations"
            elif "ai insight" in command_text or "ai analysis" in command_text or "artificial intelligence" in command_text:
                intent = "ai_insights"
                action = "get_ai_insights"
            elif "start learning" in command_text or "check learning" in command_text or "review learning" in command_text:
                intent = "check_learning"
                action = "check_learning_status"
            elif "start update" in command_text or "improve system" in command_text or "what needs improvement" in command_text or "system update" in command_text:
                intent = "system_update"
                action = "check_improvement_areas"
            
            # Account and system commands
            elif "signal" in command_text or "setup" in command_text:
                intent = "signal_check"
                action = "load_signals"
            elif "risk" in command_text or "position size" in command_text:
                intent = "risk_review"
                action = "open_risk_panel"
            elif "mt5" in command_text or "metatrader" in command_text or "check status" in command_text or "system status" in command_text:
                intent = "system_status"
                action = "check_system_status"
            elif "balance" in command_text or "account info" in command_text or "account" in command_text or "equity" in command_text:
                intent = "account_info"
                action = "get_account_info"
            elif "performance" in command_text or "pnl" in command_text or "profit" in command_text or "how much" in command_text:
                intent = "performance_check"
                action = "check_performance"
            elif "help" in command_text or "what can you do" in command_text or "command" in command_text:
                intent = "help"
                action = "show_help"

            event = {
                "type": "voice_command",
                "at": self._now(),
                "transcript": raw_text,
                "intent": intent,
                "symbol": symbol,
            }
            self._state["last_event"] = event
            self._save_state()

            return {
                "accepted": True,
                "wakeword": wakeword,
                "command": command_text,
                "intent": intent,
                "action": action,
                "symbol": symbol,
                "event": event,
            }
