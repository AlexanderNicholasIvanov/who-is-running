"""The local server behind ``who-is-running serve``. It only talks to this computer."""

from __future__ import annotations

import threading
import urllib.request
from pathlib import Path

import pytest

from who_is_running import serve
from who_is_running.cli import main


def _site(tmp_path: Path) -> Path:
    site = tmp_path / "_site"
    site.mkdir()
    (site / "index.html").write_text("<h1>who is running</h1>", encoding="utf-8")
    (site / "app.js").write_text("export {};", encoding="utf-8")
    return site


def test_serves_the_site_to_this_computer_only(tmp_path: Path) -> None:
    with serve.make_server(_site(tmp_path), 0) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        host, port = server.server_address[:2]
        url = f"http://127.0.0.1:{port}"
        try:
            with urllib.request.urlopen(f"{url}/") as page:
                assert b"who is running" in page.read()
            with urllib.request.urlopen(f"{url}/app.js") as script:
                # Browsers only run module scripts served as JavaScript.
                assert script.headers["Content-Type"] == "text/javascript"
        finally:
            server.shutdown()
    assert host == "127.0.0.1"


def test_a_busy_port_falls_back_to_a_free_one(tmp_path: Path) -> None:
    site = _site(tmp_path)
    with serve.make_server(site, 0) as first:
        taken = first.server_address[1]
        with serve.make_server(site, taken) as second:
            assert second.server_address[1] not in (0, taken)


def test_cli_builds_then_serves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Path, int, bool]] = []
    monkeypatch.setattr(
        serve, "serve", lambda site, port, open_browser: calls.append((site, port, open_browser))
    )
    out = tmp_path / "_site"
    data = tmp_path / "data"
    data.mkdir()

    assert main(["serve", "--data-dir", str(data), "--out", str(out), "--no-browser"]) == 0

    assert (out / "index.html").exists()
    assert calls == [(out, serve.PORT, False)]


def test_cli_without_a_build_needs_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve, "serve", lambda *a, **k: pytest.fail("served an empty folder"))
    assert main(["serve", "--no-build", "--out", str(tmp_path / "missing")]) == 1
