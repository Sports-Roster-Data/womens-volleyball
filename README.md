# Women's Volleyball Rosters

This repository contains roster information from more than 900 NCAA women's volleyball teams, including every active team from Division I, II and III. It was collected as part of a student project in JOUR479X, Sports Data Analysis and Visualization at the University of Maryland, with Alexa Henry, a graduate student in journalism.

This project was supervised and edited by Derek Willis, lecturer in data and computational journalism.

The original roster information is drawn from team websites, mostly obtained via [a scraper written in Python](src/rosters.py). That information has been augmented by individual research and editing. For example, a transfer player's high school may not be noted on her current team, but it could be found on her previous team's roster. Team information comes from [the NCAA](https://stats.ncaa.org/rankings?academic_year=2024&division=1.0&sport_code=WVB).

The roster data in this repository has been cleaned and standardized by contributors using R; a description of that process and the code is available in [this Quarto notebook](analysis/cleaning.qmd).

The specific information cleaned and parsed includes the following:

* Position (also standardized)
* Height (standardized and converted to total inches to make comparisons possible)
* Year (also standardized; because this information could refer to either academic or athletic eligibility, it may not be reflective of one of those - and COVID eligibility added more complexity)
* Hometown (parsed using the [postmastr](https://slu-opengis.github.io/postmastr/) package, then separated into hometown, state and country-specific fields. For foreign countries, we started with the list of FIBA nations and added others, then standardized the results)
* Previous School (probably the least consistent column in this data because of the way it is presented in the original data; we did make an attempt to move high schools listed in this column to the high school field)

We have *not* completely standardized the following data, at least not yet:

* High School
* Hometown
* Previous School

## Data files

The roster CSVs in `data/` share a 13-column schema:

`ncaa_id, team, player_id, name, year, hometown, high_school, previous_school, height, position, jersey, url, season`

| File | Contents |
| --- | --- |
| `data/rosters_<season>.csv` | Raw scraped rosters for a season (e.g. `rosters_2025-26.csv`). |
| `data/finished_rosters.csv` | Cleaned, geocoded roster data. |
| `data/teams.json` | The 900+ teams, their URLs, NCAA ids, and scraper routing (see below). `teams.csv` is the same data. |
| `data/vb_teams.csv` | Team metadata: conference, division, state. |

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

A full-season run overwrites `data/rosters_<season>.csv`. A targeted `-teams` run **merges** into the existing file, replacing only the teams it re-scraped and leaving every other team's rows intact. Both writes are atomic (temp file + rename), so an interrupted run never truncates the season file.

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
