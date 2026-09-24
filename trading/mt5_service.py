from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from time import monotonic


class MT5Service:
    """Optional MetaTrader5 integration with safe fallbacks when unavailable."""

    def __init__(self):
        self._mt5 = None
        self._connected = False
        self._last_error = "MetaTrader5 package not imported yet"
        self._load_client()

    def _load_client(self):
        try:
            import MetaTrader5 as mt5  # type: ignore

            self._mt5 = mt5
            self._last_error = "ready"
        except Exception as exc:
            self._mt5 = None
            self._last_error = f"MetaTrader5 unavailable: {exc}"

    def _now(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def _env_login(self):
        login = os.getenv("MT5_LOGIN", "").strip()
        password = os.getenv("MT5_PASSWORD", "").strip()
        server = os.getenv("MT5_SERVER", "").strip()
        path = os.getenv("MT5_PATH", "").strip()
        return login, password, server, path

    def connect(self) -> bool:
        if self._mt5 is None:
            self._connected = False
            return False

        try:
            login, password, server, path = self._env_login()
            initialized = self._mt5.initialize(path=path) if path else self._mt5.initialize()
            if not initialized:
                self._connected = False
                self._last_error = f"initialize failed: {self._mt5.last_error()}"
                return False

            if login and password and server:
                ok = self._mt5.login(int(login), password=password, server=server)
                self._connected = bool(ok)
                if not ok:
                    self._last_error = f"login failed: {self._mt5.last_error()}"
                    return False
            else:
                self._connected = True

            self._last_error = "connected"
            return True
        except Exception as exc:
            self._connected = False
            self._last_error = str(exc)
            return False

    def status(self) -> dict:
        if self._mt5 is None:
            return {
                "available": False,
                "connected": False,
                "message": self._last_error,
                "at": self._now(),
            }

        connected = self._connected or self.connect()
        return {
            "available": True,
            "connected": bool(connected),
            "message": self._last_error,
            "at": self._now(),
        }

    def account_info(self) -> dict:
        status = self.status()
        if not status["connected"]:
            return {
                "status": status,
                "account": None,
                "summary": {
                    "open_positions": 0,
                    "closed_trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "win_rate": 0.0,
                    "net_profit": 0.0,
                },
            }

        try:
            account = self._mt5.account_info()
            if account is None:
                return {
                    "status": self.status(),
                    "account": None,
                    "summary": {
                        "open_positions": 0,
                        "closed_trades": 0,
                        "wins": 0,
                        "losses": 0,
                        "win_rate": 0.0,
                        "net_profit": 0.0,
                    },
                }
            account_dict = account._asdict() if hasattr(account, "_asdict") else {
                "login": getattr(account, "login", None),
                "server": getattr(account, "server", None),
                "company": getattr(account, "company", None),
                "balance": getattr(account, "balance", None),
                "equity": getattr(account, "equity", None),
                "margin": getattr(account, "margin", None),
                "margin_free": getattr(account, "margin_free", None),
                "margin_level": getattr(account, "margin_level", None),
                "currency": getattr(account, "currency", None),
                "name": getattr(account, "name", None),
                "trade_mode": getattr(account, "trade_mode", None),
            }
            summary = self.trading_summary(days=365)
            return {
                "status": self.status(),
                "account": account_dict,
                "summary": summary,
            }
        except Exception as exc:
            return {
                "status": self.status(),
                "account": None,
                "error": str(exc),
                "summary": {
                    "open_positions": 0,
                    "closed_trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "win_rate": 0.0,
                    "net_profit": 0.0,
                },
            }

    def trading_summary(self, days: int = 365) -> dict:
        status = self.status()
        if not status["connected"] or self._mt5 is None:
            return {
                "open_positions": 0,
                "closed_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "net_profit": 0.0,
            }

        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            start = now - timedelta(days=max(1, int(days or 365)))
            open_positions_raw = self._mt5.positions_get() or []
            deals = self._mt5.history_deals_get(start, now) or []

            open_positions = []
            for pos in list(open_positions_raw)[:20]:
                pos_dict = pos._asdict() if hasattr(pos, "_asdict") else {
                    "ticket": getattr(pos, "ticket", None),
                    "symbol": getattr(pos, "symbol", None),
                    "type": getattr(pos, "type", None),
                    "volume": getattr(pos, "volume", None),
                    "price_open": getattr(pos, "price_open", None),
                    "price_current": getattr(pos, "price_current", None),
                    "sl": getattr(pos, "sl", None),
                    "tp": getattr(pos, "tp", None),
                    "profit": getattr(pos, "profit", None),
                    "time": getattr(pos, "time", None),
                    "comment": getattr(pos, "comment", None),
                }
                pos_type = int(pos_dict.get("type") or 0)
                pos_dict["direction"] = "BUY" if pos_type == 0 else "SELL" if pos_type == 1 else str(pos_type)
                open_positions.append(pos_dict)

            grouped: dict[str, float] = {}
            for deal in list(deals):
                deal_dict = deal._asdict() if hasattr(deal, "_asdict") else {
                    "position_id": getattr(deal, "position_id", None),
                    "order": getattr(deal, "order", None),
                    "profit": getattr(deal, "profit", 0.0),
                    "swap": getattr(deal, "swap", 0.0),
                    "commission": getattr(deal, "commission", 0.0),
                }
                key = deal_dict.get("position_id") or deal_dict.get("order")
                key = str(key or deal_dict.get("ticket") or id(deal))
                net_profit = float(deal_dict.get("profit") or 0.0) + float(deal_dict.get("swap") or 0.0) + float(deal_dict.get("commission") or 0.0)
                grouped[key] = grouped.get(key, 0.0) + net_profit

            closed_trades = len(grouped)
            wins = sum(1 for value in grouped.values() if value > 0)
            losses = sum(1 for value in grouped.values() if value <= 0)
            net_profit = round(sum(grouped.values()), 2)
            win_rate = round((wins / max(1, closed_trades)) * 100.0, 1) if closed_trades else 0.0

            return {
                "open_positions": len(open_positions),
                "open_positions_detail": open_positions,
                "closed_trades": closed_trades,
                "wins": wins,
                "losses": losses,
                "win_rate": win_rate,
                "net_profit": net_profit,
            }
        except Exception:
            return {
                "open_positions": 0,
                "closed_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "net_profit": 0.0,
            }

    def quote(self, symbol: str) -> dict:
        """Live bid/ask for one symbol from the connected terminal."""
        status = self.status()
        if not status["connected"] or self._mt5 is None:
            return {"ok": False, "message": "MT5 is not connected"}
        try:
            name = str(symbol or "").upper().strip()
            info = self._mt5.symbol_info(name)
            if info is None:
                return {"ok": False, "message": f"symbol not found: {name}"}
            if not getattr(info, "visible", False):
                self._mt5.symbol_select(name, True)
            tick = self._mt5.symbol_info_tick(name)
            if tick is None:
                return {"ok": False, "message": f"no market tick for {name}"}
            return {
                "ok": True,
                "symbol": name,
                "bid": float(tick.bid),
                "ask": float(tick.ask),
                "time": int(getattr(tick, "time", 0) or 0),
                "digits": int(getattr(info, "digits", 2) or 2),
                "volume_min": float(getattr(info, "volume_min", 0.01) or 0.01),
            }
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def positions(self, symbol: str | None = None, magic=None) -> list[dict] | None:
        """Open positions, optionally for one symbol and/or magic number(s). None when they cannot be read.

        ``magic`` takes one number or several. Several matters when a component's magic changes: the
        positions it opened under the old number are still its own and must stay visible to it, or the
        one-trade-per-asset guard would look for the new magic, see nothing, and open a second trade
        on a symbol that already has one.
        """
        if not self.status()["connected"] or self._mt5 is None:
            return None
        try:
            raw = self._mt5.positions_get(symbol=str(symbol).upper()) if symbol else self._mt5.positions_get()
        except Exception:
            return None
        if raw is None:
            return None
        found = []
        for pos in list(raw):
            item = pos._asdict() if hasattr(pos, "_asdict") else {
                key: getattr(pos, key, None)
                for key in ("ticket", "symbol", "type", "volume", "price_open", "price_current", "sl", "tp",
                            "profit", "time", "magic", "comment")
            }
            if magic is not None:
                wanted = {int(m) for m in (magic if isinstance(magic, (list, tuple, set)) else [magic])}
                if int(item.get("magic") or 0) not in wanted:
                    continue
            item["direction"] = "BUY" if int(item.get("type") or 0) == 0 else "SELL"
            found.append(item)
        return found

    def close_position(self, ticket: int, comment: str = "AI close") -> dict:
        """Close one open position by ticket at market."""
        status = self.status()
        if not status["connected"] or self._mt5 is None:
            return {"ok": False, "executed": False, "message": "MT5 is not connected"}
        try:
            open_positions = self._mt5.positions_get(ticket=int(ticket))
            if not open_positions:
                return {"ok": False, "executed": False, "message": f"position {ticket} is not open"}
            pos = open_positions[0]
            tick = self._mt5.symbol_info_tick(pos.symbol)
            if tick is None:
                return {"ok": False, "executed": False, "message": f"no market tick for {pos.symbol}"}
            closing_buy = int(pos.type) == int(self._mt5.POSITION_TYPE_BUY)
            request_payload = {
                "action": self._mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": float(pos.volume),
                "type": self._mt5.ORDER_TYPE_SELL if closing_buy else self._mt5.ORDER_TYPE_BUY,
                "position": int(pos.ticket),
                "price": float(tick.bid if closing_buy else tick.ask),
                "deviation": 25,
                "magic": int(getattr(pos, "magic", 0) or 0),
                "comment": str(comment or "AI close")[:31],
                "type_time": self._mt5.ORDER_TIME_GTC,
                "type_filling": self._mt5.ORDER_FILLING_IOC,
            }
            result = self._mt5.order_send(request_payload)
            if result is None:
                return {"ok": False, "executed": False, "message": f"order_send returned None: {self._mt5.last_error()}"}
            result_dict = result._asdict() if hasattr(result, "_asdict") else {
                "retcode": getattr(result, "retcode", None),
                "comment": getattr(result, "comment", ""),
            }
            executed = int(result_dict.get("retcode") or 0) == int(getattr(self._mt5, "TRADE_RETCODE_DONE", 10009))
            return {
                "ok": executed,
                "executed": executed,
                "message": str(result_dict.get("comment") or ("closed" if executed else "close rejected")),
                "request": request_payload,
                "result": result_dict,
            }
        except Exception as exc:
            return {"ok": False, "executed": False, "message": str(exc)}

    def account_snapshot(self) -> dict | None:
        """Login, server, trade_mode, balance and equity only (account_info() also builds a year of trade summary)."""
        if not self.status()["connected"] or self._mt5 is None:
            return None
        try:
            account = self._mt5.account_info()
        except Exception:
            return None
        if account is None:
            return None
        return {key: getattr(account, key, None)
                for key in ("login", "server", "trade_mode", "balance", "equity", "currency", "company")}

    def modify_position_sltp(self, ticket: int, stop_loss: float | None = None, take_profit: float | None = None) -> dict:
        """Change the stop and/or target of one open position; a value left as None keeps the current one."""
        if not self.status()["connected"] or self._mt5 is None:
            return {"ok": False, "executed": False, "message": "MT5 is not connected"}
        try:
            open_positions = self._mt5.positions_get(ticket=int(ticket))
            if not open_positions:
                return {"ok": False, "executed": False, "message": f"position {ticket} is not open"}
            pos = open_positions[0]
            request_payload = {
                "action": self._mt5.TRADE_ACTION_SLTP,
                "position": int(pos.ticket),
                "symbol": pos.symbol,
                "sl": float(stop_loss if stop_loss is not None else pos.sl),
                "tp": float(take_profit if take_profit is not None else pos.tp),
                "magic": int(getattr(pos, "magic", 0) or 0),
            }
            result = self._mt5.order_send(request_payload)
            if result is None:
                return {"ok": False, "executed": False, "message": f"order_send returned None: {self._mt5.last_error()}"}
            result_dict = result._asdict() if hasattr(result, "_asdict") else {
                "retcode": getattr(result, "retcode", None), "comment": getattr(result, "comment", "")}
            executed = int(result_dict.get("retcode") or 0) == int(getattr(self._mt5, "TRADE_RETCODE_DONE", 10009))
            return {"ok": executed, "executed": executed,
                    "message": str(result_dict.get("comment") or ("modified" if executed else "modify rejected")),
                    "request": request_payload, "result": result_dict}
        except Exception as exc:
            return {"ok": False, "executed": False, "message": str(exc)}

    def deal_history(self, days: int = 3650) -> dict:
        """Closed trades (exit deals) from the account history, times converted to UTC. Read-only."""
        status = self.status()
        if not status["connected"] or self._mt5 is None:
            return {"ok": False, "reason": "MT5 is not connected", "deals": []}
        try:
            offset_hours = self.server_utc_offset_hours()
            end = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)
            start = end - timedelta(days=max(1, int(days or 3650)))
            raw = self._mt5.history_deals_get(start, end) or []
            exit_entries = {
                int(getattr(self._mt5, "DEAL_ENTRY_OUT", 1)),
                int(getattr(self._mt5, "DEAL_ENTRY_INOUT", 2)),
                int(getattr(self._mt5, "DEAL_ENTRY_OUT_BY", 3)),
            }
            deals = []
            for deal in list(raw):
                item = deal._asdict() if hasattr(deal, "_asdict") else {
                    key: getattr(deal, key, None)
                    for key in ("ticket", "position_id", "time", "type", "entry", "symbol", "volume", "price",
                                "profit", "swap", "commission", "fee", "magic", "comment")
                }
                if int(item.get("entry") or 0) not in exit_entries or int(item.get("type") or 0) not in (0, 1):
                    continue  # entries, balance and credit operations are not closed trades
                profit = float(item.get("profit") or 0.0)
                swap = float(item.get("swap") or 0.0)
                commission = float(item.get("commission") or 0.0)
                fee = float(item.get("fee") or 0.0)
                deals.append({
                    "ticket": int(item.get("ticket") or 0),
                    "position_id": int(item.get("position_id") or 0),
                    "time": int(item.get("time") or 0) - offset_hours * 3600,  # converted to UTC
                    "symbol": str(item.get("symbol") or ""),
                    "type": int(item.get("type") or 0),
                    "volume": float(item.get("volume") or 0.0),
                    "price": float(item.get("price") or 0.0),
                    "profit": profit,
                    "swap": swap,
                    "commission": commission,
                    "fee": fee,
                    "net": round(profit + swap + commission + fee, 2),
                    "magic": int(item.get("magic") or 0),
                    "comment": str(item.get("comment") or ""),
                })
            account = self._mt5.account_info()
            return {"ok": True, "deals": deals, "server_utc_offset_hours": offset_hours,
                    "login": getattr(account, "login", None), "currency": getattr(account, "currency", None)}
        except Exception as exc:
            return {"ok": False, "reason": str(exc), "deals": []}

    def market_snapshot(self, symbols: list[str]) -> list[dict]:
        status = self.status()
        normalized = [str(symbol or "").upper() for symbol in symbols if str(symbol or "").strip()]
        if not normalized:
            normalized = ["XAUUSD", "BTCUSD"]

        if not status["connected"]:
            return []

        rows = []
        for symbol in normalized:
            try:
                tick = self._mt5.symbol_info_tick(symbol)
                if tick is None:
                    continue
                rows.append(
                    {
                        "symbol": symbol,
                        "bid": float(getattr(tick, "bid", 0.0) or 0.0),
                        "ask": float(getattr(tick, "ask", 0.0) or 0.0),
                        "last": float(getattr(tick, "last", 0.0) or 0.0),
                        "time": int(getattr(tick, "time", 0) or 0),
                        "source": "mt5",
                    }
                )
            except Exception:
                continue
        return rows

    def check_symbol(self, symbol: str) -> dict:
        status = self.status()
        normalized_symbol = str(symbol or "").upper().strip()
        if not status["connected"]:
            return {
                "symbol": normalized_symbol,
                "valid": False,
                "reason": "MT5 not connected",
            }

        if self._mt5 is None:
            return {
                "symbol": normalized_symbol,
                "valid": False,
                "reason": "MetaTrader5 module unavailable",
            }

        try:
            info = self._mt5.symbol_info(normalized_symbol)
            if info is None:
                return {
                    "symbol": normalized_symbol,
                    "valid": False,
                    "reason": f"Symbol not found on broker",
                }

            visible = getattr(info, "visible", False)
            if not visible:
                self._mt5.symbol_select(normalized_symbol, True)

            tick = self._mt5.symbol_info_tick(normalized_symbol)
            if tick is None:
                return {
                    "symbol": normalized_symbol,
                    "valid": False,
                    "reason": "No market tick available (market closed?)",
                }

            return {
                "symbol": normalized_symbol,
                "valid": True,
                "bid": float(getattr(tick, "bid", 0.0) or 0.0),
                "ask": float(getattr(tick, "ask", 0.0) or 0.0),
                "spread": float(getattr(tick, "ask", 0.0) or 0.0) - float(getattr(tick, "bid", 0.0) or 0.0),
            }
        except Exception as exc:
            return {
                "symbol": normalized_symbol,
                "valid": False,
                "reason": str(exc),
            }

    def server_utc_offset_hours(self) -> int:
        """Broker server clock minus UTC, in whole hours.

        MT5 stamps ticks and bars in the broker's server time (often UTC+2/+3), so
        bar times read as UTC looked up to three hours in the future. Measured from
        the latest tick of a symbol that trades around the clock; cached for an hour.
        """
        cached = getattr(self, "_utc_offset_cache", None)
        if cached and monotonic() - cached[1] < 3600:
            return cached[0]
        hours = 0
        if self._mt5 is not None:
            for probe in ("BTCUSD", "ETHUSD", "XAUUSD"):
                try:
                    tick = self._mt5.symbol_info_tick(probe)
                except Exception:
                    tick = None
                tick_time = float(getattr(tick, "time", 0) or 0) if tick is not None else 0.0
                if not tick_time:
                    continue
                diff = tick_time - datetime.now(timezone.utc).timestamp()
                # Only trust a fresh tick: within 15h and within 15 minutes of a whole-hour offset.
                if abs(diff) < 15 * 3600 and abs(diff - round(diff / 3600) * 3600) < 900:
                    hours = int(round(diff / 3600))
                    break
        self._utc_offset_cache = (hours, monotonic())
        return hours

    _TIMEFRAME_NAMES = {"1m": "TIMEFRAME_M1", "5m": "TIMEFRAME_M5", "15m": "TIMEFRAME_M15",
                        "1h": "TIMEFRAME_H1", "4h": "TIMEFRAME_H4", "1d": "TIMEFRAME_D1"}
    MAX_RATES = 50000

    def copy_rates(self, symbol: str, timeframe: str, count: int = 600) -> dict:
        """Read-only historical bars through this process's own MT5 connection.

        The data-feed page and the research scripts go through the app for broker
        candles. Opening a second MetaTrader5 connection from another process while
        the app is connected restarted the app once, so that path is avoided.
        MT5 bar times follow the broker's server clock.
        """
        status = self.status()
        normalized_symbol = str(symbol or "").upper().strip()
        if not status["connected"] or self._mt5 is None:
            return {"ok": False, "symbol": normalized_symbol, "reason": "MT5 not connected", "bars": []}
        constant = self._TIMEFRAME_NAMES.get(str(timeframe))
        if constant is None:
            return {"ok": False, "symbol": normalized_symbol, "reason": f"unsupported timeframe {timeframe}", "bars": []}
        count = max(1, min(int(count or 600), self.MAX_RATES))
        try:
            info = self._mt5.symbol_info(normalized_symbol)
            if info is None:
                return {"ok": False, "symbol": normalized_symbol, "reason": "Symbol not found on broker", "bars": []}
            if not getattr(info, "visible", False):
                self._mt5.symbol_select(normalized_symbol, True)
            rates = self._mt5.copy_rates_from_pos(normalized_symbol, getattr(self._mt5, constant), 0, count)
            if rates is None or len(rates) == 0:
                return {"ok": False, "symbol": normalized_symbol, "reason": f"no bars: {self._mt5.last_error()}", "bars": []}
            offset_hours = self.server_utc_offset_hours()
            bars = [{
                "time": int(row["time"]) - offset_hours * 3600,  # converted to UTC
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["tick_volume"]),
                "spread": int(row["spread"]),
            } for row in rates]
            return {"ok": True, "symbol": normalized_symbol, "timeframe": timeframe, "bars": bars,
                    "clock": "UTC", "server_utc_offset_hours": offset_hours}
        except Exception as exc:
            return {"ok": False, "symbol": normalized_symbol, "reason": str(exc), "bars": []}

    def copy_rates_range(self, symbol: str, timeframe: str, start, end) -> dict:
        """Read-only bars between two UTC instants, so research can page through deep history.

        ``copy_rates`` can only walk back from the newest bar and stops at ``MAX_RATES``, which on
        a one-minute chart is about 35 trading days. Minute strategies need more than that, and one
        enormous response would strain the running app, so this takes a window instead and the
        caller asks for several.

        ``start`` and ``end`` are UTC (datetime or epoch seconds). MT5 wants the broker's server
        clock, so the offset is added going in and taken off the bar times coming out, exactly as
        ``copy_rates`` does.
        """
        status = self.status()
        normalized_symbol = str(symbol or "").upper().strip()
        if not status["connected"] or self._mt5 is None:
            return {"ok": False, "symbol": normalized_symbol, "reason": "MT5 not connected", "bars": []}
        constant = self._TIMEFRAME_NAMES.get(str(timeframe))
        if constant is None:
            return {"ok": False, "symbol": normalized_symbol, "reason": f"unsupported timeframe {timeframe}", "bars": []}

        def as_epoch(value) -> int:
            if isinstance(value, (int, float)):
                return int(value)
            if isinstance(value, datetime):
                moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
                return int(moment.timestamp())
            raise ValueError(f"start/end must be a datetime or epoch seconds, got {type(value).__name__}")

        try:
            start_utc, end_utc = as_epoch(start), as_epoch(end)
            if end_utc <= start_utc:
                return {"ok": False, "symbol": normalized_symbol, "reason": "end must be after start", "bars": []}
            info = self._mt5.symbol_info(normalized_symbol)
            if info is None:
                return {"ok": False, "symbol": normalized_symbol, "reason": "Symbol not found on broker", "bars": []}
            if not getattr(info, "visible", False):
                self._mt5.symbol_select(normalized_symbol, True)
            offset_hours = self.server_utc_offset_hours()
            shift = offset_hours * 3600
            rates = self._mt5.copy_rates_range(
                normalized_symbol, getattr(self._mt5, constant),
                datetime.fromtimestamp(start_utc + shift, timezone.utc).replace(tzinfo=None),
                datetime.fromtimestamp(end_utc + shift, timezone.utc).replace(tzinfo=None))
            if rates is None or len(rates) == 0:
                return {"ok": True, "symbol": normalized_symbol, "timeframe": timeframe, "bars": [],
                        "clock": "UTC", "server_utc_offset_hours": offset_hours,
                        "reason": f"no bars in that window: {self._mt5.last_error()}"}
            bars = [{
                "time": int(row["time"]) - shift,   # converted to UTC
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["tick_volume"]),
                "spread": int(row["spread"]),
            } for row in rates]
            return {"ok": True, "symbol": normalized_symbol, "timeframe": timeframe, "bars": bars,
                    "clock": "UTC", "server_utc_offset_hours": offset_hours}
        except Exception as exc:
            return {"ok": False, "symbol": normalized_symbol, "reason": str(exc), "bars": []}

    def place_market_order(
        self,
        symbol: str,
        side: str,
        volume: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        comment: str = "SmartEntry",
        magic: int = 903110,
        allow_retry_without_stops: bool = True,
    ) -> dict:
        status = self.status()
        if not status["connected"]:
            return {
                "ok": False,
                "executed": False,
                "message": "MT5 is not connected",
                "status": status,
            }

        if self._mt5 is None:
            return {
                "ok": False,
                "executed": False,
                "message": "MetaTrader5 module unavailable",
                "status": status,
            }

        normalized_symbol = str(symbol or "").upper().strip()
        normalized_side = str(side or "BUY").upper().strip()
        if normalized_side not in {"BUY", "SELL"}:
            return {
                "ok": False,
                "executed": False,
                "message": "side must be BUY or SELL",
                "status": status,
            }

        try:
            info = self._mt5.symbol_info(normalized_symbol)
            if info is None:
                return {
                    "ok": False,
                    "executed": False,
                    "message": f"symbol not found: {normalized_symbol}",
                    "status": status,
                }

            if not getattr(info, "visible", False):
                self._mt5.symbol_select(normalized_symbol, True)

            tick = self._mt5.symbol_info_tick(normalized_symbol)
            if tick is None:
                return {
                    "ok": False,
                    "executed": False,
                    "message": f"no market tick for {normalized_symbol}",
                    "status": status,
                }

            order_type = self._mt5.ORDER_TYPE_BUY if normalized_side == "BUY" else self._mt5.ORDER_TYPE_SELL
            price = float(getattr(tick, "ask" if normalized_side == "BUY" else "bid", 0.0) or 0.0)

            point = float(getattr(info, "point", 0.0) or 0.0)
            digits = int(getattr(info, "digits", 5) or 5)
            stops_level_points = int(getattr(info, "trade_stops_level", 0) or 0)
            freeze_level_points = int(getattr(info, "trade_freeze_level", 0) or 0)
            min_points = max(stops_level_points, freeze_level_points, 1)
            min_distance = (point * min_points) if point > 0 else 0.0

            def _round_price(value: float) -> float:
                return round(float(value), digits)

            def _sanitize_stops(raw_sl: float | None, raw_tp: float | None) -> tuple[float | None, float | None]:
                sl_value = float(raw_sl) if raw_sl is not None else None
                tp_value = float(raw_tp) if raw_tp is not None else None

                if normalized_side == "BUY":
                    if sl_value is not None:
                        max_sl = price - min_distance
                        sl_value = min(sl_value, max_sl)
                        if sl_value <= 0 or sl_value >= price:
                            sl_value = None
                    if tp_value is not None:
                        min_tp = price + min_distance
                        tp_value = max(tp_value, min_tp)
                        if tp_value <= price:
                            tp_value = None
                else:
                    if sl_value is not None:
                        min_sl = price + min_distance
                        sl_value = max(sl_value, min_sl)
                        if sl_value <= price:
                            sl_value = None
                    if tp_value is not None:
                        max_tp = price - min_distance
                        tp_value = min(tp_value, max_tp)
                        if tp_value <= 0 or tp_value >= price:
                            tp_value = None

                if sl_value is not None:
                    sl_value = _round_price(sl_value)
                if tp_value is not None:
                    tp_value = _round_price(tp_value)
                return sl_value, tp_value

            safe_sl, safe_tp = _sanitize_stops(stop_loss, take_profit)
            request_payload = {
                "action": self._mt5.TRADE_ACTION_DEAL,
                "symbol": normalized_symbol,
                "volume": float(volume),
                "type": order_type,
                "price": price,
                "deviation": 25,
                "magic": int(magic),
                "comment": str(comment or "SmartEntry")[:31],
                "type_time": self._mt5.ORDER_TIME_GTC,
                "type_filling": self._mt5.ORDER_FILLING_IOC,
            }
            if safe_sl is not None:
                request_payload["sl"] = float(safe_sl)
            if safe_tp is not None:
                request_payload["tp"] = float(safe_tp)

            result = self._mt5.order_send(request_payload)
            if result is None:
                return {
                    "ok": False,
                    "executed": False,
                    "message": f"order_send returned None: {self._mt5.last_error()}",
                    "status": status,
                }

            result_dict = result._asdict() if hasattr(result, "_asdict") else {
                "retcode": getattr(result, "retcode", None),
                "comment": getattr(result, "comment", ""),
                "order": getattr(result, "order", None),
                "deal": getattr(result, "deal", None),
            }
            retcode_done = getattr(self._mt5, "TRADE_RETCODE_DONE", 10009)
            retcode_invalid_stops = int(getattr(self._mt5, "TRADE_RETCODE_INVALID_STOPS", 10016))

            executed = int(result_dict.get("retcode") or 0) == int(retcode_done)

            # Some brokers reject close SL/TP distances even after adjustments.
            # Retry once without SL/TP so trade execution can still proceed.
            retried_without_stops = False
            # Automated callers pass allow_retry_without_stops=False: a position without a stop is refused instead.
            if allow_retry_without_stops and not executed and int(result_dict.get("retcode") or 0) == retcode_invalid_stops and ("sl" in request_payload or "tp" in request_payload):
                retry_payload = dict(request_payload)
                retry_payload.pop("sl", None)
                retry_payload.pop("tp", None)
                retry_result = self._mt5.order_send(retry_payload)
                if retry_result is not None:
                    retry_result_dict = retry_result._asdict() if hasattr(retry_result, "_asdict") else {
                        "retcode": getattr(retry_result, "retcode", None),
                        "comment": getattr(retry_result, "comment", ""),
                        "order": getattr(retry_result, "order", None),
                        "deal": getattr(retry_result, "deal", None),
                    }
                    retry_executed = int(retry_result_dict.get("retcode") or 0) == int(retcode_done)
                    if retry_executed:
                        result_dict = retry_result_dict
                        request_payload = retry_payload
                        executed = True
                        retried_without_stops = True

            msg = str(result_dict.get("comment") or "")
            if not executed and not msg:
                retcode_int = int(result_dict.get("retcode") or 0)
                msg = f"Order rejected (code {retcode_int}). Check: symbol valid, market hours, account balance/margin."
            if not executed and int(result_dict.get("retcode") or 0) == retcode_invalid_stops:
                msg = (
                    f"Invalid stops for broker rules. Minimum distance is about {min_points} points "
                    f"({min_distance:.{digits}f} price units)."
                )
            if executed and retried_without_stops:
                msg = "Order executed without SL/TP because broker rejected stop distances."
            return {
                "ok": bool(executed),
                "executed": bool(executed),
                "message": msg or ("executed" if executed else "failed"),
                "status": self.status(),
                "request": request_payload,
                "result": result_dict,
            }
        except Exception as exc:
            return {
                "ok": False,
                "executed": False,
                "message": str(exc),
                "status": self.status(),
            }
