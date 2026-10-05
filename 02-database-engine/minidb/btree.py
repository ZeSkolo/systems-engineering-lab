"""On-disk B+ tree with leaf splits, internal splits, range scans, and merges."""
from __future__ import annotations

import struct
from typing import Iterator

from .storage import PAGE_INTERNAL, PAGE_LEAF, BufferPool, SlottedPage


def encode_leaf(key: bytes, val: bytes) -> bytes:
    return struct.pack("<H", len(key)) + key + struct.pack("<H", len(val)) + val


def decode_leaf(cell: bytes) -> tuple[bytes, bytes]:
    klen = struct.unpack_from("<H", cell, 0)[0]
    key = cell[2 : 2 + klen]
    vlen = struct.unpack_from("<H", cell, 2 + klen)[0]
    val = cell[4 + klen : 4 + klen + vlen]
    return key, val


def encode_internal(key: bytes, child: int) -> bytes:
    return struct.pack("<H", len(key)) + key + struct.pack("<I", child)


def decode_internal(cell: bytes) -> tuple[bytes, int]:
    klen = struct.unpack_from("<H", cell, 0)[0]
    key = cell[2 : 2 + klen]
    child = struct.unpack_from("<I", cell, 2 + klen)[0]
    return key, child


class BPlusTree:
    def __init__(self, pool: BufferPool, root_pid: int) -> None:
        self.pool = pool
        self.root_pid = root_pid

    def _sp(self, pid: int) -> SlottedPage:
        return SlottedPage(self.pool.get(pid))

    def _child_for(self, sp: SlottedPage, key: bytes) -> int:
        for i in range(sp.nslots()):
            k, child = decode_internal(sp.cell(i))
            if key < k:
                return child
        return sp.extra()

    def _children(self, sp: SlottedPage) -> list[int]:
        kids = [decode_internal(sp.cell(i))[1] for i in range(sp.nslots())]
        kids.append(sp.extra())
        return kids

    def search(self, key: bytes) -> bytes | None:
        pid = self.root_pid
        while True:
            sp = self._sp(pid)
            if sp.page_type() == PAGE_LEAF:
                for i in range(sp.nslots()):
                    k, v = decode_leaf(sp.cell(i))
                    if k == key:
                        return v
                    if k > key:
                        return None
                return None
            pid = self._child_for(sp, key)

    def scan(self, start: bytes | None = None, end: bytes | None = None) -> Iterator[tuple[bytes, bytes]]:
        pid = self.root_pid
        sp = self._sp(pid)
        while sp.page_type() != PAGE_LEAF:
            if start is None:
                pid = decode_internal(sp.cell(0))[1] if sp.nslots() else sp.extra()
            else:
                pid = self._child_for(sp, start)
            sp = self._sp(pid)
        while True:
            for i in range(sp.nslots()):
                k, v = decode_leaf(sp.cell(i))
                if start is not None and k < start:
                    continue
                if end is not None and k >= end:
                    return
                yield k, v
            nxt = sp.next_page()
            if not nxt:
                return
            sp = self._sp(nxt)

    def insert(self, key: bytes, val: bytes) -> None:
        leaf_pid, path = self._find_leaf(key)
        sp = self._sp(leaf_pid)
        cell = encode_leaf(key, val)
        idx = self._leaf_index(sp, key)
        if idx < sp.nslots() and decode_leaf(sp.cell(idx))[0] == key:
            sp.replace_cell(idx, cell)
            return
        if sp.can_fit(len(cell)):
            sp.insert_cell(idx, cell)
            return
        self._split_leaf_and_insert(leaf_pid, path, key, cell)

    def delete(self, key: bytes) -> bool:
        leaf_pid, path = self._find_leaf(key)
        sp = self._sp(leaf_pid)
        idx = self._leaf_index(sp, key)
        if idx >= sp.nslots() or decode_leaf(sp.cell(idx))[0] != key:
            return False
        sp.delete_cell(idx)
        if sp.nslots() == 0 and path:
            self._merge_empty_leaf(leaf_pid, path)
        return True

    def _find_leaf(self, key: bytes) -> tuple[int, list[int]]:
        path: list[int] = []
        pid = self.root_pid
        while True:
            sp = self._sp(pid)
            if sp.page_type() == PAGE_LEAF:
                return pid, path
            path.append(pid)
            pid = self._child_for(sp, key)

    @staticmethod
    def _leaf_index(sp: SlottedPage, key: bytes) -> int:
        lo, hi = 0, sp.nslots()
        while lo < hi:
            mid = (lo + hi) // 2
            k, _ = decode_leaf(sp.cell(mid))
            if k < key:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def _split_leaf_and_insert(self, leaf_pid: int, path: list[int], key: bytes, cell: bytes) -> None:
        sp = self._sp(leaf_pid)
        cells = sp.cells()
        idx = self._leaf_index(sp, key)
        cells.insert(idx, cell)
        mid = len(cells) // 2
        left, right = cells[:mid], cells[mid:]
        old_next = sp.next_page()
        sp.init(PAGE_LEAF)
        for i, c in enumerate(left):
            sp.insert_cell(i, c)
        rp = self.pool.new_page()
        rsp = SlottedPage(rp)
        rsp.init(PAGE_LEAF)
        for i, c in enumerate(right):
            rsp.insert_cell(i, c)
        rsp.set_next(old_next)
        sp.set_next(rp.pid)
        sep = decode_leaf(right[0])[0]
        self._insert_separator(path, leaf_pid, sep, rp.pid)

    def _place_separator(self, sp: SlottedPage, left_pid: int, sep: bytes, right_pid: int) -> None:
        kids = self._children(sp)
        idx = kids.index(left_pid)
        if idx == len(kids) - 1:
            sp.insert_cell(sp.nslots(), encode_internal(sep, left_pid))
            sp.set_extra(right_pid)
            return
        sp.insert_cell(idx, encode_internal(sep, left_pid))
        old_k, _old_child = decode_internal(sp.cell(idx + 1))
        sp.replace_cell(idx + 1, encode_internal(old_k, right_pid))

    def _insert_separator(self, path: list[int], left_pid: int, sep: bytes, right_pid: int) -> None:
        cell = encode_internal(sep, left_pid)
        if not path:
            root = self.pool.new_page()
            sp = SlottedPage(root)
            sp.init(PAGE_INTERNAL)
            sp.insert_cell(0, encode_internal(sep, left_pid))
            sp.set_extra(right_pid)
            self.root_pid = root.pid
            return
        parent_pid = path[-1]
        psp = self._sp(parent_pid)
        if psp.can_fit(len(cell)):
            self._place_separator(psp, left_pid, sep, right_pid)
            return
        promoted, new_right = self._split_internal(parent_pid)
        self._insert_separator(path[:-1], parent_pid, promoted, new_right)
        target_pid = parent_pid if left_pid in self._children(self._sp(parent_pid)) else new_right
        self._place_separator(self._sp(target_pid), left_pid, sep, right_pid)

    def _split_internal(self, pid: int) -> tuple[bytes, int]:
        sp = self._sp(pid)
        cells = sp.cells()
        mid = max(1, len(cells) // 2)
        promoted, left_of_promoted = decode_internal(cells[mid])
        left_cells, right_cells = cells[:mid], cells[mid + 1 :]
        old_extra = sp.extra()
        sp.init(PAGE_INTERNAL)
        for i, c in enumerate(left_cells):
            sp.insert_cell(i, c)
        sp.set_extra(left_of_promoted)
        rp = self.pool.new_page()
        rsp = SlottedPage(rp)
        rsp.init(PAGE_INTERNAL)
        for i, c in enumerate(right_cells):
            rsp.insert_cell(i, c)
        rsp.set_extra(old_extra)
        return promoted, rp.pid

    def _merge_empty_leaf(self, leaf_pid: int, path: list[int]) -> None:
        sp = self._sp(leaf_pid)
        parent = self._sp(path[-1])
        kids = self._children(parent)
        try:
            idx = kids.index(leaf_pid)
        except ValueError:
            return
        # Borrow / merge with right sibling if it shares this parent.
        if idx + 1 < len(kids):
            right_pid = kids[idx + 1]
            rsp = self._sp(right_pid)
            if rsp.nslots() == 0:
                return
            # Pull the first cell from the right sibling.
            cell = rsp.cell(0)
            rsp.delete_cell(0)
            sp.insert_cell(0, cell)
            sep = decode_leaf(sp.cell(0))[0]
            if idx < parent.nslots():
                _k, child = decode_internal(parent.cell(idx))
                parent.replace_cell(idx, encode_internal(sep, child))
            return
        if idx > 0:
            left_pid = kids[idx - 1]
            lsp = self._sp(left_pid)
            lsp.set_next(sp.next_page())
            self._remove_child(parent, leaf_pid)
            self.pool.free_page(leaf_pid)
            if parent.nslots() == 0 and path[-1] == self.root_pid and parent.page_type() == PAGE_INTERNAL:
                self.root_pid = parent.extra()
                self.pool.free_page(path[-1])

    def _remove_child(self, parent: SlottedPage, child_pid: int) -> None:
        kids = self._children(parent)
        idx = kids.index(child_pid)
        if idx == len(kids) - 1:
            # Removing extra: last cell's child becomes extra, drop last key.
            if parent.nslots() == 0:
                return
            last_k, last_child = decode_internal(parent.cell(parent.nslots() - 1))
            parent.delete_cell(parent.nslots() - 1)
            parent.set_extra(last_child)
            return
        parent.delete_cell(idx)
