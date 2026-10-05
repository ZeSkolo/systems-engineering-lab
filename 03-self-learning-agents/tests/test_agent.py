#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.react import Agent  # noqa: E402


def main() -> None:
    data = tempfile.mkdtemp(prefix="agent-test-")
    agent = Agent(data_dir=data)
    r1 = agent.chat("Hi, my name is Valteri and I like compilers.")
    assert r1.answer
    r2 = agent.chat("What is my name?")
    assert "Valteri" in r2.answer or "Valteri" in (r2.trace[0].observation or ""), r2
    r3 = agent.chat("What is 21 * 2?")
    blob = r3.answer + "".join(s.observation or "" for s in r3.trace)
    assert "42" in blob, r3
    facts = agent.memory.semantic.all_facts()
    assert any("Valteri" in f or "name" in f.lower() for f in facts), facts
    print("agent tests: PASS")
    print("  facts:", facts)


if __name__ == "__main__":
    main()
