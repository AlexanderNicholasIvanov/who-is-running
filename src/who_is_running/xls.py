"""Read cell values from old-style Excel ``.xls`` workbooks (BIFF8), standard library only.

Maine publishes some of its ranked-choice summaries only as ``.xls``. This
reads just enough of the format to get each sheet's text and numbers:
formatting, formulas and dates are not interpreted (a formula cell gives its
cached numeric result, or nothing if the result is text).

An ``.xls`` file is an OLE2 "compound document": a little FAT file system
whose ``Workbook`` stream holds BIFF records.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator

Cell = str | float
Sheet = dict[tuple[int, int], Cell]
"""(row, column) -> value, both zero-based."""

MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
END_OF_CHAIN = 0xFFFFFFFE


class NotXls(ValueError):
    """The bytes aren't a BIFF8 workbook."""


def read_sheets(raw: bytes) -> list[tuple[str, Sheet]]:
    """Every worksheet in the workbook, in order, as (name, cells)."""
    return _parse_workbook(_workbook_stream(raw))


def rows(sheet: Sheet) -> list[list[Cell]]:
    """The sheet as a list of rows, with "" for empty cells."""
    if not sheet:
        return []
    width = max(c for _, c in sheet) + 1
    height = max(r for r, _ in sheet) + 1
    return [[sheet.get((r, c), "") for c in range(width)] for r in range(height)]


# --- compound document ----------------------------------------------------


def _workbook_stream(raw: bytes) -> bytes:
    if raw[:8] != MAGIC:
        raise NotXls("not an OLE2 compound document")
    sector_size = 1 << struct.unpack_from("<H", raw, 0x1E)[0]
    mini_size = 1 << struct.unpack_from("<H", raw, 0x20)[0]
    first_dir, _, cutoff, first_minifat, n_minifat, first_difat, n_difat = struct.unpack_from(
        "<7I", raw, 0x30
    )

    def sector(n: int) -> bytes:
        start = (n + 1) * sector_size
        return raw[start : start + sector_size]

    fat_sectors = [s for s in struct.unpack_from("<109I", raw, 0x4C) if s < END_OF_CHAIN]
    difat, per = first_difat, sector_size // 4 - 1
    for _ in range(n_difat):
        links = struct.unpack(f"<{per + 1}I", sector(difat))
        fat_sectors += [s for s in links[:per] if s < END_OF_CHAIN]
        difat = links[per]
    fat: list[int] = []
    for s in fat_sectors:
        fat += struct.unpack(f"<{sector_size // 4}I", sector(s))

    def chain(start: int, table: list[int]) -> Iterator[int]:
        seen = set()
        while start < END_OF_CHAIN and start < len(table) and start not in seen:
            seen.add(start)
            yield start
            start = table[start]

    def read_chain(start: int) -> bytes:
        return b"".join(sector(s) for s in chain(start, fat))

    directory = read_chain(first_dir)
    entries = []
    for off in range(0, len(directory) - 127, 128):
        name_len = struct.unpack_from("<H", directory, off + 64)[0]
        name = directory[off : off + max(name_len - 2, 0)].decode("utf-16-le", "replace")
        start, size = struct.unpack_from("<II", directory, off + 116)
        entries.append((name, directory[off + 66], start, size))
    if not entries:
        raise NotXls("empty directory")
    root_start = entries[0][2]
    for name, kind, start, size in entries:
        if kind != 2 or name not in ("Workbook", "Book"):
            continue
        if size >= cutoff:
            return read_chain(start)[:size]
        # Small streams live in the "mini stream", in 64-byte sectors.
        minifat: list[int] = []
        if n_minifat:
            data = read_chain(first_minifat)
            minifat = list(struct.unpack(f"<{len(data) // 4}I", data))
        container = read_chain(root_start)
        return b"".join(
            container[m * mini_size : (m + 1) * mini_size] for m in chain(start, minifat)
        )[:size]
    raise NotXls("no Workbook stream")


# --- BIFF records -----------------------------------------------------------

BOF, EOF, BOUNDSHEET, SST, CONTINUE = 0x0809, 0x000A, 0x0085, 0x00FC, 0x003C
LABELSST, LABEL, NUMBER, RK, MULRK, FORMULA = 0x00FD, 0x0204, 0x0203, 0x027E, 0x00BD, 0x0006


def _records(stream: bytes) -> Iterator[tuple[int, int, bytes]]:
    """(offset, type, data) for each record, with CONTINUE records left separate."""
    pos = 0
    while pos + 4 <= len(stream):
        kind, length = struct.unpack_from("<HH", stream, pos)
        yield pos, kind, stream[pos + 4 : pos + 4 + length]
        pos += 4 + length


def _parse_workbook(stream: bytes) -> list[tuple[str, Sheet]]:
    records = list(_records(stream))
    sheet_names: dict[int, str] = {}
    strings: list[str] = []
    for i, (_, kind, data) in enumerate(records):
        if kind == BOUNDSHEET and data[5] == 0:  # 0 = worksheet (not chart or macro)
            sheet_names[struct.unpack_from("<I", data)[0]] = _short_string(data[6:])
        elif kind == SST:
            chunks = [data]
            for _, k, d in records[i + 1 :]:
                if k != CONTINUE:
                    break
                chunks.append(d)
            strings = _sst(chunks)

    sheets: list[tuple[str, Sheet]] = []
    current: Sheet | None = None
    for offset, kind, data in records:
        if kind == BOF and offset in sheet_names:
            current = {}
            sheets.append((sheet_names[offset], current))
        elif current is None:
            continue
        elif kind == EOF:
            current = None
        elif kind == LABELSST:
            row, col, _, index = struct.unpack_from("<HHHI", data)
            if index < len(strings):
                current[row, col] = strings[index]
        elif kind == LABEL:
            row, col = struct.unpack_from("<HH", data)
            current[row, col] = _long_string(data[6:])
        elif kind == NUMBER:
            row, col, _, value = struct.unpack_from("<HHHd", data)
            current[row, col] = value
        elif kind == RK:
            row, col, _, rk = struct.unpack_from("<HHHI", data)
            current[row, col] = _rk(rk)
        elif kind == MULRK:
            row, first = struct.unpack_from("<HH", data)
            count = (len(data) - 6) // 6
            for n in range(count):
                current[row, first + n] = _rk(struct.unpack_from("<I", data, 6 + n * 6)[0])
        elif kind == FORMULA:
            row, col = struct.unpack_from("<HH", data)
            if data[12:14] != b"\xff\xff":  # otherwise the cached result isn't a number
                current[row, col] = struct.unpack_from("<d", data, 6)[0]
    return sheets


def _rk(rk: int) -> float:
    if rk & 2:
        value = float((rk >> 2) - (1 << 30) if rk & 0x80000000 else rk >> 2)
    else:
        value = struct.unpack("<d", struct.pack("<Q", (rk & 0xFFFFFFFC) << 32))[0]
    return value / 100 if rk & 1 else value


def _short_string(data: bytes) -> str:
    """ShortXLUnicodeString: 1-byte length, flags, characters."""
    length, flags = data[0], data[1]
    return _chars(data[2:], length, flags & 1)


def _long_string(data: bytes) -> str:
    """XLUnicodeString: 2-byte length, flags, characters."""
    length, flags = struct.unpack_from("<HB", data)
    return _chars(data[3:], length, flags & 1)


def _chars(data: bytes, count: int, wide: int) -> str:
    if wide:
        return data[: count * 2].decode("utf-16-le", "replace")
    return data[:count].decode("latin-1")


def _sst(chunks: list[bytes]) -> list[str]:
    """The shared string table, which may run on into CONTINUE records.

    A string's characters can break across a CONTINUE; the continuation then
    starts with a fresh flags byte saying whether it is 1- or 2-byte text.
    """
    chunk, pos = 0, 8  # skip the total and unique counts

    def take(n: int) -> bytes:
        nonlocal chunk, pos
        out = b""
        while n > 0 and chunk < len(chunks):
            if pos >= len(chunks[chunk]):
                chunk, pos = chunk + 1, 0
                continue
            part = chunks[chunk][pos : pos + n]
            out += part
            pos += len(part)
            n -= len(part)
        return out

    unique = struct.unpack_from("<I", chunks[0], 4)[0]
    strings: list[str] = []
    for _ in range(unique):
        if chunk < len(chunks) and pos >= len(chunks[chunk]):
            chunk, pos = chunk + 1, 0
        if chunk >= len(chunks):
            break
        header = take(3)
        if len(header) < 3:
            break
        count, flags = struct.unpack("<HB", header)
        runs = struct.unpack("<H", take(2))[0] if flags & 8 else 0
        ext = struct.unpack("<i", take(4))[0] if flags & 4 else 0
        wide = flags & 1
        text = ""
        while count > 0 and chunk < len(chunks):
            available = len(chunks[chunk]) - pos
            if available <= 0:
                chunk, pos = chunk + 1, 0
                if chunk < len(chunks):
                    wide = take(1)[0] & 1  # the continuation's own flags byte
                continue
            n = min(count, available // 2 if wide else available)
            if n == 0:  # a 2-byte character can't straddle records; move on
                chunk, pos = chunk + 1, 0
                continue
            text += _chars(take(n * 2 if wide else n), n, wide)
            count -= n
        take(runs * 4 + max(ext, 0))
        strings.append(text)
    return strings
