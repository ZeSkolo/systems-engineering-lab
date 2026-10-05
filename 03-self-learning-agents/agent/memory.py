"""Layered memory: working window + episodic log + semantic facts (Mem0-style)."""
from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .vector_store import Item, VectorStore


@dataclass
class Turn:
    role: str
    content: str
    ts: float


class WorkingMemory:
    def __init__(self, max_turns: int = 8) -> None:
        self.turns: deque[Turn] = deque(maxlen=max_turns)

    def add(self, role: str, content: str) -> None:
        self.turns.append(Turn(role, content, time.time()))

    def prompt_block(self) -> str:
        if not self.turns:
            return "(empty)"
        return "\n".join(f"{t.role}: {t.content}" for t in self.turns)


class EpisodicMemory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.events: list[Turn] = []
        if path.exists():
            self.events = [Turn(**row) for row in json.loads(path.read_text())]

    def add(self, role: str, content: str) -> None:
        self.events.append(Turn(role, content, time.time()))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([asdict(e) for e in self.events], indent=2))

    def recent(self, n: int = 20) -> list[Turn]:
        return self.events[-n:]


class SemanticMemory:
    def __init__(self, path: Path) -> None:
        self.store = VectorStore(path)

    def remember(self, fact: str, kind: str = "fact", subject: str | None = None) -> Item:
        meta: dict[str, Any] = {"kind": kind, "ts": time.time()}
        if subject:
            meta["subject"] = subject
            self.store.upsert_replace(
                lambda it: it.metadata.get("subject") == subject, fact, meta
            )
            return self.store.items[-1]
        return self.store.add(fact, meta)

    def search(self, query: str, k: int = 5) -> list[tuple[Item, float]]:
        return self.store.search(query, k=k)

    def forget_subject(self, subject: str) -> None:
        self.store.items = [it for it in self.store.items if it.metadata.get("subject") != subject]
        self.store._save()

    def all_facts(self) -> list[str]:
        return [it.text for it in self.store.items]


class MemorySystem:
    def __init__(self, data_dir: str | Path = "data") -> None:
        root = Path(data_dir)
        root.mkdir(parents=True, exist_ok=True)
        self.working = WorkingMemory()
        self.episodic = EpisodicMemory(root / "episodic.json")
        self.semantic = SemanticMemory(root / "semantic.json")

    def observe(self, role: str, content: str) -> None:
        self.working.add(role, content)
        self.episodic.add(role, content)

    def recall(self, query: str) -> str:
        hits = self.semantic.search(query)
        if not hits:
            facts = self.semantic.all_facts()
            if not facts:
                return "(no long-term facts)"
            return "\n".join(f"- {f}" for f in facts[:8])
        return "\n".join(f"- {it.text} ({score:.2f})" for it, score in hits)
