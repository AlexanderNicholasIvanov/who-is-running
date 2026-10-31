"""Minimal HTTP fetching with retries, using only the standard library."""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from collections.abc import Callable

from who_is_running import __version__

Fetcher = Callable[[str], bytes]
"""Takes a URL, returns the response body. Sources accept one so tests can stub the network."""

# The usual "compatible" bot form: maine.gov and Tennessee's file server refuse
# agents that don't start "Mozilla/5.0".
USER_AGENT = (
    f"Mozilla/5.0 (compatible; who-is-running/{__version__}; "
    "+https://github.com/AlexanderNicholasIvanov/who-is-running)"
)


class NotFound(Exception):
    """The server answered 403/404: the file does not exist (S3 returns 403 for missing keys)."""


def http_get(url: str, *, retries: int = 3, timeout: float = 60.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body: bytes = response.read()
                return body
        except urllib.error.HTTPError as err:
            if err.code in (403, 404):
                raise NotFound(url) from err
            if attempt == retries or err.code < 500:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries:
                raise
        time.sleep(2**attempt)
    raise AssertionError("unreachable")
