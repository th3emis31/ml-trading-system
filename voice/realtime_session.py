from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(value: datetime | None = None) -> str:
    return (value or _utc_now()).isoformat()


class RealtimeVoiceSessionManager:
    """Tracks additive realtime voice sessions with safe fallback semantics."""

    def __init__(self, state_path: Path, ttl_minutes: int = 30):
        self._state_path = Path(state_path)
        self._ttl = timedelta(minutes=max(5, int(ttl_minutes)))
        self._lock = threading.Lock()
        self._sessions: dict[str, dict] = {}
        self._recent_closed: list[dict] = []
        self._load_state()

    def _load_state(self):
        if not self._state_path.exists():
            return
        try:
            loaded = json.loads(self._state_path.read_text(encoding="utf-8"))
        except ValueError:
            return
        if not isinstance(loaded, dict):
            return
        recent = loaded.get("recent_closed")
        if isinstance(recent, list):
            self._recent_closed = recent[-100:]

    def _save_state(self):
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "recent_closed": self._recent_closed[-100:],
            "saved_at": _utc_iso(),
        }
        self._state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _cleanup_expired_locked(self):
        cutoff = _utc_now() - self._ttl
        stale_ids = []
        for session_id, session in self._sessions.items():
            updated_at = session.get("updated_at")
            if isinstance(updated_at, str):
                try:
                    updated_dt = datetime.fromisoformat(updated_at)
                except ValueError:
                    updated_dt = cutoff - timedelta(seconds=1)
            else:
                updated_dt = cutoff - timedelta(seconds=1)
            if updated_dt < cutoff:
                stale_ids.append(session_id)
        for session_id in stale_ids:
            stale = self._sessions.pop(session_id, None)
            if stale:
                stale["status"] = "expired"
                stale["closed_at"] = _utc_iso()
                self._recent_closed.append(stale)
        if stale_ids:
            self._save_state()

    def start_session(self, client_id: str | None = None, mode: str = "conversation", locale: str = "en-US") -> dict:
        with self._lock:
            self._cleanup_expired_locked()
            session_id = secrets.token_urlsafe(12)
            session = {
                "session_id": session_id,
                "client_id": str(client_id or "web"),
                "mode": str(mode or "conversation"),
                "locale": str(locale or "en-US"),
                "status": "active",
                "created_at": _utc_iso(),
                "updated_at": _utc_iso(),
                "chunks": [],
                "transcript": "",
                "last_confidence": None,
                "turn_count": 0,
            }
            self._sessions[session_id] = session
            return dict(session)

    def add_chunk(
        self,
        session_id: str,
        transcript: str,
        *,
        confidence: float | None = None,
        is_final: bool = False,
    ) -> dict:
        clean = str(transcript or "").strip()
        if not clean:
            raise ValueError("transcript is required")
        with self._lock:
            self._cleanup_expired_locked()
            session = self._sessions.get(session_id)
            if not session:
                raise KeyError("session_not_found")

            chunk = {
                "text": clean,
                "confidence": float(confidence) if confidence is not None else None,
                "is_final": bool(is_final),
                "received_at": _utc_iso(),
            }
            session["chunks"].append(chunk)
            session["updated_at"] = _utc_iso()
            session["last_confidence"] = chunk["confidence"]
            if bool(is_final):
                session["turn_count"] = int(session.get("turn_count", 0)) + 1
                prior = str(session.get("transcript") or "")
                session["transcript"] = (prior + " " + clean).strip() if prior else clean

            return {
                "session_id": session_id,
                "status": session.get("status"),
                "turn_count": int(session.get("turn_count", 0)),
                "last_confidence": session.get("last_confidence"),
                "transcript": session.get("transcript", ""),
            }

    def end_session(self, session_id: str) -> dict:
        with self._lock:
            self._cleanup_expired_locked()
            session = self._sessions.pop(session_id, None)
            if not session:
                raise KeyError("session_not_found")
            session["status"] = "closed"
            session["closed_at"] = _utc_iso()
            self._recent_closed.append(session)
            self._save_state()
            return dict(session)

    def session_status(self, session_id: str) -> dict:
        with self._lock:
            self._cleanup_expired_locked()
            session = self._sessions.get(session_id)
            if not session:
                raise KeyError("session_not_found")
            return {
                "session_id": session.get("session_id"),
                "status": session.get("status"),
                "updated_at": session.get("updated_at"),
                "turn_count": int(session.get("turn_count", 0)),
                "transcript": session.get("transcript", ""),
                "chunks_received": len(session.get("chunks") or []),
                "last_confidence": session.get("last_confidence"),
            }

    def status(self) -> dict:
        with self._lock:
            self._cleanup_expired_locked()
            return {
                "active_sessions": len(self._sessions),
                "recent_closed_sessions": len(self._recent_closed),
                "ttl_minutes": int(self._ttl.total_seconds() // 60),
                "updated_at": _utc_iso(),
            }
