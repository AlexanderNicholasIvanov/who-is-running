# who-is-running

Python pipeline that pulls US candidate lists from free public sources and writes normalized CSVs to `data/<source>/<year>.csv`.

## Commands

- Setup: `uv sync`
- All checks (CI runs these plus a site build): `uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run pytest -q && node --input-type=module --check < web/app.js && node --input-type=module --check < web/map.js`
- Try a source against live data without touching `data/`: `uv run who-is-running fetch --years 2026 --source nc --data-dir /tmp/out`

## Layout

- `src/who_is_running/models.py`: `Candidacy`, the one normalized record. Field order = CSV column order = sort order.
- `src/who_is_running/sources/`: one module per publisher, registered in `sources/__init__.py`.
- `src/who_is_running/pipeline.py`: writes CSVs. It never overwrites a file with an empty result.
- `src/who_is_running/sources/medsl.py`: all-states results. Precinct files are big (up to ~3M rows), so they're streamed with `text.decode_lines`, never loaded whole. 2020 is read from a commit-pinned GitHub copy and 2016 from MEDSL's older `official-precinct-returns` repo (`BASES`); 2016 uses the older column names. Results skip blank-district races that no candidate carries across 90% of their counties (really per-district races).
- `src/who_is_running/districts.py`: Census district-to-county lines, used to fill `counties` for district races. `HOUSE_LINES` maps each election year to its Congress; `REDRAWN` lists states whose map that year has no Census file yet. Keep the JS `countyKey` in `web/app.js` matching how sources and Census spell counties.
- `src/who_is_running/results.py`: `CountyResult`, vote totals per county for statewide races. Only MEDSL produces them (`Source.results`); the pipeline writes `data/results/<source>/<year>.csv`, and `build_site.build_results` turns those into `results/<year>/<state>.json` and `national.json` for the map. `MAP_COUNTIES` there maps result county codes onto the map's older county shapes.
- `src/who_is_running/geo.py` and `web/geo/`: state and county shapes for the map, pre-drawn as SVG paths from us-atlas. Committed; regenerate only with `uv run who-is-running build-geo`.
- `web/map.js`: the results map (national view by state, click for counties). Same `esc()` rule as `app.js`.
- `web/`: the static site (vanilla JS, no build step). `build_site.py` turns `data/` into `_site/` for GitHub Pages. Preview it with `build-site` then `python -m http.server --directory _site`.
- `data/`: generated, and committed by the `Refresh data` workflow. Don't hand-edit it.

## Rules

- Runtime code is standard library only (`dependencies = []`). Keep it that way unless there's a strong reason.
- Tests never touch the network: sources take a `fetch` callable, and tests pass `FakeWeb` with fixtures built from real rows.
- Leave a field empty rather than guess. Don't publish candidate addresses, phone numbers or emails.
- The web page renders untrusted source text. Always pass it through `esc()` in `app.js`; never insert raw HTML.
- Government files aren't reliably UTF-8. Decode with `who_is_running.text.decode`.
