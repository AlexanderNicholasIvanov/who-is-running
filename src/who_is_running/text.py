"""Decoding for government data files, which are not reliably UTF-8."""

from __future__ import annotations


def decode(raw: bytes) -> str:
    """Decode as UTF-8 (dropping any BOM), falling back to cp1252 line by line.

    Some files are UTF-8 with a few stray Windows-1252 bytes pasted in.
    Decoding the whole file as cp1252 would garble the valid UTF-8, so only
    the offending lines get the fallback.
    """
    raw = raw.removeprefix(b"\xef\xbb\xbf")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return "".join(_decode_line(line) for line in raw.splitlines(keepends=True))


def _decode_line(line: bytes) -> str:
    try:
        return line.decode("utf-8")
    except UnicodeDecodeError:
        return line.decode("cp1252", errors="replace")
