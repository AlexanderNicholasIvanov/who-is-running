# who-is-running

An open dataset of **who is running in US elections**, covering federal, state and local races. It's built only from free public sources and refreshed daily by GitHub Actions.

Each source writes one normalized CSV per year to `data/<source>/<year>.csv`, all with the same columns.

## Sources

| Source | Coverage | Years | What it is |
|---|---|---|---|
| `fec` | President, US Senate, US House, all states | 2000â€“present (2-year cycles) | [FEC candidate master file](https://www.fec.gov/campaign-finance-data/candidate-master-file-description/) |
| `md` | Maryland: federal, state, legislative, judicial, county offices, school board, party central committees | 2022–present | [Maryland State Board of Elections candidate lists](https://elections.maryland.gov/candidacy/index.html) |
| `nc` | North Carolina, every level: federal, state, judicial, county, municipal, school board, special districts | 2010â€“present | [NC State Board of Elections candidate lists](https://www.ncsbe.gov/results-data/candidate-lists) |

There is no free national source for state and local candidates, so coverage grows one state at a time. See [Adding a source](#adding-a-source).

### Caveats

- **FEC data covers registrations, not ballots.** Anyone who files a federal Statement of Candidacy appears in it, including many people who never qualify for a ballot. Use `candidate_status` (`statutory_candidate` means they crossed the FEC's $5,000 threshold) to filter. A cycle file such as `fec/2026.csv` holds elections in 2025â€“2026; odd years are special elections.
- **MD files list each primary and general election's candidates.** Maryland renames its files every cycle, so each new election needs an entry in `ELECTIONS` in `sources/md.py`. Lists from before 2022 have no header row and aren't supported. City races are mostly absent, because cities run their own elections.
- **NC files are per calendar year** and include every primary, general and municipal election held that year.
- **No contact details are published.** Sources include candidates' street addresses, phone numbers and emails. The pipeline deliberately drops them.

## Columns

| Column | Meaning |
|---|---|
| `state` | Two-letter code of the office's state (`US` for president) |
| `election_year` | Year of the election |
| `election_date` | ISO date, when the source provides it |
| `level` | `federal`, `state` or `local` |
| `office` | Office or contest name as the source words it (FEC offices normalized to `US House` / `US Senate` / `US President`) |
| `district` | District or seat identifier, when there is one |
| `jurisdiction` | County, when the race is confined to a single county (wording varies by source, e.g. `WAKE` vs `Allegany County`) |
| `candidate_name` | Name as the source gives it (FEC: `LAST, FIRST`; NC: as printed on the ballot) |
| `party` | Party code from the source (e.g. `DEM`, `REP`, `LIB`, `GRE`, `UNA`) |
| `election_type` | `primary`, `general`, or empty when the source doesn't say |
| `incumbent_status` | `incumbent`, `challenger`, `open_seat` (FEC only) |
| `candidate_status` | Source-specific status (FEC: registration status; MD: `active`, `withdrawn`, `disqualified`, …) |
| `source` | Which source the row came from |
| `source_candidate_id` | The source's ID for the candidate (FEC `CAND_ID`) |

An empty value always means "the source didn't provide it". The pipeline never guesses.

## Running it

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run who-is-running sources                         # list sources
uv run who-is-running fetch --refresh                 # re-pull recent years for every source
uv run who-is-running fetch --years 2026 --source nc  # one source, one year
uv run who-is-running fetch --all-years               # full historical backfill
```

## Data refresh

[`.github/workflows/refresh.yml`](.github/workflows/refresh.yml) runs daily, re-pulls each source's current and previous year, and commits any changes to `main`. To backfill history, run it manually with `mode = all-years`. If one source fails, the others still commit, and the run is then marked failed.

## Adding a source

1. Create `src/who_is_running/sources/<state>.py` with a `Source` subclass. It implements `available_years`, `refresh_years` and `candidacies(year)`, which returns `Candidacy` records. Accept the injected `fetch` callable instead of calling the network directly.
2. Register it in `src/who_is_running/sources/__init__.py`.
3. Add a small fixture made from real rows to `tests/fixtures/`, plus tests that use `FakeWeb`.

Good candidates are states whose election office publishes a statewide, machine-readable candidate list that includes local races at a stable URL.

## Development

```sh
uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest
```
