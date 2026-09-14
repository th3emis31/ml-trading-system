from __future__ import annotations

import json
from pathlib import Path
from typing import List, Dict


class KnowledgeBase:
    def __init__(self, path: str | None = None):
        self.path = Path(path or "data/knowledge.json")
        self.path.parent.mkdir(exist_ok=True)

    def load(self) -> List[Dict[str, str]]:
        if self.path.exists():
            return json.loads(self.path.read_text(encoding="utf-8"))
        return []

    def save(self, entries: List[Dict[str, str]]):
        self.path.write_text(json.dumps(entries, indent=2), encoding="utf-8")

    def add(self, text: str, source: str, image_path: str | None = None) -> int:
        entries = self.load()
        entry = {"text": text, "source": source}
        if image_path:
            entry["image_path"] = image_path
        entries.append(entry)
        self.save(entries)
        return len(entries)

    def search(self, query: str) -> List[Dict[str, str]]:
        lower = query.lower()
        return [entry for entry in self.load() if lower in entry["text"].lower() or lower in entry["source"].lower()]
