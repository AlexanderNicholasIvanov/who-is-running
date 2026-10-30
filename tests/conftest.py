from __future__ import annotations

from pathlib import Path

from who_is_running.http import NotFound

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeWeb:
    """Stands in for http_get: serves canned bodies by URL substring, 404s otherwise."""

    def __init__(self, routes: dict[str, bytes]) -> None:
        self.routes = routes
        self.requested: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.requested.append(url)
        for fragment, body in self.routes.items():
            if fragment in url:
                return body
        raise NotFound(url)
