#!/usr/bin/env python3
"""RESP integration tests against mini_redis.py"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "python-runtime" / "mini_redis.py"
PORT = 16379


def send(cmd: list[str]) -> bytes:
    buf = f"*{len(cmd)}\r\n".encode()
    for part in cmd:
        body = part.encode()
        buf += f"${len(body)}\r\n".encode() + body + b"\r\n"
    with socket.create_connection(("127.0.0.1", PORT), timeout=2) as s:
        s.sendall(buf)
        s.settimeout(2)
        return s.recv(65536)


def main() -> None:
    proc = subprocess.Popen([sys.executable, str(SERVER), "--port", str(PORT)], stdout=subprocess.PIPE)
    try:
        time.sleep(0.25)
        assert send(["PING"]) == b"+PONG\r\n", send(["PING"])
        assert send(["ECHO", "hello"]) == b"$5\r\nhello\r\n"
        assert send(["SET", "k", "v"]) == b"+OK\r\n"
        assert send(["GET", "k"]) == b"$1\r\nv\r\n"
        assert send(["INCR", "n"]) == b":1\r\n"
        assert send(["INCR", "n"]) == b":2\r\n"
        assert send(["DEL", "k"]) == b":1\r\n"
        assert send(["GET", "k"]) == b"$-1\r\n"
        assert send(["SET", "tmp", "1", "PX", "150"]) == b"+OK\r\n"
        assert send(["GET", "tmp"]) == b"$1\r\n1\r\n"
        time.sleep(0.25)
        assert send(["GET", "tmp"]) == b"$-1\r\n"
        print("redis RESP tests: PASS")
    finally:
        proc.terminate()
        proc.wait(timeout=2)


if __name__ == "__main__":
    main()
