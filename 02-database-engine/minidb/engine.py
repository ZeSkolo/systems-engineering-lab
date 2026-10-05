"""Catalog, row encoding, relational executor, and commit (flush + fsync)."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

from .btree import BPlusTree
from .sql import CreateTable, Delete, DropTable, Insert, Predicate, Select, parse_sql
from .storage import PAGE_LEAF, BufferPool, Pager, SlottedPage

INT, TEXT, NULL = 1, 2, 0


def encode_row(values: list[Any]) -> bytes:
    parts = [struct.pack("<H", len(values))]
    for v in values:
        if v is None:
            parts.append(struct.pack("<BH", NULL, 0))
        elif isinstance(v, int):
            body = struct.pack(">q", v)
            parts.append(struct.pack("<BH", INT, len(body)) + body)
        else:
            body = str(v).encode("utf-8")
            parts.append(struct.pack("<BH", TEXT, len(body)) + body)
    return b"".join(parts)


def decode_row(buf: bytes) -> list[Any]:
    n = struct.unpack_from("<H", buf, 0)[0]
    i = 2
    out: list[Any] = []
    for _ in range(n):
        typ, length = struct.unpack_from("<BH", buf, i)
        i += 3
        body = buf[i : i + length]
        i += length
        if typ == NULL:
            out.append(None)
        elif typ == INT:
            out.append(struct.unpack(">q", body)[0])
        else:
            out.append(body.decode("utf-8"))
    return out


def encode_schema(root_pid: int, next_rowid: int, columns: list[tuple[str, str]]) -> bytes:
    parts = [struct.pack("<IQH", root_pid, next_rowid, len(columns))]
    for name, typ in columns:
        nb = name.encode()
        t = INT if typ == "INT" else TEXT
        parts.append(struct.pack("<HB", len(nb), t) + nb)
    return b"".join(parts)


def decode_schema(buf: bytes) -> tuple[int, int, list[tuple[str, str]]]:
    root_pid, next_rowid, ncol = struct.unpack_from("<IQH", buf, 0)
    i = 14
    cols = []
    for _ in range(ncol):
        nlen, t = struct.unpack_from("<HB", buf, i)
        i += 3
        name = buf[i : i + nlen].decode()
        i += nlen
        cols.append((name, "INT" if t == INT else "TEXT"))
    return root_pid, next_rowid, cols


def rowid_key(rowid: int) -> bytes:
    return struct.pack(">Q", rowid)


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    message: str | None = None

    def __str__(self) -> str:
        if self.message:
            return self.message
        if not self.columns:
            return ""
        widths = [len(c) for c in self.columns]
        for row in self.rows:
            for i, v in enumerate(row):
                widths[i] = max(widths[i], len(str(v)))
        hdr = " | ".join(c.ljust(widths[i]) for i, c in enumerate(self.columns))
        sep = "-+-".join("-" * w for w in widths)
        lines = [hdr, sep]
        for row in self.rows:
            lines.append(" | ".join(str(v).ljust(widths[i]) for i, v in enumerate(row)))
        lines.append(f"({len(self.rows)} rows)")
        return "\n".join(lines)


class MiniDB:
    def __init__(self, path: str, pool_size: int = 64) -> None:
        self.pager = Pager(path)
        self.pool = BufferPool(self.pager, pool_size)
        if self.pager.catalog_root == 0:
            page = self.pool.new_page()
            SlottedPage(page).init(PAGE_LEAF)
            self.pager.set_catalog_root(page.pid)
        self.catalog = BPlusTree(self.pool, self.pager.catalog_root)

    def close(self) -> None:
        self.commit()
        self.pager.close()

    def commit(self) -> None:
        """A commit is: write dirty pages, update superblock, fsync."""
        self.pager.set_catalog_root(self.catalog.root_pid)
        self.pool.flush()

    def execute(self, sql: str) -> QueryResult:
        stmt = parse_sql(sql)
        if isinstance(stmt, CreateTable):
            return self._create(stmt)
        if isinstance(stmt, Insert):
            return self._insert(stmt)
        if isinstance(stmt, Select):
            return self._select(stmt)
        if isinstance(stmt, Delete):
            return self._delete(stmt)
        if isinstance(stmt, DropTable):
            return self._drop(stmt)
        raise RuntimeError("unhandled statement")

    def _load_table(self, name: str):
        raw = self.catalog.search(name.encode())
        if raw is None:
            raise KeyError(f"no such table: {name}")
        root, next_rowid, cols = decode_schema(raw)
        tree = BPlusTree(self.pool, root)
        return tree, next_rowid, cols

    def _save_table(self, name: str, tree: BPlusTree, next_rowid: int, cols: list[tuple[str, str]]) -> None:
        self.catalog.insert(name.encode(), encode_schema(tree.root_pid, next_rowid, cols))

    def _create(self, stmt: CreateTable) -> QueryResult:
        if self.catalog.search(stmt.name.encode()) is not None:
            raise ValueError(f"table already exists: {stmt.name}")
        page = self.pool.new_page()
        SlottedPage(page).init(PAGE_LEAF)
        cols = [(c.name, c.col_type) for c in stmt.columns]
        tree = BPlusTree(self.pool, page.pid)
        self._save_table(stmt.name, tree, 1, cols)
        self.commit()
        return QueryResult([], [], f"created table {stmt.name}")

    def _insert(self, stmt: Insert) -> QueryResult:
        tree, next_rowid, cols = self._load_table(stmt.table)
        names = [c[0] for c in cols]
        values: list[Any] = [None] * len(cols)
        if stmt.columns:
            if len(stmt.columns) != len(stmt.values):
                raise ValueError("column/value count mismatch")
            for n, v in zip(stmt.columns, stmt.values):
                values[names.index(n)] = v
        else:
            if len(stmt.values) != len(cols):
                raise ValueError("value count mismatch")
            values = list(stmt.values)
        for i, (name, typ) in enumerate(cols):
            v = values[i]
            if v is None:
                continue
            if typ == "INT":
                values[i] = int(v)
            else:
                values[i] = str(v)
        tree.insert(rowid_key(next_rowid), encode_row(values))
        self._save_table(stmt.table, tree, next_rowid + 1, cols)
        self.commit()
        return QueryResult([], [], f"inserted rowid {next_rowid}")

    def _match(self, names: list[str], row: list[Any], preds: list[Predicate]) -> bool:
        for p in preds:
            left = row[names.index(p.column)]
            right = p.value
            if p.op == "=" and not (left == right):
                return False
            if p.op == "!=" and not (left != right):
                return False
            if p.op == "<" and not (left < right):
                return False
            if p.op == ">" and not (left > right):
                return False
            if p.op == "<=" and not (left <= right):
                return False
            if p.op == ">=" and not (left >= right):
                return False
        return True

    def _select(self, stmt: Select) -> QueryResult:
        tree, _nr, cols = self._load_table(stmt.table)
        names = [c[0] for c in cols]
        out_names = names if stmt.columns is None else stmt.columns
        rows: list[list[Any]] = []
        for _k, raw in tree.scan():
            row = decode_row(raw)
            if not self._match(names, row, stmt.where):
                continue
            rows.append([row[names.index(c)] for c in out_names])
        if stmt.order_by:
            idx = out_names.index(stmt.order_by)
            rows.sort(key=lambda r: (r[idx] is None, r[idx]))
        if stmt.limit is not None:
            rows = rows[: stmt.limit]
        return QueryResult(out_names, rows)

    def _delete(self, stmt: Delete) -> QueryResult:
        tree, next_rowid, cols = self._load_table(stmt.table)
        names = [c[0] for c in cols]
        doomed = []
        for k, raw in tree.scan():
            row = decode_row(raw)
            if self._match(names, row, stmt.where):
                doomed.append(k)
        for k in doomed:
            tree.delete(k)
        self._save_table(stmt.table, tree, next_rowid, cols)
        self.commit()
        return QueryResult([], [], f"deleted {len(doomed)} row(s)")

    def _drop(self, stmt: DropTable) -> QueryResult:
        if self.catalog.search(stmt.name.encode()) is None:
            raise KeyError(f"no such table: {stmt.name}")
        self.catalog.delete(stmt.name.encode())
        self.commit()
        return QueryResult([], [], f"dropped table {stmt.name}")
