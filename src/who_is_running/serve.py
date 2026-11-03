"""Serve the built site on this computer, for ``who-is-running serve`` and the launchers."""

from __future__ import annotations

import functools
import logging
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

HOST = "127.0.0.1"
"""Only this computer can open the site."""
PORT = 8000


class Handler(SimpleHTTPRequestHandler):
    # Windows can map .js to text/plain in its registry, and browsers refuse to
    # run a module script served that way.
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".json": "application/json",
        ".csv": "text/csv; charset=utf-8",
    }

    def log_message(self, format: str, *args: Any) -> None:
        pass  # one line per file the page loads is noise


class Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second server take a port another one is
    # listening on; elsewhere it only skips the wait after a restart.
    allow_reuse_address = sys.platform != "win32"


def make_server(site: Path, port: int = PORT) -> Server:
    """A server for ``site`` on ``port``, or any free port if that one is taken."""
    handler = functools.partial(Handler, directory=str(site))
    try:
        return Server((HOST, port), handler)
    except OSError:
        log.info("port %d is in use; picking another", port)
        return Server((HOST, 0), handler)


def serve(site: Path, port: int = PORT, *, open_browser: bool = True) -> None:
    """Serve ``site`` until Ctrl+C."""
    with make_server(site, port) as server:
        url = f"http://localhost:{server.server_address[1]}/"
        log.info("serving %s at %s (Ctrl+C to stop)", site, url)
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            log.info("stopped")
