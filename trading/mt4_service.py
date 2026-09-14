"""
MetaTrader 4 Trading Service
Handles order execution, symbol validation, and market data for MT4 platforms.
Uses ZeroMQ-based communication with MT4 terminal.
"""

import ast
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Dict, Optional, List, Any

logger = logging.getLogger(__name__)

class MT4Service:
    """
    Professional MT4 integration for order execution and market data.
    Communicates with MT4 terminal via DWX-ZeroMQ bridge or direct socket connection.
    """
    
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 32768,
        expected_account: Optional[int] = None,
        port_fallback_step: int = 10,
        port_fallback_sets: int = 4,
    ):
        """
        Initialize MT4 Service.

        Args:
            host: MT4 terminal host (localhost or remote IP)
            port: DWX command port where client sends commands (default: 32768)
            expected_account: when set, only a bridge reporting this account
                number is accepted. Several MT4 terminals can each run a bridge
                on a different port set, so without this the client may attach
                to whichever answers first and trade the wrong account.
            port_fallback_step/sets: mirror the EA's port fallback so a bridge
                that stepped past an occupied port is still discovered.
        """
        self.host = host
        self.base_port = int(port)
        self.command_port = int(port)
        self.response_port = self.command_port + 1
        self.expected_account = int(expected_account) if expected_account else None
        self.account_number = None
        self.account_info_cache = {}
        self._candidate_ports = [
            self.base_port + (index * int(port_fallback_step))
            for index in range(max(1, int(port_fallback_sets)))
        ]
        self._last_error = "not initialized"
        self._last_connect_attempt = 0.0
        self._reconnect_cooldown_sec = 5.0
        # Flask serves requests on several threads and ZeroMQ sockets are not
        # thread-safe; interleaved send/recv pairs hand one caller another's reply.
        self._io_lock = threading.RLock()
        self.available = self._check_availability()
        self.connected = False
        if self.available:
            self.connected = self._connect()
    
    def _check_availability(self) -> bool:
        """Check if MT4 Python bridge is available."""
        try:
            import zmq
            return True
        except ImportError:
            logger.warning("ZeroMQ not installed. MT4 bridge unavailable. Install: pip install pyzmq")
            return False
    
    def _close_sockets(self):
        for attr in ('cmd_socket', 'resp_socket'):
            if hasattr(self, attr):
                try:
                    getattr(self, attr).close()
                except Exception:
                    pass
        if hasattr(self, 'context'):
            try:
                self.context.term()
            except Exception:
                pass

    def _try_port(self, command_port: int) -> bool:
        """Open sockets against one port set and heartbeat it.

        The heartbeat reply carries the account number, so a bridge belonging
        to a different terminal is rejected here rather than being traded.
        """
        import zmq

        self._close_sockets()
        self.context = zmq.Context()
        self.cmd_socket = self.context.socket(zmq.PUSH)
        self.cmd_socket.setsockopt(zmq.LINGER, 0)
        self.cmd_socket.setsockopt(zmq.SNDTIMEO, 2000)
        self.cmd_socket.connect(f"tcp://{self.host}:{command_port}")

        self.resp_socket = self.context.socket(zmq.PULL)
        self.resp_socket.setsockopt(zmq.LINGER, 0)
        self.resp_socket.setsockopt(zmq.RCVTIMEO, 2000)
        self.resp_socket.connect(f"tcp://{self.host}:{command_port + 1}")

        response = self._send_command("HEARTBEAT")
        alive = bool(response) and "loud and clear" in str(response.get("response", "")).lower()
        if not alive:
            self._last_error = response.get('raw') or response.get('response') or 'bridge ping failed'
            self._close_sockets()
            return False

        account = response.get('account')
        try:
            account = int(account) if account is not None else None
        except (TypeError, ValueError):
            account = None

        if self.expected_account and account is not None and account != self.expected_account:
            self._last_error = (
                f"bridge on port {command_port} serves account {account}, "
                f"expected {self.expected_account}"
            )
            logger.warning(self._last_error)
            self._close_sockets()
            return False

        self.command_port = command_port
        self.response_port = command_port + 1
        self.account_number = account
        self.account_info_cache = {
            'account': account,
            'server': response.get('server'),
            'company': response.get('company'),
            'balance': response.get('balance'),
            'currency': response.get('currency'),
        }
        self._last_error = "connected"
        return True

    def _connect(self) -> bool:
        """Find and attach to the DWX bridge, scanning the fallback port sets."""
        self._last_connect_attempt = time.monotonic()
        if not self.available:
            return False
        errors = []
        for candidate in self._candidate_ports:
            try:
                if self._try_port(candidate):
                    logger.info(
                        "MT4 bridge connected on port %s (account %s)",
                        candidate, self.account_number,
                    )
                    return True
                errors.append(f"{candidate}: {self._last_error}")
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")
        self._last_error = "; ".join(errors) if errors else "no bridge found"
        logger.warning("MT4 bridge not found. Tried %s", self._last_error)
        return False

    def _ping(self) -> bool:
        """Check whether the existing socket is still responsive."""
        if not self.connected or not hasattr(self, 'cmd_socket') or not hasattr(self, 'resp_socket'):
            return False
        try:
            response = self._send_command("HEARTBEAT")
            ok = bool(response) and "response" in response and "loud and clear" in str(response.get("response", "")).lower()
            if not ok:
                self._last_error = response.get('raw') or response.get('response') or 'bridge ping failed'
            return ok
        except Exception as e:
            self._last_error = str(e)
            return False

    def _parse_response(self, payload: str) -> Dict[str, Any]:
        """Parse DWX payload which is commonly a Python-style dict string with single quotes."""
        text = (payload or "").strip()
        if not text:
            return {"raw": payload}
        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, dict):
                return {str(k).lstrip('_'): v for k, v in parsed.items()}
        except Exception:
            pass
        return {"raw": payload}

    def _send_command(self, message: str) -> Dict[str, Any]:
        """Send DWX command and read response from pull channel."""
        import zmq

        with self._io_lock:
            # A reply that arrives after its command timed out stays queued and
            # would be read as the answer to the next command. Drop it first.
            stale = 0
            while True:
                try:
                    self.resp_socket.recv_string(flags=zmq.NOBLOCK)
                    stale += 1
                except zmq.Again:
                    break
            if stale:
                logger.warning("Discarded %s stale MT4 bridge reply(s) before %s", stale, message.split(';')[0])
            self.cmd_socket.send_string(message)
            raw = self.resp_socket.recv_string()
        parsed = self._parse_response(raw)
        if "raw" not in parsed:
            parsed["raw"] = raw
        return parsed
    
    def status(self) -> Dict[str, Any]:
        """
        Get MT4 connection and availability status.
        
        Returns:
            {
                'at': current_datetime,
                'available': module_available,
                'connected': connection_status,
                'message': status_message,
                'host': connection_host,
                'port': command_port
            }
        """
        if self.available:
            if self.connected:
                if not self._ping():
                    self.connected = False
                    if (time.monotonic() - float(self._last_connect_attempt or 0.0)) >= self._reconnect_cooldown_sec:
                        self.connected = self._connect()
            else:
                if (time.monotonic() - float(self._last_connect_attempt or 0.0)) >= self._reconnect_cooldown_sec:
                    self.connected = self._connect()

        message = 'connected' if self.connected else (
            f"disconnected ({self._last_error})" if self.available else 'MT4 bridge unavailable (DWX-ZeroMQ not installed)'
        )

        return {
            'at': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
            'available': self.available,
            'connected': self.connected,
            'message': message,
            'host': self.host,
            'port': self.command_port,
            'ports_scanned': self._candidate_ports,
            'account': self.account_number,
            'expected_account': self.expected_account,
            'account_matches': (
                None if not self.expected_account or self.account_number is None
                else self.account_number == self.expected_account
            ),
            'terminal': self.account_info_cache.get('company'),
            'server': self.account_info_cache.get('server'),
        }
    
    def check_symbol(self, symbol: str) -> Dict[str, Any]:
        """
        Validate symbol availability on MT4 broker.
        
        Args:
            symbol: Trading pair (e.g., 'XAUUSD', 'BTCUSD')
        
        Returns:
            {
                'symbol': validated_symbol,
                'valid': bool,
                'bid': bid_price or None,
                'ask': ask_price or None,
                'spread': spread_in_decimal or None,
                'reason': error_reason if invalid
            }
        """
        if not self.connected:
            return {
                'symbol': symbol,
                'valid': False,
                'bid': None,
                'ask': None,
                'spread': None,
                'reason': 'MT4 not connected - enable DWX-ZeroMQ EA in MT4 terminal'
            }
        
        try:
            # Request symbol data from MT4
            response = self._send_command(f"RATES;{symbol.upper()}")

            bid = response.get('bid')
            ask = response.get('ask')
            if bid is None or ask is None:
                return {
                    'symbol': symbol,
                    'valid': False,
                    'bid': None,
                    'ask': None,
                    'spread': None,
                    'reason': response.get('response', response.get('raw', 'Symbol not found on MT4 broker'))
                }
            
            bid = float(bid)
            ask = float(ask)
            spread = (ask - bid) / bid if bid > 0 else 0
            
            return {
                'symbol': symbol,
                'valid': True,
                'bid': bid,
                'ask': ask,
                'spread': spread,
            }
        except Exception as e:
            logger.error(f"Symbol check failed for {symbol}: {str(e)}")
            return {
                'symbol': symbol,
                'valid': False,
                'bid': None,
                'ask': None,
                'spread': None,
                'reason': f'Error checking symbol: {str(e)}'
            }
    
    def place_market_order(
        self,
        symbol: str,
        side: str,
        volume: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        comment: str = 'AI Trade'
    ) -> Dict[str, Any]:
        """
        Execute a market order on MT4.
        
        Args:
            symbol: Trading pair (e.g., 'XAUUSD')
            side: 'BUY' or 'SELL'
            volume: Lot size (e.g., 0.01)
            stop_loss: SL price (optional)
            take_profit: TP price (optional)
            comment: Order comment/label
        
        Returns:
            {
                'executed': order_success,
                'ticket': order_ticket_id or None,
                'symbol': symbol,
                'side': side,
                'volume': volume,
                'entry': entry_price or None,
                'stop_loss': stop_loss or None,
                'take_profit': take_profit or None,
                'message': result_message,
                'timestamp': execution_time
            }
        """
        if not self.connected:
            return {
                'executed': False,
                'ticket': None,
                'symbol': symbol,
                'side': side,
                'volume': volume,
                'entry': None,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'message': 'MT4 not connected - DWX-ZeroMQ EA must be running',
                'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            }
        
        try:
            # Validate inputs
            if side.upper() not in {'BUY', 'SELL'}:
                return {
                    'executed': False,
                    'ticket': None,
                    'symbol': symbol,
                    'side': side,
                    'volume': volume,
                    'entry': None,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'message': f'Invalid side: {side}. Must be BUY or SELL.',
                    'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                }
            
            if volume <= 0:
                return {
                    'executed': False,
                    'ticket': None,
                    'symbol': symbol,
                    'side': side,
                    'volume': volume,
                    'entry': None,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'message': f'Invalid volume: {volume}. Must be > 0.',
                    'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                }
            
            # Check symbol first
            symbol_check = self.check_symbol(symbol)
            if not symbol_check.get('valid'):
                return {
                    'executed': False,
                    'ticket': None,
                    'symbol': symbol,
                    'side': side,
                    'volume': volume,
                    'entry': symbol_check.get('bid') or symbol_check.get('ask'),
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'message': symbol_check.get('reason', 'Symbol validation failed'),
                    'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                }
            
            entry_price = symbol_check.get('ask') if side.upper() == 'BUY' else symbol_check.get('bid')

            # DWX expects command format with semicolon-delimited fields.
            # For broad broker compatibility, submit market order with SL/TP in points set to 0.
            order_type = 0 if side.upper() == 'BUY' else 1
            safe_comment = (comment or 'AI Trade').replace(';', ' ')
            command = f"TRADE;OPEN;{order_type};{symbol.upper()};0;0;0;{safe_comment};{float(volume)};123456"
            response = self._send_command(command)
            raw_resp = str(response.get('raw', ''))
            ticket = response.get('ticket')
            if ticket is None:
                ticket = response.get('Ticket')
            if ticket is None and 'ticket' in raw_resp.lower():
                try:
                    digits = ''.join(ch for ch in raw_resp if ch.isdigit())
                    ticket = int(digits) if digits else None
                except Exception:
                    ticket = None

            response_text = str(response.get('response', raw_resp)).upper()
            is_success = ('ERROR' not in response_text) and (ticket is not None or 'OPEN' in response_text or 'DONE' in response_text)

            if is_success:
                return {
                    'executed': True,
                    'ticket': ticket,
                    'symbol': symbol,
                    'side': side.upper(),
                    'volume': volume,
                    'entry': entry_price,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'message': f'Order {ticket} executed successfully' if ticket else 'Order executed successfully',
                    'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                }
            else:
                return {
                    'executed': False,
                    'ticket': None,
                    'symbol': symbol,
                    'side': side.upper(),
                    'volume': volume,
                    'entry': entry_price,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'message': response.get('response', response.get('raw', 'Order execution failed')),
                    'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                }
        
        except Exception as e:
            if 'timed out' in str(e).lower() or 'resource temporarily unavailable' in str(e).lower():
                return {
                    'executed': False,
                    'ticket': None,
                    'symbol': symbol,
                    'side': side,
                    'volume': volume,
                    'entry': None,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'message': 'MT4 bridge timeout - check DWX-ZeroMQ EA in terminal',
                    'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                }
            logger.error(f"Order placement failed: {str(e)}")
            return {
                'executed': False,
                'ticket': None,
                'symbol': symbol,
                'side': side,
                'volume': volume,
                'entry': None,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'message': f'Error: {str(e)}',
                'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            }
    
    def market_snapshot(self, symbols: List[str]) -> Dict[str, Any]:
        """
        Get current market data for multiple symbols from MT4.
        
        Args:
            symbols: List of trading pairs
        
        Returns:
            {
                'symbol': {
                    'bid': bid_price,
                    'ask': ask_price,
                    'time': timestamp
                },
                ...
            }
        """
        if not self.connected:
            return {symbol: {'bid': 0, 'ask': 0, 'time': None} for symbol in symbols}
        
        try:
            snapshot = {}
            for symbol in symbols:
                response = self._send_command(f"RATES;{symbol}")
                
                snapshot[symbol] = {
                    'bid': float(response.get('bid', 0) or 0),
                    'ask': float(response.get('ask', 0) or 0),
                    'time': datetime.now(timezone.utc).isoformat()
                }
            return snapshot
        except Exception as e:
            logger.error(f"Market snapshot failed: {str(e)}")
            return {symbol: {'bid': 0, 'ask': 0, 'time': None} for symbol in symbols}
    
    def account_info(self) -> Dict[str, Any]:
        """
        Get account information from MT4.
        
        Returns:
            {
                'balance': balance,
                'equity': equity,
                'margin_used': margin_used,
                'margin_free': margin_free,
                'margin_level': margin_level,
                'connected': connection_status
            }
        """
        if not self.connected:
            return {
                'balance': 0,
                'equity': 0,
                'margin_used': 0,
                'margin_free': 0,
                'margin_level': 0,
                'connected': False,
                'message': 'MT4 not connected'
            }
        
        try:
            response = self._send_command("TRADE;GET_ACCOUNT_INFO")
            # The EA nests the figures:
            # {'_action': 'GET_ACCOUNT_INFORMATION', '_data': [{'account_balance': ..., ...}]}
            # Reading top-level keys reported a zero balance on every call.
            data = response.get('data')
            row = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else None
            if row is None:
                return {
                    'balance': 0,
                    'equity': 0,
                    'margin_used': 0,
                    'margin_free': 0,
                    'margin_level': 0,
                    'connected': True,
                    'available': False,
                    'message': str(response.get('response') or response.get('raw') or 'no account data in bridge reply'),
                }
            balance = float(row.get('account_balance', 0) or 0)
            equity = float(row.get('account_equity', 0) or 0)
            margin_free = float(row.get('account_free_margin', 0) or 0)
            margin_used = max(equity - margin_free, 0.0)
            return {
                'balance': balance,
                'equity': equity,
                'margin_used': margin_used,
                'margin_free': margin_free,
                'margin_level': (equity / margin_used * 100.0) if margin_used > 0 else 0.0,
                'profit': float(row.get('account_profit', 0) or 0),
                'leverage': row.get('account_leverage'),
                'account_name': row.get('account_name'),
                'account': response.get('account_number'),
                'connected': True,
                'available': True,
                'message': 'Account info retrieved'
            }
        except Exception as e:
            logger.error(f"Account info retrieval failed: {str(e)}")
            return {
                'balance': 0,
                'equity': 0,
                'margin_used': 0,
                'margin_free': 0,
                'margin_level': 0,
                'connected': False,
                'message': str(e)
            }
    
    def close(self):
        """Close MT4 connection."""
        try:
            if hasattr(self, 'cmd_socket'):
                self.cmd_socket.close()
            if hasattr(self, 'resp_socket'):
                self.resp_socket.close()
            if hasattr(self, 'context'):
                self.context.term()
            self.connected = False
        except:
            pass
    
    def __del__(self):
        """Cleanup on garbage collection."""
        self.close()
