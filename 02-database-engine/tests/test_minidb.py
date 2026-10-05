#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from minidb.engine import MiniDB  # noqa: E402


def run() -> None:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    db = MiniDB(path, pool_size=16)
    try:
        db.execute("CREATE TABLE users (id INT, name TEXT, score INT)")
        db.execute("INSERT INTO users (id, name, score) VALUES (1, 'ada', 10)")
        db.execute("INSERT INTO users VALUES (2, 'grace', 20)")
        db.execute("INSERT INTO users VALUES (3, 'alan', 15)")
        r = db.execute("SELECT name, score FROM users WHERE id = 2")
        assert r.rows == [["grace", 20]], r.rows
        r = db.execute("SELECT name FROM users WHERE score > 10 ORDER BY name")
        assert [row[0] for row in r.rows] == ["alan", "grace"], r.rows
        db.execute("DELETE FROM users WHERE id = 1")
        r = db.execute("SELECT * FROM users")
        assert len(r.rows) == 2, r.rows
        # Force B+ tree splits with many rows.
        db.execute("CREATE TABLE kv (k INT, v TEXT)")
        for i in range(200):
            db.execute(f"INSERT INTO kv VALUES ({i}, 'v{i}')")
        r = db.execute("SELECT k FROM kv WHERE k = 150")
        assert r.rows == [[150]], r.rows
        r = db.execute("SELECT k FROM kv WHERE k >= 195")
        assert [row[0] for row in r.rows] == [195, 196, 197, 198, 199], r.rows
        db.close()
        # Reopen: durability after commit/fsync.
        db = MiniDB(path, pool_size=8)
        r = db.execute("SELECT name FROM users ORDER BY name")
        assert [row[0] for row in r.rows] == ["alan", "grace"], r.rows
        r = db.execute("SELECT k FROM kv WHERE k = 42")
        assert r.rows == [[42]], r.rows
        print("minidb tests: PASS")
        print(
            f"  buffer hits={db.pool.hits} misses={db.pool.misses} "
            f"evictions={db.pool.evictions} pages={db.pager.page_count}"
        )
    finally:
        db.close()
        try:
            os.remove(path)
        except OSError:
            pass


if __name__ == "__main__":
    run()
