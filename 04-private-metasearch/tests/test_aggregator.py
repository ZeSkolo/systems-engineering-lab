#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "aggregator"))

from metasearch import search, strip_tracking  # noqa: E402


def main() -> None:
    dirty = "https://redis.io/docs?utm_source=ad&gclid=1#frag"
    clean = strip_tracking(dirty)
    assert "utm_source" not in clean and "gclid" not in clean
    assert "#" not in clean
    rows = search("B+ tree", engines=["catalog"])
    assert rows, rows
    assert any("B+" in r.title or "tree" in r.title.lower() for r in rows), rows
    print("metasearch tests: PASS")
    for r in rows[:3]:
        print(f"  {r.engine}: {r.title} -> {r.url}")


if __name__ == "__main__":
    main()
