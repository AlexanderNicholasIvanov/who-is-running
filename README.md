# who-is-running

An open dataset of **who is running in US elections**, covering federal, state and local races. It's built only from free public sources and refreshed daily by GitHub Actions.

Each source writes one normalized CSV per year to `data/<source>/<year>.csv`, all with the same columns.

**Browse it:** https://alexandernicholasivanov.github.io/who-is-running/. Pick a state and year to see every race and who's running. Pick a county to see everything on that county's ballot: statewide races, the districts that include it, and its local races. Filter by level, primary/general, or a name or office search. Filtered views have shareable links, e.g. [`#state=NC&year=2026&county=WAKE`](https://alexandernicholasivanov.github.io/who-is-running/#state=NC&year=2026&county=WAKE).

## Sources

| Source | Coverage | Years | What it is |
|---|---|---|---|
| `fec` | President, US Senate, US House, all states | 2000–present (2-year cycles) | [FEC candidate master file](https://www.fec.gov/campaign-finance-data/candidate-master-file-description/) |
| `md` | Maryland: federal, state, legislative, judicial, county offices, school board, party central committees | 2022–present | [Maryland State Board of Elections candidate lists](https://elections.maryland.gov/candidacy/index.html) |
| `medsl` | **All 50 states + DC**: federal, state and local offices on the November ballot | 2018, 2022, 2024 | [MIT Election Data + Science Lab precinct returns](https://github.com/MEDSL) |
| `nc` | North Carolina, every level: federal, state, judicial, county, municipal, school board, special districts | 2010–present | [NC State Board of Elections candidate lists](https://www.ncsbe.gov/results-data/candidate-lists) |

There is no free national source of *upcoming* state and local candidates. `medsl` gives every state's state and local races for past general elections. Official candidate lists for upcoming elections (`nc`, `md`) are added one state at a time. See [Adding a source](#adding-a-source).

### Caveats

- **FEC data covers registrations, not ballots.** Anyone who files a federal Statement of Candidacy appears in it, including many people who never qualify for a ballot. Use `candidate_status` (`statutory_candidate` means they crossed the FEC's $5,000 threshold) to filter. A cycle file such as `fec/2026.csv` holds elections in 2025–2026; odd years are special elections.
- **MD files list each primary and general election's candidates.** Maryland renames its files every cycle, so each new election needs an entry in `ELECTIONS` in `sources/md.py`. Lists from before 2022 have no header row and aren't supported. City races are mostly absent, because cities run their own elections.
- **NC files are per calendar year** and include every primary, general and municipal election held that year.
- **MEDSL rows are general-election results reduced to candidates.** They show who appeared on the November ballot, not who filed: primary losers and withdrawn candidates are absent, and there is no 2026 data until MEDSL publishes it. Local coverage depends on what each state reports. Some state-years have no local races (for example California 2018 and New York), and a few files give only surnames (Texas 2022 and 2024). Write-ins, ballot questions and turnout lines are dropped. Candidates on several party lines (fusion voting) get one row, with the parties joined by `/`. Local races carry the county as `jurisdiction`, or the town in New England. 2020 is only on Harvard Dataverse and isn't included. The daily refresh skips this source because the data is historical. Re-pull it with `fetch --all-years --source medsl`.
- **District counties come from Census Bureau lines.** Candidate lists name a district but not the counties it spans, so FEC House races and Maryland congressional and legislative races get `counties` from the [Census Bureau's district-to-county relationship files](https://www.census.gov/geographies/reference-files/time-series/geo/relationship-files.html). Each election uses the lines it was held on: the 109th Congress for 2004, the 110th for 2006–2010, the 113th for 2012–2014, the 115th for 2016, the 116th for 2018–2020, the 118th for 2022 and the 119th for 2024. A district is listed in every county it shares land with, even a sliver. These are left empty because no published lines match them: 2000 and 2002, North Carolina in 2020 (court-ordered map), and in 2026 the ten states with new maps (AL, CA, FL, LA, MO, NC, OH, TN, TX, UT). Every other state's 2026 race uses its 2024 lines. Update `REDRAWN` in `districts.py` if a state's map changes. Maryland circuit-court judges are listed by circuit but elected county by county, so they're placed in the judge's county. Maryland appellate judges stay unplaced.
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
| `jurisdiction` | County (MEDSL: town in New England), when the race is confined to one. Wording varies by source, e.g. `WAKE` vs `Allegany County` |
| `counties` | Counties whose ballot has the race, `;`-separated (e.g. `DURHAM;WAKE`). `*` means every county in the state. District races are filled in from Census district lines (see Caveats). Empty means nobody says, e.g. 2026 House races in states with new maps |
| `candidate_name` | Name as the source gives it (FEC: `LAST, FIRST`; NC: as printed on the ballot) |
| `party` | Party code from the source (e.g. `DEM`, `REP`, `LIB`, `GRE`, `UNA`) |
| `election_type` | `primary`, `general`, `runoff`, or empty when the source doesn't say |
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

## Website

The site is static: plain HTML, CSS and JS in [`web/`](web/), with no framework or build tooling. `who-is-running build-site` copies `web/` to `_site/`. It converts each CSV to compact JSON, one file per state (`data/<source>/<year>/<state>.json`), and writes `data/manifest.json`, which lists every file and the states and years it covers, so the page downloads only the state it shows. Where sources overlap, the page by default prefers an official candidate list, then MEDSL results, then FEC registrations. [`.github/workflows/pages.yml`](.github/workflows/pages.yml) deploys to GitHub Pages on every push to `main` and after every data refresh.

### Results map

Above the candidate search, a map shows who won each state in past November general elections (2018, 2022 and 2024, from MEDSL): president, US Senate or governor, shaded by the winner's party and margin (under 5, 5–10, 10–20 and 20+ points). Pick a state, on the map or in the state picker, to see that state's counties for any of its statewide races, with each county's winner, margin and top candidates on hover or tap. A table view lists the same numbers.

- County totals are summed from MEDSL's precinct returns, using each precinct's `TOTAL` row when it has one and otherwise adding up its vote modes.
- Lines that repeat a county's precincts as one more precinct (Idaho 2022's `COUNTY TOTAL`, Michigan 2022's `TOTALS`) are skipped, so those counties aren't counted twice. Statewide court seats that MEDSL files as districts, such as North Carolina's 2018 Supreme Court race (`001`), are mapped as "SEAT 1" and so on.
- A race MEDSL's file misses counties for is left off the map rather than drawn wrong, except where `sources/medsl_fill.py` replaces it with the state's certified county results: California 2022 US Senate, regular and special (Secretary of State's Statement of Vote), Tennessee 2022 governor (Secretary of State), Idaho 2022 US Senate, lieutenant governor and schools superintendent (Secretary of State canvass), Indiana 2022 US Senate, secretary of state, auditor and treasurer (Election Division), and North Dakota 2024 governor ([OpenElections](https://github.com/openelections/openelections-data-nd)' copy of the Secretary of State's results, since the state's site keeps only its latest election). The candidate list still comes from MEDSL, so California's 2022 Senate candidates and Tennessee's 2022 governor candidates appear on the map but not in the list.
- Still missing: New York 2018 governor, US Senate, attorney general and comptroller, and New York 2022 US Senate and comptroller. MEDSL's files miss counties, and the State Board of Elections' site answers scripts with a browser challenge, so there's no fill yet.
- Some votes aren't reported by county (most of Alaska's early and absentee ballots, for example). They count toward the statewide result, and the map's note says what share the county shading leaves out. Kansas City, Missouri reports apart from its county, but only for the part of the city in Jackson County, so it's drawn as part of Jackson County. Kalawao County, Hawaii has no polling place; its votes are counted with Maui County, and the map says so.
- There are no 2020 results (MEDSL publishes them only on Harvard Dataverse) and none for 2026 until MEDSL publishes them.
- Shapes come from [us-atlas](https://github.com/topojson/us-atlas) (Census Bureau 1:10M boundaries, ISC license). `who-is-running build-geo` regenerates `web/geo/` from it.

Preview locally:

```sh
uv run who-is-running build-site --out _site
python -m http.server 8000 --directory _site   # then open http://localhost:8000
```

## Adding a source

1. Create `src/who_is_running/sources/<state>.py` with a `Source` subclass. It implements `available_years`, `refresh_years` and `candidacies(year)`, which returns `Candidacy` records. Accept the injected `fetch` callable instead of calling the network directly.
2. Register it in `src/who_is_running/sources/__init__.py`, and add a short label in `SOURCE_LABELS` in `web/app.js` and a link in the footer of `web/index.html`.
3. Add a small fixture made from real rows to `tests/fixtures/`, plus tests that use `FakeWeb`.

Good candidates are states whose election office publishes a statewide, machine-readable candidate list that includes local races at a stable URL.

## Development

```sh
uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest
```
