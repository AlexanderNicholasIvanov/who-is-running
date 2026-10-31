"""Read the cell values of an .xlsx workbook, with only the standard library.

An .xlsx file is a zip of XML parts: ``xl/workbook.xml`` names the sheets,
``xl/sharedStrings.xml`` holds the text cells point into, and each
``xl/worksheets/sheetN.xml`` lists its cells by reference ("B7"). Formatting,
formulas and dates aren't interpreted; every cell comes back as text, the way
the file stores it (numbers as "1234" or "1234.5").
"""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
from xml.etree import ElementTree

NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
CELL_REF = re.compile(r"^([A-Z]+)(\d+)$")


def sheets(raw: bytes) -> dict[str, list[list[str]]]:
    """Every sheet's rows, by sheet name, in workbook order. Missing cells are ""."""
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = set(zf.namelist())
        shared = (
            _shared_strings(zf.read("xl/sharedStrings.xml"))
            if ("xl/sharedStrings.xml" in names)
            else []
        )
        rels = ElementTree.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        targets = {
            rel.get("Id"): posixpath.normpath(posixpath.join("xl", rel.get("Target", "")))
            if not rel.get("Target", "").startswith("/")
            else rel.get("Target", "").lstrip("/")
            for rel in rels.findall("rel:Relationship", NS)
        }
        workbook = ElementTree.fromstring(zf.read("xl/workbook.xml"))
        out: dict[str, list[list[str]]] = {}
        for sheet in workbook.findall("m:sheets/m:sheet", NS):
            target = targets[sheet.get(f"{{{NS['r']}}}id")]
            out[sheet.get("name", "")] = _rows(zf.read(target), shared)
        return out


def _shared_strings(xml: bytes) -> list[str]:
    root = ElementTree.fromstring(xml)
    # A string may be split into formatted runs (<r><t>..</t></r>); join them.
    return ["".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")) for si in root]


def _rows(xml: bytes, shared: list[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in ElementTree.fromstring(xml).iterfind("m:sheetData/m:row", NS):
        cells: dict[int, str] = {}
        for i, cell in enumerate(row.iterfind("m:c", NS)):
            m = CELL_REF.match(cell.get("r", ""))
            col = _column(m.group(1)) if m else (max(cells) + 1 if cells else i)
            kind = cell.get("t", "n")
            if kind == "inlineStr":
                value = "".join(t.text or "" for t in cell.iter(f"{{{NS['m']}}}t"))
            else:
                v = cell.find("m:v", NS)
                value = v.text or "" if v is not None else ""
                if kind == "s" and value:
                    value = shared[int(value)]
            cells[col] = value
        index = int(row.get("r", len(rows) + 1)) - 1
        while len(rows) < index:
            rows.append([])  # rows the sheet leaves out are empty
        rows.append([cells.get(c, "") for c in range(max(cells) + 1)] if cells else [])
    return rows


def _column(letters: str) -> int:
    """ "A" -> 0, "Z" -> 25, "AA" -> 26."""
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - ord("A") + 1
    return n - 1
