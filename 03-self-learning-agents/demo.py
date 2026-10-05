#!/usr/bin/env python3
"""Scripted demo: extract facts, recall them later, use calculator via ReAct."""
from __future__ import annotations

import tempfile
from pathlib import Path

from agent.react import Agent


def main() -> None:
    data = tempfile.mkdtemp(prefix="agent-demo-")
    agent = Agent(data_dir=data)
    turns = [
        "Hi, my name is Valteri and I like compilers.",
        "What is my name?",
        "What do I like?",
        "What is 21 * 2?",
    ]
    for msg in turns:
        print(f"you> {msg}")
        result = agent.chat(msg)
        print(f"agent> {result.answer}")
        for step in result.trace:
            if step.action:
                print(f"       tool {step.action}({step.action_input}) -> {step.observation}")
        if result.extracted:
            print(f"       extracted {result.extracted}")
        print()
    print("semantic store:", Path(data, "semantic.json").read_text()[:400])
    print("demo: PASS")


if __name__ == "__main__":
    main()
