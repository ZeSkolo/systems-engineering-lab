"""Chroma/Qdrant-shaped in-memory vector store with JSON persistence."""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .embeddings import cosine, embed


@dataclass
class Item:
    id: str
    text: str
    vector: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)


class VectorStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self.items: list[Item] = []
        if self.path and self.path.exists():
            self._load()

    def add(self, text: str, metadata: dict[str, Any] | None = None, item_id: str | None = None) -> Item:
        item = Item(item_id or str(uuid.uuid4()), text, embed(text), metadata or {})
        self.items.append(item)
        self._save()
        return item

    def delete(self, item_id: str) -> bool:
        n = len(self.items)
        self.items = [it for it in self.items if it.id != item_id]
        changed = len(self.items) != n
        if changed:
            self._save()
        return changed

    def upsert_replace(self, predicate, text: str, metadata: dict[str, Any] | None = None) -> Item:
        self.items = [it for it in self.items if not predicate(it)]
        return self.add(text, metadata)

    def search(self, query: str, k: int = 5, min_score: float = 0.05) -> list[tuple[Item, float]]:
        qwords = set(query.lower().split())
        q = embed(query)
        scored: list[tuple[Item, float]] = []
        for it in self.items:
            score = cosine(q, it.vector)
            words = set(it.text.lower().split())
            if qwords & words:
                score += 0.35
            scored.append((it, score))
        scored.sort(key=lambda p: p[1], reverse=True)
        return [(it, s) for it, s in scored[:k] if s >= min_score]

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([asdict(it) for it in self.items], indent=2))

    def _load(self) -> None:
        raw = json.loads(self.path.read_text())
        self.items = [Item(**row) for row in raw]
