# who-is-running

Python pipeline that pulls US candidate lists from free public sources and writes normalized CSVs to `data/<source>/<year>.csv`.

## Commands

- Setup: `uv sync`
- All checks (CI runs these plus a site build): `uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run pytest -q && node --input-type=module --check < web/app.js && node --input-type=module --check < web/map.js`
- Try a source against live data without touching `data/`: `uv run who-is-running fetch --years 2026 --source nc --data-dir /tmp/out`

## Layout

- `src/who_is_running/models.py`: `Candidacy`, the one normalized record. Field order = CSV column order = sort order.
- `src/who_is_running/sources/`: one module per publisher, registered in `sources/__init__.py`.
- `src/who_is_running/pipeline.py`: writes CSVs. It never overwrites a file with an empty result, except for sources with `expires` set (`civic`), whose empty result deletes the file.
- `src/who_is_running/sources/medsl.py`: all-states results. Precinct files are big (up to ~3M rows), so they're streamed with `text.decode_lines`, never loaded whole. 2020 is read from a commit-pinned GitHub copy and 2016 from MEDSL's older `official-precinct-returns` repo (`BASES`); 2016 uses the older column names. Results skip blank-district races that no candidate carries across 90% of their counties (really per-district races).
- `src/who_is_running/sources/civic.py`: Google Civic Information API, needs env `GOOGLE_CIVIC_API_KEY` (skipped without it). Skips `OFFICIAL_LISTS` states (NC, MD, VA). Samples Census-listed government street addresses per county, calls `voterinfo` for each and dedupes contests. Never let the key reach logs or exception text: wrap fetches in `_Client`, which redacts it.
- `src/who_is_running/sources/va.py`: Virginia ELECT's all-offices candidate workbook (.xlsx), found through the election's list page (`ELECTIONS`). One row per candidate per locality; localities listing the same candidates for the same office and district are one race. Never read the workbook's contact columns.
- `src/who_is_running/districts.py`: Census district-to-county lines, used to fill `counties` for district races. `HOUSE_LINES` maps each election year to its Congress; `REDRAWN` lists states whose map that year has no Census file yet. Keep the JS `countyKey` in `web/app.js` matching how sources and Census spell counties.
- `src/who_is_running/results.py`: `CountyResult`, vote totals per county for statewide races. MEDSL (certified) and `enr` (live) produce them (`Source.results`); the pipeline writes `data/results/<source>/<year>.csv`, and `build_site.build_results` turns those into `results/<year>/<state>.json` and `national.json` for the map. `MAP_COUNTIES` there maps result county codes onto the map's older county shapes, and `MAP_PLACES` folds places that report apart (Kansas City) into their county. Each result has a `stage` (`STAGES`: general, runoff, primary, primary runoff); every stage is its own race, each party's primary too, and `national.json` uses general elections only. `build_results` still reads results CSVs written before the stage columns, as general elections. Statewide ballot measures are results too: candidates `Yes` and `No`, no party (`measures.py` reads MEDSL's wordings and holds `PASS_RULES`, the states whose pass threshold is known). They never become candidacies.
- `src/who_is_running/sources/medsl_fill.py`: replaces the county results of statewide races MEDSL's files get wrong with the state's certified ones (`FILLS`). A fill replaces a race, never adds to it. `src/who_is_running/xlsx.py` reads the .xlsx files some states publish.
- `src/who_is_running/sources/enr.py`: live, unofficial election-night results from statewide Clarity ENR sites and Florida's and Minnesota's own files; results only, no candidates. `FEEDS` lists each year's state feeds; a blank Clarity election ID is looked up in the site's `elections.json` by date. `Source.results_status` adds `data/results/enr/<year>.json` (precincts reporting, last update), and a results CSV with that file beside it counts as live: `build_results` uses it only for states that no certified file covers that year. `.github/workflows/live-results.yml` pulls it every 10 minutes on election night.
- `src/who_is_running/rounds.py` and `sources/rcv.py`: ranked-choice rounds (Alaska, Maine). `RcvSource` has no candidacies, only `Source.rounds`; the pipeline writes `data/rounds/<source>/<year>.csv`, and `build_site.build_rounds` writes `results/<year>/rcv.json`, which `map.js` and `app.js` match to races by state, office, district and election type. Report URLs are listed by hand in `REPORTS`, with office and district spelled as MEDSL spells them. `xls.py` and `pdf.py` are minimal stdlib readers for the states' `.xls` and PDF reports.
- `src/who_is_running/geo.py` and `web/geo/`: state and county shapes for the map, pre-drawn as SVG paths from us-atlas. Committed; regenerate only with `uv run who-is-running build-geo`.
- `web/map.js`: the results map (national view by state, click for counties). Same `esc()` rule as `app.js`. The territories (`TERRITORIES`) aren't in us-atlas, so the national view draws them as a row of tiles under the country.
- `models.house_office`: the office name for a state's House seat, `US House Delegate` (DC, territories) or `US House Resident Commissioner` (PR). FEC and MEDSL both use it.
- `web/`: the static site (vanilla JS, no build step). `build_site.py` turns `data/` into `_site/` for GitHub Pages. Preview it with `build-site` then `python -m http.server --directory _site`.
- `data/`: generated, and committed by the `Refresh data` workflow. Don't hand-edit it.

## Rules

- Runtime code is standard library only (`dependencies = []`). Keep it that way unless there's a strong reason.
- Tests never touch the network: sources take a `fetch` callable, and tests pass `FakeWeb` with fixtures built from real rows.
- Leave a field empty rather than guess. Don't publish candidate addresses, phone numbers or emails.
- The web page renders untrusted source text. Always pass it through `esc()` in `app.js`; never insert raw HTML.
- Government files aren't reliably UTF-8. Decode with `who_is_running.text.decode`.
