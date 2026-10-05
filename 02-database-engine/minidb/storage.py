"""Slotted pages, free-space bitmap, pager, and LRU buffer pool."""
from __future__ import annotations

import os
import struct
from collections import OrderedDict

PAGE_SIZE = 4096
HEADER_SIZE = 16
SLOT_SIZE = 4
MAGIC = b"MNDB"
PAGE_FREE = 0
PAGE_LEAF = 1
PAGE_INTERNAL = 2

# header: type u8, flags u8, nslots u16, cell_start u16, next_page u32, extra u32, pad u16
HDR = struct.Struct("<BBHHI IH")  # wait pad is u16 after extra u32 = 1+1+2+2+4+4+2 = 16
# B B H H I I H = 1+1+2+2+4+4+2 = 16 yes but I wrote extra space. Fix:
HDR = struct.Struct("<BBHHI IH".replace(" ", ""))


def _hdr_pack(page_type: int, flags: int, nslots: int, cell_start: int, next_page: int, extra: int) -> bytes:
    return struct.pack("<BBHHIIH", page_type, flags, nslots, cell_start, next_page, extra, 0)


def _hdr_unpack(data: bytes) -> tuple[int, int, int, int, int, int]:
    page_type, flags, nslots, cell_start, next_page, extra, _pad = struct.unpack_from("<BBHHIIH", data, 0)
    return page_type, flags, nslots, cell_start, next_page, extra


class Page:
    __slots__ = ("pid", "data", "dirty")

    def __init__(self, pid: int, data: bytearray) -> None:
        self.pid = pid
        self.data = data
        self.dirty = False

    def mark_dirty(self) -> None:
        self.dirty = True


class SlottedPage:
    def __init__(self, page: Page) -> None:
        self.page = page

    @property
    def data(self) -> bytearray:
        return self.page.data

    def header(self) -> tuple[int, int, int, int, int, int]:
        return _hdr_unpack(self.data)

    def init(self, page_type: int) -> None:
        self.data[:] = b"\x00" * PAGE_SIZE
        self.data[0:16] = _hdr_pack(page_type, 0, 0, PAGE_SIZE, 0, 0)
        self.page.mark_dirty()

    def nslots(self) -> int:
        return _hdr_unpack(self.data)[2]

    def page_type(self) -> int:
        return self.data[0]

    def next_page(self) -> int:
        return _hdr_unpack(self.data)[4]

    def extra(self) -> int:
        return _hdr_unpack(self.data)[5]

    def set_next(self, pid: int) -> None:
        t, f, n, c, _nxt, e = self.header()
        self.data[0:16] = _hdr_pack(t, f, n, c, pid, e)
        self.page.mark_dirty()

    def set_extra(self, extra: int) -> None:
        t, f, n, c, nxt, _e = self.header()
        self.data[0:16] = _hdr_pack(t, f, n, c, nxt, extra)
        self.page.mark_dirty()

    def slot(self, i: int) -> tuple[int, int]:
        off, length = struct.unpack_from("<HH", self.data, HEADER_SIZE + i * SLOT_SIZE)
        return off, length

    def cell(self, i: int) -> bytes:
        off, length = self.slot(i)
        return bytes(self.data[off : off + length])

    def free_space(self) -> int:
        t, f, n, cell_start, nxt, e = self.header()
        slot_end = HEADER_SIZE + n * SLOT_SIZE
        return cell_start - slot_end

    def can_fit(self, cell_len: int) -> bool:
        return self.free_space() >= cell_len + SLOT_SIZE

    def insert_cell(self, index: int, cell: bytes) -> None:
        t, f, n, cell_start, nxt, e = self.header()
        if not self.can_fit(len(cell)):
            raise OverflowError("page full")
        new_start = cell_start - len(cell)
        self.data[new_start:cell_start] = cell
        # shift slots [index, n) right by one slot
        src = HEADER_SIZE + index * SLOT_SIZE
        dst = src + SLOT_SIZE
        count = (n - index) * SLOT_SIZE
        if count:
            self.data[dst : dst + count] = self.data[src : src + count]
        struct.pack_into("<HH", self.data, src, new_start, len(cell))
        self.data[0:16] = _hdr_pack(t, f, n + 1, new_start, nxt, e)
        self.page.mark_dirty()

    def replace_cell(self, index: int, cell: bytes) -> None:
        # delete + insert at same index (simple, may fragment; compact first)
        self.delete_cell(index)
        self.insert_cell(index, cell)

    def delete_cell(self, index: int) -> None:
        t, f, n, cell_start, nxt, e = self.header()
        if index < 0 or index >= n:
            raise IndexError(index)
        # Compact by rebuilding — avoids fragmentation after deletes/splits.
        cells = [self.cell(i) for i in range(n) if i != index]
        self.data[:] = b"\x00" * PAGE_SIZE
        self.data[0:16] = _hdr_pack(t, f, 0, PAGE_SIZE, nxt, e)
        self.page.mark_dirty()
        for i, c in enumerate(cells):
            self.insert_cell(i, c)

    def cells(self) -> list[bytes]:
        return [self.cell(i) for i in range(self.nslots())]


class BufferPool:
    def __init__(self, pager: "Pager", capacity: int = 64) -> None:
        self.pager = pager
        self.capacity = capacity
        self._cache: OrderedDict[int, Page] = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def get(self, pid: int) -> Page:
        if pid in self._cache:
            self._cache.move_to_end(pid)
            self.hits += 1
            return self._cache[pid]
        self.misses += 1
        page = self.pager.read_page(pid)
        self._cache[pid] = page
        self._evict_if_needed()
        return page

    def new_page(self) -> Page:
        pid = self.pager.allocate()
        page = Page(pid, bytearray(PAGE_SIZE))
        page.mark_dirty()
        self._cache[pid] = page
        self._evict_if_needed()
        return page

    def free_page(self, pid: int) -> None:
        self._cache.pop(pid, None)
        self.pager.free(pid)

    def _evict_if_needed(self) -> None:
        while len(self._cache) > self.capacity:
            pid, page = next(iter(self._cache.items()))
            if page.dirty:
                self.pager.write_page(page)
                page.dirty = False
            self._cache.pop(pid)
            self.evictions += 1

    def flush(self) -> None:
        for page in self._cache.values():
            if page.dirty:
                self.pager.write_page(page)
                page.dirty = False
        self.pager.flush()


class Pager:
    def __init__(self, path: str) -> None:
        self.path = path
        new = not os.path.exists(path) or os.path.getsize(path) == 0
        self.fp = open(path, "r+b" if not new else "w+b")
        if new:
            self.fp.write(b"\x00" * PAGE_SIZE * 2)
            self.fp.flush()
            self._init_meta()
        else:
            self._load_meta()

    def _init_meta(self) -> None:
        buf = bytearray(PAGE_SIZE)
        buf[0:4] = MAGIC
        struct.pack_into("<HHI II", buf, 4, 1, PAGE_SIZE, 2, 0, 1)
        # version u16, page_size u16, page_count u32, catalog_root u32, freemap_page u32
        # struct format: H H I I I at offset 4
        self.fp.seek(0)
        self.fp.write(buf)
        freemap = bytearray(PAGE_SIZE)
        # pages 0 and 1 allocated
        freemap[0] = 0b00000011
        self.fp.seek(PAGE_SIZE)
        self.fp.write(freemap)
        self.fp.flush()
        self.page_count = 2
        self.catalog_root = 0
        self.freemap_page = 1

    def _load_meta(self) -> None:
        self.fp.seek(0)
        buf = self.fp.read(PAGE_SIZE)
        if buf[0:4] != MAGIC:
            raise ValueError("not a MiniDB file")
        version, page_size, page_count, catalog_root, freemap_page = struct.unpack_from("<HHI II", buf, 4)
        if page_size != PAGE_SIZE:
            raise ValueError("unsupported page size")
        self.page_count = page_count
        self.catalog_root = catalog_root
        self.freemap_page = freemap_page

    def set_catalog_root(self, pid: int) -> None:
        self.catalog_root = pid
        self.fp.seek(4)
        self.fp.write(struct.pack("<HHI II", 1, PAGE_SIZE, self.page_count, pid, self.freemap_page))

    def _write_meta_counts(self) -> None:
        self.fp.seek(4)
        self.fp.write(
            struct.pack("<HHIII", 1, PAGE_SIZE, self.page_count, self.catalog_root, self.freemap_page)
        )

    def read_page(self, pid: int) -> Page:
        self.fp.seek(pid * PAGE_SIZE)
        raw = self.fp.read(PAGE_SIZE)
        if len(raw) < PAGE_SIZE:
            raw = raw + b"\x00" * (PAGE_SIZE - len(raw))
        return Page(pid, bytearray(raw))

    def write_page(self, page: Page) -> None:
        self.fp.seek(page.pid * PAGE_SIZE)
        self.fp.write(page.data)
        if page.pid == 0:
            return

    def flush(self) -> None:
        self._write_meta_counts()
        self.fp.flush()
        os.fsync(self.fp.fileno())

    def _freemap(self) -> bytearray:
        self.fp.seek(self.freemap_page * PAGE_SIZE)
        return bytearray(self.fp.read(PAGE_SIZE))

    def _write_freemap(self, fm: bytearray) -> None:
        self.fp.seek(self.freemap_page * PAGE_SIZE)
        self.fp.write(fm)

    def allocate(self) -> int:
        fm = self._freemap()
        for i, byte in enumerate(fm):
            if byte != 0xFF:
                for bit in range(8):
                    if not (byte & (1 << bit)):
                        pid = i * 8 + bit
                        fm[i] = byte | (1 << bit)
                        self._write_freemap(fm)
                        if pid >= self.page_count:
                            self.page_count = pid + 1
                            self.fp.seek(pid * PAGE_SIZE)
                            self.fp.write(b"\x00" * PAGE_SIZE)
                            self._write_meta_counts()
                        return pid
        raise RuntimeError("freemap exhausted (db too large for single bitmap page)")

    def free(self, pid: int) -> None:
        if pid < 2:
            return
        fm = self._freemap()
        i, bit = divmod(pid, 8)
        fm[i] &= ~(1 << bit)
        self._write_freemap(fm)

    def close(self) -> None:
        self.flush()
        self.fp.close()
