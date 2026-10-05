#!/usr/bin/env python3
"""MiniDB REPL — SQLite-style prompt over the embedded engine."""
from __future__ import annotations

import argparse
import os
import sys

from .engine import MiniDB


def main() -> None:
    p = argparse.ArgumentParser(description="MiniDB embedded RDBMS")
    p.add_argument("db", nargs="?", default="minidb.db")
    p.add_argument("-c", "--command", help="run a single SQL statement")
    p.add_argument("--pool", type=int, default=64)
    args = p.parse_args()
    db = MiniDB(args.db, pool_size=args.pool)
    try:
        if args.command:
            print(db.execute(args.command))
            return
        print(f"MiniDB 1.0  ({os.path.abspath(args.db)})")
        print("slotted pages · LRU buffer pool · B+ tree · SQL  |  .quit to exit")
        buf = ""
        while True:
            try:
                line = input("... " if buf else "minidb> ")
            except EOFError:
                print()
                break
            if not buf and line.strip() in {".quit", ".exit", "quit", "exit"}:
                break
            if not buf and line.strip() == ".stats":
                print(
                    f"buffer hits={db.pool.hits} misses={db.pool.misses} "
                    f"evictions={db.pool.evictions} pages={db.pager.page_count}"
                )
                continue
            buf += line + " "
            if ";" in line:
                try:
                    print(db.execute(buf))
                except Exception as exc:
                    print(f"ERROR: {exc}")
                buf = ""
    finally:
        db.close()


if __name__ == "__main__":
    main()
