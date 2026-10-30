"""Registry of data sources. Add a new state by writing a Source and listing it here."""

from __future__ import annotations

from who_is_running.http import Fetcher, http_get
from who_is_running.sources.base import Source
from who_is_running.sources.fec import FecSource
from who_is_running.sources.md import MdSource
from who_is_running.sources.nc import NcSource

SOURCES: dict[str, type[Source]] = {
    cls.name: cls
    for cls in (
        FecSource,
        MdSource,
        NcSource,
    )
}


def get_source(name: str, fetch: Fetcher = http_get) -> Source:
    return SOURCES[name](fetch)


__all__ = ["SOURCES", "Source", "get_source"]
