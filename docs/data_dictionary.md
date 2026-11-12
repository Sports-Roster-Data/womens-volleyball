# Data dictionary — cleaned volleyball rosters

Applies to every file published by the build (`data/clean/vb_rosters_<season>.csv`,
`data/derived/baseline/…`, `data/derived/quarantine/…`) as of schema version **2.0**.
Column order below is the published column order.

## Build contract

```
active raw snapshot -> structural baseline -> normalization -> corrections -> validated clean season
```

- Grain: **one accepted roster entry per team-season**, identified by
  `source_row_id`. It is not a claim about a person across seasons
  (cross-season player IDs are a later phase).
- Every output is deterministic: a repeated build is byte-identical. Data
  files never carry generated timestamps.
- Reconciliation enforced at build time:

```
raw rows = clean rows + quarantined rows + exact-duplicate removals
         + reviewed duplicate removals + reviewed source exclusions
```

## Reference tables (data/reference/)

| File | Role |
|---|---|
| `raw_snapshots.csv` | Registered raw snapshots: `season, snapshot_id, raw_path, raw_season_value, sha256, retrieved_at, active, notes`. One active snapshot per canonical season; hashes validated at build time. |
| `teams_canonical.csv` | One row per NCAA ID: canonical name, state, division, plus registry-disagreement notes. |
| `team_affiliations.csv` | `ncaa_id, first_season, last_season, division, conference, source, note`. Conference values are intentionally blank until reviewed season ranges exist — current conference labels are not copied backward without evidence. |
| `positions.csv` | Raw position → canonical code. Single active source of truth (replaces `data/raw_positions.csv` / `data/cleaned_positions.csv`). |
| `classes.csv` | Raw class year → canonical class + redshirt flag. Single active source (replaces `data/years_cleaned.csv` in the build path). |
| `state_variants.csv` | Region-token → US state/territory (2-letter), state name, census region. |
| `country_variants.csv` | Region-token → ISO 3166-1 alpha-3 country, country name, region; Canadian provinces and Australian states are preserved as `admin_region`. |
| `school_mappings.csv` | Geography-aware reviewed school mappings (`mapping_id, school_type, raw_school_key, canonical_school, city, state, country, confidence, source, reviewed_on, note`). Empty until reviewed. |
| `source_exclusions.csv` | Reviewed team-season exclusions (e.g. flagged stale-roster copies). The only path that removes rows from publication for staleness. |
| `coverage_exceptions.csv` | Reviewed approval for teams that publish nothing from a season with raw rows. |

## Clean schema (data/clean/vb_rosters_<season>.csv)

### Identity and provenance

| Column | Type | Notes |
|---|---|---|
| `schema_version` | chr | `2.0` |
| `snapshot_id` | chr | e.g. `2015-16_1`; stable row/correction key prefix |
| `source_file` | chr | raw path of the snapshot |
| `source_row_number` | int | row number inside the raw CSV (header = 1) |
| `source_row_id` | chr | `<snapshot_id>#<row>`; **the row and correction key** — name/team/jersey are not stable enough |
| `season_raw` | chr | value as scraped (`2015`, or `2026-27` from 2026 on) |
| `season` | chr | canonical academic-year label (`YYYY-YY`) |
| `ncaa_id` | chr | NCAA team ID |
| `team` | chr | team name as scraped |
| `team_name_status` | chr | `matched` / `matched_id_name_differs` / `unmatched` — joins are by NCAA ID and season, never by team name alone |
| `player_id` | chr | the site's player ID, blank when absent |

### Raw roster values

`name_raw, jersey_raw, year_raw, hometown_raw, high_school_raw, previous_school_raw, height_raw, position_raw, url_raw` — exact source strings (after reviewed cell corrections and the blanking of source junk values such as `NA`). They are never overridden by derived values.

### Basic fields

| Column | Type | Notes |
|---|---|---|
| `name` | chr | display name; diacritics preserved |
| `name_key` | chr | ASCII-folded lowercase join key — matching only, never published display |
| `jersey` | chr | string; `00` kept; composite values such as `14/23` retained, not cast |
| `jersey_status` | chr | `parsed` / `composite` / `unparsed` / `missing` |
| `profile_url` | chr | as scraped (trimmed, junk values blanked) |
| `url_status` | chr | `absolute` / `relative` / `invalid` / `missing` |

### Height

| Column | Type | Notes |
|---|---|---|
| `height_clean` | chr | genuine `6-1` form |
| `height_ft`, `height_in` | int | inches 0–11 required, else unparsed |
| `total_inches` | int | retained only when inside the 55–84 plausibility screen |
| `height_status` | chr | `parsed` / `implausible` / `unparsed` / `missing` |
| `height_reason` | chr | explains every nonempty raw value that produced no normalized value |

### Position

Vocabulary: `OH, MB, S, L, DS, OPP, RS, PH, UT` (slash-combined for multi-position).

| Column | Type | Notes |
|---|---|---|
| `position_clean` | chr | e.g. `OH/DS`; more than two tokens are kept auditable, never silently truncated |
| `primary_position`, `secondary_position` | chr | split of `position_clean` (secondary holds tokens 2..n joined) |
| `position_status` | chr | `parsed` / `unparsed` / `missing` |
| `position_reason` | chr | e.g. “fallback mapping - add the variant to data/reference/positions.csv” |

### Class

| Column | Type | Notes |
|---|---|---|
| `year_clean` | chr | `Freshman` … `Senior`, `Graduate Student`, `Fifth Year`, `Sixth Year` |
| `redshirt` | chr | `TRUE` only for an explicit redshirt marker; `FALSE` for a recognized non-redshirt value; `""` when unknown |
| `year_status` | chr | `parsed` / `unparsed` / `missing` |
| `year_reason` | chr | grad-year inference and lookup vs fallback are recorded here |

Reported class is **not** reliable remaining eligibility.

### Schools

| Column | Type | Notes |
|---|---|---|
| `high_school_clean` | chr | cleaned display value (HS-like values first reclassified from `previous_school_raw`; slash/paren school parts split out of hometown) |
| `high_school_status` | chr | `missing` / `mapped` / `unmapped` / `backfilled` |
| `high_school_reason` | chr | why, incl. mapping reference |
| `high_school_mapping_id`, `high_school_confidence` | chr | reviewed mapping from `school_mappings.csv`, blank when unmapped |
| `high_school_backfill` | chr | `TRUE` when the value came from a prior season |
| `high_school_backfill_season` | chr | source season of the backfill |
| `previous_school_clean` | chr | cleaned display value |
| `previous_school_type` | chr | `High School` / `Junior College` / `College` / `Unknown` |
| `previous_school_ncaa_id` | chr | NCAA ID when the value exactly matches a canonical institution name |
| `previous_school_status` | chr | `missing` / `matched` / `unparsed` — fuzzy matches are review-only |

### Geography

| Column | Type | Notes |
|---|---|---|
| `hometown_clean` | chr | lightly cleaned display value |
| `state` | chr | 2-letter US state/territory code when recognized; blank otherwise |
| `state_name` | chr | full state/territory name |
| `region` | chr | US census region for states; Canadian province / Australian state (province preserved as region); continent-style region for other countries |
| `country` | chr | ISO 3166-1 alpha-3; `USA` for US states and territories |
| `hometown_status` | chr | `parsed` / `unparsed` / `missing` |
| `hometown_reason` | chr | the literal value that failed lookup |

No arbitrary final hometown token is ever promoted to a country; only
`state_variants.csv` / `country_variants.csv` values resolve.

### Team metadata (affiliation, effective-dated)

| Column | Type | Notes |
|---|---|---|
| `team_state` | chr | state in the canonical registry (the literal `NA` for non-US teams such as Simon Fraser) |
| `division` | chr | `I` / `II` / `III` / `NAIA` / `NA` (unknown) when the season is inside the affiliation range |
| `conference` | chr | intentionally blank while label effective-seasons are unknown |

## Legacy crosswalk (old → new)

| Legacy column (notebook output / `vb_rosters_2026-27.csv`) | New column |
|---|---|
| `season` | `season` (canonical label; raw label in `season_raw`) |
| (none) | `snapshot_id`, `source_file`, `source_row_number`, `source_row_id`, `source_sha256` |
| `team` | `team` + `team_name_status` + joins by `ncaa_id` |
| `jersey` | `jersey` (+ `jersey_status`) |
| `name` | `name`, `name_raw`, `name_key` |
| `position` / `position_full` | `position_raw`, `position_clean`, `primary_position`, `secondary_position` |
| `height_clean`/`height` | `height_raw`, `height_clean`, `height_ft`, `height_in`, `total_inches`, `height_status`, `height_reason` |
| `year` | `year_raw`; cleaned class in `year_clean`, `year_status`, `year_reason`, `redshirt` |
| `hometown` / `hometown_clean` | `hometown_raw`, `hometown_clean`, `state`, `state_name`, `region`, `country`, `hometown_status`, `hometown_reason` |
| `homestate` | `state` / `region` (see Geography) |
| `high_school` / `hs_clean` | `high_school_raw`, `high_school_clean`, `high_school_status`, … |
| `previous_school` / `previous_school_clean` | `previous_school_raw`, `previous_school_clean`, `previous_school_type`, `previous_school_ncaa_id`, `previous_school_status` |
| `url` | `url_raw`, `profile_url`, `url_status` |
| `team_state` / `conference` / `division` | same names, now from `team_affiliations.csv` by season range |

Missing-value semantics: `""` (empty string) means not reported/not derived;
there are no `NA` literals in outputs. Status pairs exist for every
normalized field group: a nonempty raw value that produced no normalized
value always has an explicit status (`unparsed`/`implausible`/`unmapped`).

## Corrections (corrections/)

- `cell_corrections.csv`: `source_row_id, field, expected_value, new_value,
  reason, evidence, reviewed_on` — targets raw fields only (provenance is not
  editable). Stale expected values are reported and skipped; unknown rows and
  ineligible fields fail the build.
- `row_corrections.csv`: `source_row_id, action, keeper_source_row_id,
  evidence, reason, reviewed_on` with actions `quarantine`, `restore`,
  `drop_duplicate`, `keep_distinct`.

### Provenance of the committed corrections (2026-11-11)

The 2,863 committed corrections were generated by
`scripts/prepare_review_corrections.py` from the raw snapshots (they are also
regenerable there), every row carrying its `reason` and `evidence`, and each
was cross-checked so that `expected_value` equals the cell actually in the
raw CSV. The patterns:

- **A / C2 / C3 / C1 — team-season template shifts**: a handful of sites wrote
  class/height/position (or hometown/position/height) one or two columns
  wide of the schema; the corrections restore each value to its true field
  per team-season with the detected signature (>= 40-50% of the team's rows
  share it). Never guessed per-row; a team-season either matches the pattern
  or is left quarantined.
- **C — numeric-only names recovered from profile-URL slugs**: roster pages
  that listed players by number only. The name comes from the URL slug
  (`lastname_firstname`), quoted in evidence.
- **B — jersey column holding a position code**: jersey corrected to blank
  where the page showed only the number-adjacent position.
- **Duplicate dispositions** (`row_corrections.csv`): same-name same/singleton
  jersey within a team-season across two profile pages is treated as one
  player's page revision (`drop_duplicate`, keeping the fuller row); groups
  with differing-but-both-present jerseys are kept as distinct entries
  (`keep_distinct`) pending identity adjudication, and bare site-root URL
  coincidences keep both rows.

## Audits (data/derived/audits/<season>/)

`row_issues.csv`, `duplicate_review.csv`, `team_coverage.csv`,
`quality_summary.csv`, `reconciliation.csv`. The notebook
(`analysis/cleaning.qmd`) reads and renders these; it does not re-implement
transformations.

## Season/schema crosswalk for legacy files

| File | Status |
|---|---|
| `vb_rosters_2024.csv` (root) | superseded one-off raw capture, old 7-column schema; not an input to the build |
| `vb_rosters_2026-27.csv` (root) | superseded notebook output for 2026-27; superseded by `data/clean/vb_rosters_2026-27.csv` |
| `data/finished_rosters.csv` | one-off geocoded 2023-24 vintage (20+ columns); not an input |
| `data/missing_teams.csv` | legacy one-line list of team IDs; superseded by audit outputs |
| `data/raw_positions.csv` / `data/cleaned_positions.csv` | byte-identical historical lookups; superseded by `data/reference/positions.csv` |
| `data/years_cleaned.csv` | historical class lookup; superseded by `data/reference/classes.csv` |
