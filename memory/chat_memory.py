from __future__ import annotations

import ast
import re
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class ChatMessage:
    role: str
    content: str
    created_at: str
    format: str = 'text'
    topic: str | None = None
    symbol: str | None = None
    has_code: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at,
            "format": self.format,
            "topic": self.topic,
            "symbol": self.symbol,
            "has_code": self.has_code,
        }


class ChatMemoryStore:
    def __init__(self, file_path: Path, max_messages: int = 200) -> None:
        self.file_path = Path(file_path)
        self.max_messages = max_messages
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _repair_content(item: dict[str, Any]) -> dict[str, Any]:
        """Unwrap replies that were stored as the repr of the reply dict.

        An earlier version persisted ``str(reply)`` instead of ``reply['content']``,
        so those rows render in the UI as a literal Python dict. The stored text
        is left untouched on disk; it is unwrapped on the way out.
        """
        content = item.get("content")
        if not isinstance(content, str):
            return item
        stripped = content.strip()
        if not (stripped.startswith("{") and "'content'" in stripped):
            return item
        parsed = None
        try:
            parsed = ast.literal_eval(stripped)
        except Exception:
            # Most of these reprs embed datetime.datetime(...) inside a nested
            # context dict, which literal_eval refuses. Lift the content field
            # out textually instead.
            match = re.match(r"^\{'content':\s*('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")", stripped, re.S)
            if match:
                try:
                    text = ast.literal_eval(match.group(1))
                    repaired = dict(item)
                    repaired["content"] = str(text)
                    return repaired
                except Exception:
                    return item
            return item
        if not isinstance(parsed, dict) or "content" not in parsed:
            return item
        repaired = dict(item)
        repaired["content"] = str(parsed.get("content") or "")
        for key in ("format", "topic", "symbol", "has_code"):
            if parsed.get(key) is not None:
                repaired[key] = parsed[key]
        return repaired

    def _read(self) -> list[dict[str, Any]]:
        if not self.file_path.exists():
            return []
        try:
            data = json.loads(self.file_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [self._repair_content(item) for item in data if isinstance(item, dict)]
        except Exception:
            return []
        return []

    def _write(self, rows: list[dict[str, Any]]) -> None:
        limited = rows[-self.max_messages :]
        self.file_path.write_text(json.dumps(limited, indent=2), encoding="utf-8")

    def add(self, role: str, content: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        meta = metadata or {}
        record = ChatMessage(
            role=str(role),
            content=str(content),
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            format=str(meta.get('format', 'text')),
            topic=meta.get('topic'),
            symbol=meta.get('symbol'),
            has_code=bool(meta.get('has_code', False)),
        ).to_dict()
        rows = self._read()
        rows.append(record)
        self._write(rows)
        return record

    def recent(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = self._read()
        return rows[-max(1, int(limit)) :]
