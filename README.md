# Women's Volleyball Rosters

This repository contains roster information from more than 900 NCAA women's volleyball teams, including every active team from Division I, II and III. It was collected as part of a student project in JOUR479X, Sports Data Analysis and Visualization at the University of Maryland, with Alexa Henry, a graduate student in journalism.

This project was supervised and edited by Derek Willis, lecturer in data and computational journalism.

The original roster information is drawn from team websites, mostly obtained via [a scraper written in Python](src/rosters.py). That information has been augmented by individual research and editing. For example, a transfer player's high school may not be noted on her current team, but it could be found on her previous team's roster. Team information comes from [the NCAA](https://stats.ncaa.org/rankings?academic_year=2024&division=1.0&sport_code=WVB).

## Building the cleaned data

The raw roster CSVs are the build's only source, and their bytes are frozen:
`data/reference/raw_snapshots.csv` records each season's file, canonical
academic-year label (`2015-16` … `2026-27`) and SHA-256 hash, which the build
re-validates every time it runs. The notebook in
[analysis/cleaning.qmd](analysis/cleaning.qmd) renders a human-readable
quality report from the build's outputs; all transformations live in tested R
functions in [R/roster_cleaning.R](R/roster_cleaning.R).

Schema version and column-by-column documentation, including the legacy →
new column mapping, are in [docs/data_dictionary.md](docs/data_dictionary.md).

### Setup

```bash
Rscript -e 'renv::restore()'   # installs the R dependency versions in renv.lock
uv run pytest                  # scraper + pytest additions
```

### Build a season

```bash
Rscript scripts/build_rosters.R --season 2026-27    # single season
Rscript scripts/build_rosters.R --all               # all seasons, chronologically
Rscript scripts/build_rosters.R --all --render      # + notebooks/quality report
```

The build reads the registered raw snapshot, applies the reference tables and
the reviewed corrections, and writes:

```
data/clean/vb_rosters_<season>.csv
data/derived/baseline/vb_rosters_<season>.csv
data/derived/quarantine/vb_rosters_<season>.csv
data/derived/audits/<season>/{row_issues,duplicate_review,team_coverage,
                              quality_summary,reconciliation}.csv
```

Structural problems quarantine the individual row with a reason; only
deterministic formatting is fixed automatically, and raw cells are never
rewritten (cell values move only through reviewed corrections in `corrections/`).
The build fails when rows cannot be reconciled, when a team with raw rows
publishes nothing, or when duplicate groups are unresolved — pass
`--allow-partial` to ship anyway (for example while a first review is pending).
Rebuilds are byte-identical: outputs carry no timestamps.

### Tests

```bash
Rscript -e 'testthat::test_dir("tests/testthat")'
uv run pytest
```

### Raw immutability

Never edit `data/rosters_*.csv` in place. The scraper archives any
prior raw vintage it would overwrite to `data/raw_archive/` (byte-for-byte)
and new/changed snapshots are registered with `scripts/register_snapshot.py`;
see the scraper section below. Corrections to data go through
`corrections/cell_corrections.csv` / `row_corrections.csv` with a reason and
reviewed-on date, or into the reference tables when a fix is systematic.

## Data files

The roster CSVs in `data/` share a 13-column schema:

`ncaa_id, team, player_id, name, year, hometown, high_school, previous_school, height, position, jersey, url, season`

| File | Contents |
| --- | --- |
| `data/rosters_<season>.csv` | Raw scraped rosters for a season; files use the full academic-year label, e.g. `rosters_2025-26.csv`. |
| `data/reference/` | Build inputs: snapshot registry, canonical teams and affiliations, position/class/state/country lookups, reviewed school mappings, exclusions and coverage exceptions. |
| `data/finished_rosters.csv` | Legacy cleaned, geocoded roster data (superseded; kept for reference). |
| `data/teams.json` | The 900+ teams, their URLs, NCAA ids, and scraper routing (see below). `teams.csv` is the same data — scraper configuration, not the analytical team dimension. |
| `data/vb_teams.csv` | Team metadata: conference, division, state. |
| `data/clean/` | Published clean season CSVs from the build. |
| `data/derived/` | Build-time baselines, quarantines and audits. |

## Running the scraper

The scraper lives in [src/rosters.py](src/rosters.py) and is managed with [uv](https://docs.astral.sh/uv/).

### Setup

```bash
uv sync
uv run shot-scraper install   # downloads the Chromium browser used for JS-rendered pages (~200MB)
```

### Usage

Run from the repository root (the script reads and writes paths relative to it):

```bash
# Scrape every team for a season
uv run python src/rosters.py -season 2025-26
```

```bash
# Scrape one or more specific teams by NCAA id
uv run python src/rosters.py -season 2025-26 -teams 721 5
```

```bash
# Resume an interrupted run: teams already scraped successfully for this
# season are served from cache instead of re-fetched
uv run python src/rosters.py -season 2025-26 -resume
```

A full-season run writes `data/rosters_<season>.csv` using the full academic-year label (a `-season 2025` run now writes `data/rosters_2025-26.csv` and leaves any older year-only file untouched). If a write would change an existing raw file, the prior bytes are archived verbatim to `data/raw_archive/` first; run `uv run python scripts/register_snapshot.py -season 2025-26` to register the new snapshot (the build refuses an unregistered season). A targeted `-teams` run **merges** into the existing file, replacing only the teams it re-scraped and leaving every other team's rows intact. Both writes are atomic (temp file + rename), so an interrupted run never truncates the season file. Every successful team's rows are also cached to `data/cache/rosters_<season>/`; `-resume` reads that cache, and deleting the directory forces a full re-scrape. Failed and empty teams are never cached, so a resumed run still retries them.

### Season verification

Pages fetched at a current-roster URL (e.g. bare `/roster` on JavaScript-rendered sites, which a `/roster/<season>` path 404s on) are checked for year evidence in the page title and headings. A page that positively identifies a *different* season is rejected rather than scraped under the requested season's label; pages with no year evidence are accepted. This prevents, say, a November run for `2026-27` from silently recording a site's stale `2025-26` roster as current.

### How team routing works

Most teams use standard [Sidearm Sports](https://sidearmsports.com/) roster pages and are parsed directly. Teams whose sites need JavaScript rendering or a bespoke parser carry a `"scraper"` key in `data/teams.json` that names their strategy:

| `scraper` value | Handler |
| --- | --- |
| `js_render` | Fetch with a headless browser, then parse. |
| `shotscraper_card` / `_table` / `_list_item` / `_roster_player` / `_data_tables` | Extract a specific DOM layout via shot-scraper. |
| `byu`, `clemson`, `miami`, `vandy`, `sanjose`, `iowa_state`, `airforce` | One-off per-team parsers. |
| `skip` | Never scrape this team. Use `skip_seasons` to skip only certain seasons. |

Teams without a `"scraper"` key fall back to URL-pattern routing.

### Adding or fixing a team

1. Edit the team's entry in `data/teams.json` — set or change its `"scraper"` key to the strategy that matches its site.
2. Capture a fixture of the page so the parser can be tested offline:
   ```bash
   uv run python tests/capture_fixture.py parse_roster <name> <roster-url> 2025-26
   ```
3. Add assertions for it in `tests/test_parsers.py` and run the tests:
   ```bash
   uv run --with pytest pytest
   ```

### Troubleshooting

* Some athletics sites return `403` to Python's `requests`; the scraper automatically retries those with `curl`.
* JavaScript-rendered pages require the shot-scraper browser — run `uv run shot-scraper install` if scraping returns empty rosters for `js_render` teams.

## Contributing

We welcome comments, corrections and questions. Please use [this repository's Issues](https://github.com/Sports-Roster-Data/womens-volleyball/issues) to let us know about any errors or omissions, or submit a pull request with any changes.
