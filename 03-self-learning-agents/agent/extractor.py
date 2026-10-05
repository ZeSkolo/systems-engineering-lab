"""Dynamic knowledge extraction: regex fallback + optional LLM JSON facts."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .llm import LLM
from .memory import MemorySystem

EXTRACT_PROMPT = """Extract durable facts about the USER from this turn.
Return JSON only: {{"facts": [{{"subject": "name|likes|location|constraint|other", "text": "...", "obsolete": false}}]}}
If nothing durable, return {{"facts": []}}.
User: {user}
Assistant: {assistant}
"""

PATTERNS = [
    (re.compile(r"\bmy name is ([A-Za-z][A-Za-z'-]{0,40})(?:\s+and\b|[.!?,]|$)", re.I), "name"),
    (re.compile(r"\bi (?:like|love|enjoy) ([^.!?]+)", re.I), "likes"),
    (re.compile(r"\bi (?:live|i'm based|am based) in ([^.!?]+)", re.I), "location"),
    (re.compile(r"\bi (?:don't|do not|never) ([^.!?]+)", re.I), "constraint"),
]


@dataclass
class Fact:
    subject: str
    text: str
    obsolete: bool = False


def extract_rules(user: str) -> list[Fact]:
    facts: list[Fact] = []
    for rx, subject in PATTERNS:
        m = rx.search(user)
        if m:
            facts.append(Fact(subject, f"User {subject}: {m.group(1).strip().rstrip('.')}"))
    return facts


def extract_llm(llm: LLM, user: str, assistant: str) -> list[Fact]:
    raw = llm.complete(EXTRACT_PROMPT.format(user=user, assistant=assistant), json_mode=True)
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        data = json.loads(raw[start:end])
    except Exception:
        return []
    out = []
    for row in data.get("facts", []):
        if not row.get("text"):
            continue
        out.append(Fact(str(row.get("subject", "other")), str(row["text"]), bool(row.get("obsolete"))))
    return out


def apply_facts(memory: MemorySystem, facts: list[Fact]) -> list[str]:
    applied = []
    seen = set()
    for fact in facts:
        key = (fact.subject, fact.text)
        if key in seen:
            continue
        seen.add(key)
        if fact.obsolete:
            memory.semantic.forget_subject(fact.subject)
            applied.append(f"forgot {fact.subject}")
            continue
        memory.semantic.remember(fact.text, kind="fact", subject=fact.subject)
        applied.append(fact.text)
    return applied
