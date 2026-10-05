#!/usr/bin/env python3
"""Protocol-compatible Mini-Redis runtime (same architecture as Redis.java).

Use this when a JDK is unavailable. Speaks official RESP on a raw TCP socket.

    python3 mini_redis.py --port 6379
"""
from __future__ import annotations

import argparse
import random
import re
import socket
import threading
import time
from typing import Any


class RedisStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._map: dict[str, tuple[str, float]] = {}  # key -> (value, expire_at or 0)
        self._running = True
        self._daemon = threading.Thread(target=self._active_expire, name="expire", daemon=True)

    def start(self) -> None:
        self._daemon.start()

    def stop(self) -> None:
        self._running = False

    def _live(self, key: str) -> tuple[str, float] | None:
        entry = self._map.get(key)
        if entry is None:
            return None
        value, exp = entry
        if exp and exp <= time.time():
            self._map.pop(key, None)
            return None
        return entry

    def _active_expire(self) -> None:
        while self._running:
            time.sleep(0.1)
            with self._lock:
                if not self._map:
                    continue
                keys = list(self._map.keys())
                now = time.time()
                for _ in range(min(20, len(keys))):
                    key = random.choice(keys)
                    entry = self._map.get(key)
                    if entry and entry[1] and entry[1] <= now:
                        self._map.pop(key, None)

    def get(self, key: str) -> str | None:
        with self._lock:
            live = self._live(key)
            return None if live is None else live[0]

    def set(self, key: str, value: str, px_ms: float | None, nx: bool, xx: bool) -> bool:
        with self._lock:
            live = self._live(key)
            if nx and live is not None:
                return False
            if xx and live is None:
                return False
            exp = 0.0 if px_ms is None else time.time() + (px_ms / 1000.0)
            self._map[key] = (value, exp)
            return True

    def delete(self, keys: list[str]) -> int:
        with self._lock:
            n = 0
            for key in keys:
                if self._live(key) is not None:
                    self._map.pop(key, None)
                    n += 1
            return n

    def exists(self, keys: list[str]) -> int:
        with self._lock:
            return sum(1 for key in keys if self._live(key) is not None)

    def expire(self, key: str, px_ms: float) -> bool:
        with self._lock:
            live = self._live(key)
            if live is None:
                return False
            self._map[key] = (live[0], time.time() + px_ms / 1000.0)
            return True

    def ttl_ms(self, key: str) -> int:
        with self._lock:
            live = self._live(key)
            if live is None:
                return -2
            if not live[1]:
                return -1
            return max(0, int((live[1] - time.time()) * 1000))

    def keys(self, glob: str) -> list[str]:
        rx = re.compile("^" + re.escape(glob).replace(r"\*", ".*").replace(r"\?", ".") + "$")
        with self._lock:
            return [k for k in list(self._map) if self._live(k) is not None and rx.match(k)]

    def flush(self) -> None:
        with self._lock:
            self._map.clear()

    def incr(self, key: str, delta: int) -> int:
        with self._lock:
            live = self._live(key)
            if live is None:
                self._map[key] = (str(delta), 0.0)
                return delta
            try:
                cur = int(live[0])
            except ValueError as exc:
                raise ValueError("ERR value is not an integer or out of range") from exc
            nxt = cur + delta
            self._map[key] = (str(nxt), live[1])
            return nxt

    def size(self) -> int:
        with self._lock:
            return sum(1 for k in list(self._map) if self._live(k) is not None)


class RespError(Exception):
    pass


class RespCodec:
    @staticmethod
    def encode(value: Any) -> bytes:
        if value is None:
            return b"$-1\r\n"
        if isinstance(value, RespError):
            return f"-{value.args[0]}\r\n".encode()
        if isinstance(value, bool):
            return f":{1 if value else 0}\r\n".encode()
        if isinstance(value, int):
            return f":{value}\r\n".encode()
        if isinstance(value, str):
            body = value.encode()
            return f"${len(body)}\r\n".encode() + body + b"\r\n"
        if isinstance(value, bytes):
            return f"${len(value)}\r\n".encode() + value + b"\r\n"
        if isinstance(value, list):
            out = f"*{len(value)}\r\n".encode()
            for item in value:
                out += RespCodec.encode(item)
            return out
        if value == "OK" or value == "PONG":
            return f"+{value}\r\n".encode()
        raise TypeError(type(value))

    @staticmethod
    def simple(s: str) -> bytes:
        return f"+{s}\r\n".encode()

    @staticmethod
    def error(s: str) -> bytes:
        return f"-{s}\r\n".encode()


class RespParser:
    def __init__(self) -> None:
        self.buf = bytearray()

    def feed(self, data: bytes) -> None:
        self.buf.extend(data)

    def next(self) -> Any | None:
        if not self.buf:
            return None
        try:
            value, end = self._parse(0)
        except IndexError:
            return None
        except ValueError:
            self.buf.clear()
            return RespError("ERR protocol error")
        if end is None:
            return None
        del self.buf[:end]
        return value

    def _parse(self, i: int) -> tuple[Any, int | None]:
        if i >= len(self.buf):
            raise IndexError
        kind = self.buf[i]
        if kind == ord("+"):
            line, end = self._line(i + 1)
            return (None, None) if end is None else (line.decode(), end)
        if kind == ord("-"):
            line, end = self._line(i + 1)
            return (None, None) if end is None else (RespError(line.decode()), end)
        if kind == ord(":"):
            line, end = self._line(i + 1)
            return (None, None) if end is None else (int(line), end)
        if kind == ord("$"):
            line, hdr_end = self._line(i + 1)
            if hdr_end is None:
                return None, None
            n = int(line)
            if n == -1:
                return None, hdr_end
            end = hdr_end + n + 2
            if end > len(self.buf):
                return None, None
            return bytes(self.buf[hdr_end : hdr_end + n]).decode("utf-8", "replace"), end
        if kind == ord("*"):
            line, hdr_end = self._line(i + 1)
            if hdr_end is None:
                return None, None
            n = int(line)
            if n == -1:
                return None, hdr_end
            pos = hdr_end
            items = []
            for _ in range(n):
                item, pos2 = self._parse(pos)
                if pos2 is None:
                    return None, None
                items.append(item)
                pos = pos2
            return items, pos
        line, end = self._line(i)
        if end is None:
            return None, None
        parts = line.decode().strip().split()
        return parts, end

    def _line(self, i: int) -> tuple[bytes, int | None]:
        cr = self.buf.find(b"\r\n", i)
        if cr < 0:
            return b"", None
        return bytes(self.buf[i:cr]), cr + 2


class CommandProcessor:
    def __init__(self, store: RedisStore) -> None:
        self.store = store

    def dispatch(self, value: Any) -> bytes:
        if isinstance(value, RespError):
            return RespCodec.error(value.args[0])
        if not isinstance(value, list) or not value:
            return RespCodec.error("ERR unknown command encoding")
        cmd = str(value[0]).upper()
        args = [str(x) if x is not None else "" for x in value[1:]]
        try:
            return self._run(cmd, args)
        except ValueError as exc:
            msg = str(exc)
            if not msg.startswith("ERR"):
                msg = "ERR " + msg
            return RespCodec.error(msg)

    def _run(self, cmd: str, args: list[str]) -> bytes:
        if cmd == "PING":
            return RespCodec.simple(args[0] if args else "PONG") if args else RespCodec.simple("PONG")
        if cmd == "ECHO":
            self._arity(args, 1, cmd)
            return RespCodec.encode(args[0])
        if cmd == "SET":
            return self._set(args)
        if cmd == "GET":
            self._arity(args, 1, cmd)
            return RespCodec.encode(self.store.get(args[0]))
        if cmd == "DEL":
            if not args:
                raise ValueError("ERR wrong number of arguments for 'del' command")
            return RespCodec.encode(self.store.delete(args))
        if cmd == "EXISTS":
            if not args:
                raise ValueError("ERR wrong number of arguments for 'exists' command")
            return RespCodec.encode(self.store.exists(args))
        if cmd == "EXPIRE":
            self._arity(args, 2, cmd)
            return RespCodec.encode(1 if self.store.expire(args[0], float(args[1]) * 1000) else 0)
        if cmd == "PEXPIRE":
            self._arity(args, 2, cmd)
            return RespCodec.encode(1 if self.store.expire(args[0], float(args[1])) else 0)
        if cmd == "TTL":
            self._arity(args, 1, cmd)
            ms = self.store.ttl_ms(args[0])
            return RespCodec.encode(ms if ms < 0 else (ms + 999) // 1000)
        if cmd == "PTTL":
            self._arity(args, 1, cmd)
            return RespCodec.encode(self.store.ttl_ms(args[0]))
        if cmd == "KEYS":
            self._arity(args, 1, cmd)
            return RespCodec.encode(self.store.keys(args[0]))
        if cmd in {"FLUSHDB", "FLUSHALL"}:
            self.store.flush()
            return RespCodec.simple("OK")
        if cmd == "INCR":
            self._arity(args, 1, cmd)
            return RespCodec.encode(self.store.incr(args[0], 1))
        if cmd == "DECR":
            self._arity(args, 1, cmd)
            return RespCodec.encode(self.store.incr(args[0], -1))
        if cmd == "INCRBY":
            self._arity(args, 2, cmd)
            return RespCodec.encode(self.store.incr(args[0], int(args[1])))
        if cmd == "TYPE":
            self._arity(args, 1, cmd)
            return RespCodec.simple("none" if self.store.get(args[0]) is None else "string")
        if cmd == "MGET":
            if not args:
                raise ValueError("ERR wrong number of arguments for 'mget' command")
            return RespCodec.encode([self.store.get(k) for k in args])
        if cmd == "MSET":
            if len(args) < 2 or len(args) % 2:
                raise ValueError("ERR wrong number of arguments for 'mset' command")
            for i in range(0, len(args), 2):
                self.store.set(args[i], args[i + 1], None, False, False)
            return RespCodec.simple("OK")
        if cmd == "DBSIZE":
            return RespCodec.encode(self.store.size())
        if cmd == "QUIT":
            return RespCodec.simple("OK")
        raise ValueError(f"ERR unknown command '{cmd}'")

    def _set(self, args: list[str]) -> bytes:
        if len(args) < 2:
            raise ValueError("ERR wrong number of arguments for 'set' command")
        key, val = args[0], args[1]
        px = None
        nx = xx = False
        i = 2
        while i < len(args):
            opt = args[i].upper()
            if opt == "EX":
                i += 1
                px = float(args[i]) * 1000
            elif opt == "PX":
                i += 1
                px = float(args[i])
            elif opt == "NX":
                nx = True
            elif opt == "XX":
                xx = True
            else:
                raise ValueError("ERR syntax error")
            i += 1
        ok = self.store.set(key, val, px, nx, xx)
        return RespCodec.encode(None) if not ok else RespCodec.simple("OK")

    @staticmethod
    def _arity(args: list[str], n: int, cmd: str) -> None:
        if len(args) != n:
            raise ValueError(f"ERR wrong number of arguments for '{cmd.lower()}' command")


def handle(conn: socket.socket, processor: CommandProcessor) -> None:
    parser = RespParser()
    conn.settimeout(60)
    try:
        while True:
            try:
                chunk = conn.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                return
            parser.feed(chunk)
            while True:
                msg = parser.next()
                if msg is None:
                    break
                conn.sendall(processor.dispatch(msg))
                if isinstance(msg, list) and msg and str(msg[0]).upper() == "QUIT":
                    return
    except OSError:
        return
    finally:
        conn.close()


def serve(host: str, port: int) -> None:
    store = RedisStore()
    store.start()
    processor = CommandProcessor(store)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen(128)
        print(f"mini-redis listening on {host}:{port} (RESP)")
        while True:
            conn, _ = server.accept()
            threading.Thread(target=handle, args=(conn, processor), daemon=True).start()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=6379)
    args = p.parse_args()
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
