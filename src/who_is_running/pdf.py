"""Pull positioned text lines out of simple PDF reports, standard library only.

Some ranked-choice tabulations are published only as PDF. This is not a
general PDF reader: it handles the machine-generated reports we use (text
drawn with ``Tj``/``TJ`` from literal strings, Flate-compressed content
streams, objects possibly packed in object streams). Text in hex strings, which
needs a font's glyph map to decode, is skipped. Scanned PDFs have no text at
all, so they give no lines.

Each page becomes a list of lines, top to bottom; each line is the text pieces
on it, left to right. Tables come out one row per line.
"""

from __future__ import annotations

import re
import zlib
from collections.abc import Iterator

Line = list[str]

OBJ = re.compile(rb"(?<![0-9])(\d+)\s+(\d+)\s+obj\b")
REF = re.compile(rb"(\d+)\s+\d+\s+R")
TOKEN = re.compile(
    rb"\((?:\\.|[^\\)])*\)"  # literal string (nested parens are rare; escaped ones are handled)
    rb"|<<|>>|<[0-9A-Fa-f\s]*>"  # dictionary delimiters, hex string
    rb"|\[|\]"
    rb"|/[^\s/\[\]()<>{}%]+"  # name
    rb"|[-+]?(?:\d+\.?\d*|\.\d+)"  # number
    rb"|[A-Za-z'\"*][A-Za-z0-9*]*",  # operator
    re.S,
)
ESCAPES = {
    b"n": b"\n",
    b"r": b"\r",
    b"t": b"\t",
    b"b": b"\b",
    b"f": b"\f",
    b"(": b"(",
    b")": b")",
    b"\\": b"\\",
}


def pages(raw: bytes) -> list[list[Line]]:
    """The text lines of each page, in page order."""
    objects = _objects(raw)
    out = []
    for page in _page_objects(objects):
        content = b"\n".join(_stream(objects, n) for n in _contents(page))
        out.append(_lines(_text_items(content)))
    return out


# --- objects ------------------------------------------------------------------


def _objects(raw: bytes) -> dict[int, bytes]:
    """Object number -> its bytes (dictionary, plus stream if any). Later definitions win."""
    found: dict[int, bytes] = {}
    matches = list(OBJ.finditer(raw))
    for m, nxt in zip(matches, [*matches[1:], None], strict=True):
        end = raw.find(b"endobj", m.end(), nxt.start() if nxt else len(raw))
        found[int(m.group(1))] = raw[m.end() : end if end != -1 else (nxt.start() if nxt else None)]
    # Objects packed inside object streams (PDF 1.5+).
    for body in list(found.values()):
        head = body[: body.find(b"stream")] if b"stream" in body else body
        if b"/ObjStm" not in head:
            continue
        data = _decode(body)
        first = re.search(rb"/First\s+(\d+)", head)
        if not data or not first:
            continue
        start = int(first.group(1))
        pairs = [int(n) for n in data[:start].split()]
        offsets = list(zip(pairs[::2], pairs[1::2], strict=False))
        for i, (num, off) in enumerate(offsets):
            stop = offsets[i + 1][1] if i + 1 < len(offsets) else len(data) - start
            found.setdefault(num, data[start + off : start + stop])
    return found


def _decode(body: bytes) -> bytes:
    """The (inflated) stream data of an object, or b"" if it has none we can read."""
    m = re.search(rb"stream\r?\n", body)
    if not m:
        return b""
    head, data = body[: m.start()], body[m.end() :]
    end = data.rfind(b"endstream")
    data = data[:end] if end != -1 else data
    if b"/FlateDecode" in head:
        try:
            return zlib.decompressobj().decompress(data)
        except zlib.error:
            return b""
    if b"/Filter" in head:
        return b""  # an image codec or similar: not text
    return data


def _stream(objects: dict[int, bytes], num: int) -> bytes:
    return _decode(objects.get(num, b""))


def _page_objects(objects: dict[int, bytes]) -> Iterator[bytes]:
    """Page dictionaries in reading order, following the page tree from its root."""
    kids_of = {n: b for n, b in objects.items() if re.search(rb"/Type\s*/Pages\b", _head(b))}
    roots = [n for n, b in kids_of.items() if b"/Parent" not in _head(b)]

    def walk(num: int, seen: set[int]) -> Iterator[bytes]:
        if num in seen:
            return
        seen.add(num)
        body = _head(objects.get(num, b""))
        if re.search(rb"/Type\s*/Page\b", body):
            yield body
            return
        kids = re.search(rb"/Kids\s*\[([^\]]*)\]", body)
        for ref in REF.finditer(kids.group(1) if kids else b""):
            yield from walk(int(ref.group(1)), seen)

    seen: set[int] = set()
    for root in roots:
        yield from walk(root, seen)


def _head(body: bytes) -> bytes:
    cut = re.search(rb"stream\r?\n", body)
    return body[: cut.start()] if cut else body


def _contents(page: bytes) -> list[int]:
    m = re.search(rb"/Contents\s*(\[[^\]]*\]|\d+\s+\d+\s+R)", page)
    return [int(r.group(1)) for r in REF.finditer(m.group(1))] if m else []


# --- text ---------------------------------------------------------------------


def _unescape(s: bytes) -> str:
    out = bytearray()
    i = 0
    while i < len(s):
        c = s[i : i + 1]
        if c != b"\\":
            out += c
            i += 1
            continue
        nxt = s[i + 1 : i + 2]
        if nxt in ESCAPES:
            out += ESCAPES[nxt]
            i += 2
            continue
        octal = re.match(rb"[0-7]{1,3}", s[i + 1 : i + 4])
        if octal:
            out.append(int(octal.group(), 8) & 0xFF)
            i += 1 + len(octal.group())
            continue
        i += 1  # a backslash before a line break, or before nothing special
    # Simple fonts in these reports use the standard Windows/PDF Latin encoding.
    return out.decode("cp1252", "replace")


def _text_items(content: bytes) -> list[tuple[float, float, str]]:
    """(y, x, text) for each string drawn. Positions come from Tm/Td/TD/T* and cm."""
    items: list[tuple[float, float, str]] = []
    operands: list[bytes] = []
    array: list[str] | None = None
    ctm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    saved: list[list[float]] = []
    line_x = line_y = 0.0
    leading = 0.0
    pending = ""

    def numbers(n: int) -> list[float] | None:
        try:
            return [float(v) for v in operands[-n:]] if len(operands) >= n else None
        except ValueError:
            return None

    def place(text: str) -> None:
        x = ctm[0] * line_x + ctm[2] * line_y + ctm[4]
        y = ctm[1] * line_x + ctm[3] * line_y + ctm[5]
        if text.strip():
            items.append((y, x, text))

    for tok in TOKEN.findall(content):
        if tok == b"[":
            array = []
            continue
        if array is not None:
            if tok == b"]":
                operands.append(b"")  # stands in for the array
                pending = "".join(array)
                array = None
                continue
            if tok.startswith(b"("):
                array.append(_unescape(tok[1:-1]))
            continue
        if not (tok[:1].isalpha() or tok in (b"'", b'"')):
            operands.append(tok)
            continue
        op = tok
        if op == b"q":
            saved.append(ctm[:])
        elif op == b"Q" and saved:
            ctm = saved.pop()
        elif op == b"cm" and (m := numbers(6)):
            a, b, c, d, e, f = m
            ctm = [
                a * ctm[0] + b * ctm[2],
                a * ctm[1] + b * ctm[3],
                c * ctm[0] + d * ctm[2],
                c * ctm[1] + d * ctm[3],
                e * ctm[0] + f * ctm[2] + ctm[4],
                e * ctm[1] + f * ctm[3] + ctm[5],
            ]
        elif op == b"BT":
            line_x = line_y = 0.0
        elif op == b"Tm" and (m := numbers(6)):
            line_x, line_y = m[4], m[5]
        elif op in (b"Td", b"TD") and (m := numbers(2)):
            line_x += m[0]
            line_y += m[1]
            if op == b"TD":
                leading = -m[1]
        elif op == b"TL" and (m := numbers(1)):
            leading = m[0]
        elif op == b"T*":
            line_y -= leading
        elif op == b"Tj" and operands and operands[-1].startswith(b"("):
            place(_unescape(operands[-1][1:-1]))
        elif op == b"TJ" and operands and operands[-1] == b"":
            place(pending)
        elif op in (b"'", b'"') and operands and operands[-1].startswith(b"("):
            line_y -= leading
            place(_unescape(operands[-1][1:-1]))
        operands = []
    return items


def _lines(items: list[tuple[float, float, str]], tolerance: float = 2.0) -> list[Line]:
    """Group text pieces into lines by baseline (top to bottom), each left to right."""
    rows: list[tuple[float, list[tuple[float, str]]]] = []
    for y, x, text in sorted(items, key=lambda i: (-i[0], i[1])):
        if rows and abs(rows[-1][0] - y) <= tolerance:
            rows[-1][1].append((x, text))
        else:
            rows.append((y, [(x, text)]))
    return [[t.strip() for _, t in sorted(pieces)] for _, pieces in rows]
