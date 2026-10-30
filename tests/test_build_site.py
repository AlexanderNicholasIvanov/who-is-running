from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import FIXTURES

from who_is_running.build_site import build
from who_is_running.models import Candidacy

WEB = Path(__file__).parent.parent / "web"
DATA = FIXTURES / "site_data"


def test_build_writes_site_manifest_and_json(tmp_path: Path) -> None:
    out = tmp_path / "_site"

    manifest = build(DATA, WEB, out)

    assert (out / "index.html").exists()
    assert (out / "app.js").exists()
    assert set(manifest["sources"]) == {"fec", "nc"}

    files = {(f["source"], f["year"]): f for f in manifest["files"]}
    fec = files[("fec", 2026)]
    assert fec["coverage"]["NC"] == {"2025": 3, "2026": 3}
    assert fec["coverage"]["US"] == {"2026": 1}
    assert files[("nc", 2026)]["rows"] == 5

    on_disk = json.loads((out / "data" / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk["files"] == manifest["files"]

    payload = json.loads((out / "data" / "nc" / "2026" / "NC.json").read_text(encoding="utf-8"))
    assert payload["columns"] == Candidacy.columns()
    assert len(payload["rows"]) == 5
    assert (out / "data" / "nc" / "2026.csv").exists()

    # One JSON per state, so a page only downloads the state it shows.
    fec_dir = out / "data" / "fec" / "2026"
    assert sorted(p.name for p in fec_dir.iterdir()) == [
        f"{s}.json" for s in sorted(fec["coverage"])
    ]
    us = json.loads((fec_dir / "US.json").read_text(encoding="utf-8"))
    assert [row[0] for row in us["rows"]] == ["US"]


def test_build_replaces_previous_output(tmp_path: Path) -> None:
    out = tmp_path / "_site"
    out.mkdir()
    (out / "stale.txt").write_text("old")

    build(DATA, WEB, out)

    assert not (out / "stale.txt").exists()


def test_build_rejects_unexpected_columns(tmp_path: Path) -> None:
    data = tmp_path / "data" / "xx"
    data.mkdir(parents=True)
    (data / "2026.csv").write_text("name,office\nA,B\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unexpected columns"):
        build(tmp_path / "data", WEB, tmp_path / "_site")


def test_page_references_files_that_exist() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for asset in ("style.css", "app.js"):
        assert f'"{asset}"' in html
        assert (WEB / asset).exists()
