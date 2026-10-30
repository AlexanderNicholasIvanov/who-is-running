# who-is-running

Python pipeline that pulls US candidate lists from free public sources and writes normalized CSVs to `data/<source>/<year>.csv`.

## Commands

- Setup: `uv sync`
- All checks (CI runs exactly these): `uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run pytest -q`
- Try a source against live data without touching `data/`: `uv run who-is-running fetch --years 2026 --source nc --data-dir /tmp/out`

## Layout

- `src/who_is_running/models.py`: `Candidacy`, the one normalized record. Field order = CSV column order = sort order.
- `src/who_is_running/sources/`: one module per publisher, registered in `sources/__init__.py`.
- `src/who_is_running/pipeline.py`: writes CSVs. It never overwrites a file with an empty result.
- `data/`: generated, and committed by the `Refresh data` workflow. Don't hand-edit it.

## Rules

- Runtime code is standard library only (`dependencies = []`). Keep it that way unless there's a strong reason.
- Tests never touch the network: sources take a `fetch` callable, and tests pass `FakeWeb` with fixtures built from real rows.
- Leave a field empty rather than guess. Don't publish candidate addresses, phone numbers or emails.
- Government files aren't reliably UTF-8. Decode with `who_is_running.text.decode`.
