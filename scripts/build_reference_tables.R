# Build the reference tables from the repo's existing lookup/config files.
#
# Outputs (data/reference/):
#   teams_canonical.csv   - one row per NCAA ID (name, state, division)
#   team_affiliations.csv - effective-dated affiliation ranges
#   positions.csv         - raw position -> canonical code
#   classes.csv           - raw class year -> canonical class + redshirt flag
#   raw_snapshots.csv     - registered raw season snapshots + hashes
#
# State/country variant tables (state_variants.csv, country_variants.csv) are
# curated lists maintained directly under data/reference/, not generated.
#
# The generator is idempotent and deterministic: rerunning replaces the
# generated files byte-for-byte. It never touches the raw roster CSVs.

suppressPackageStartupMessages({
  library(tidyverse)
})

options(digits = 10)

## ----------------------------------------------------------------------------
## Hash helper (record source bytes; validated at build time)
## ----------------------------------------------------------------------------

sha256_file <- function(path) {
  # Record source bytes without pulling digest into the build dependency set.
  # system2 passes arguments directly (no shell), so no quoting needed.
  out <- suppressWarnings(system2("shasum", c("-a", "256", path), stdout = TRUE, stderr = "ignore"))
  if (length(out) >= 1 && grepl("^[0-9a-f]{64}", out[1])) return(str_extract(out[1], "^[0-9a-f]{64}"))
  out <- suppressWarnings(system2("openssl", c("dgst", "-sha256", path), stdout = TRUE, stderr = "ignore"))
  if (length(out) >= 1) {
    h <- str_extract(paste(out, collapse = " "), "([0-9a-f]{64})")
    if (!is.na(h)) return(h)
  }
  stop("Cannot compute sha256 for ", path, " - need the shasum or openssl CLI on PATH.")
}

if (!requireNamespace("digest", quietly = TRUE)) {
  probe <- system2("shasum", c("-a", "256", "README.md"), stdout = TRUE, stderr = "ignore")
  if ((length(probe) == 0) || is.null(probe)) {
    stop("Need the digest package or the shasum CLI to record source hashes.")
  }
}

## ----------------------------------------------------------------------------
## Load legacy lookup sources
## ----------------------------------------------------------------------------

# teams.csv is scraper config (34+ seasons of config rows, mostly not
# volleyball-relevant). vb_teams.csv is the volleyball team list with
# state/division/conference. teams.json mirrors teams.csv.
teams_config <- read_csv("data/teams.csv", show_col_types = FALSE) |>
  select(-any_of(c("url", "twitter", "stats_name", "private", "roster"))) |>
  distinct(ncaa_id, .keep_all = TRUE)

# na = "" keeps the literal strings "NA"/"N/A" intact; "unknown" sentinels are
# cleaned in R, not silently nullified by the reader.
vb_teams <- read_csv("data/vb_teams.csv", show_col_types = FALSE, na = "") %>%
  # Repair the malformed Florida State row: its state and division collapsed
  # into the conference/last columns (Florida State,234,ACC,I -> state='ACC',
  # conference='I', division missing). 'NA' division rows (Concordia Portland,
  # MacMurray) keep NA as a legitimate unknown; Simon Fraser's NA state is
  # non-US and stays.
  mutate(
    team_state = case_when(
      team == "Florida State" & ncaa_id == 234 ~ "FL",
      TRUE ~ as.character(team_state)
    ),
    conference = case_when(
      team == "Florida State" & ncaa_id == 234 ~ "ACC",
      TRUE ~ as.character(conference)
    ),
    division = case_when(
      team == "Florida State" & ncaa_id == 234 ~ "I",
      division == "NA" ~ "NA",
      TRUE ~ as.character(division)
    )
  )

stopifnot(
  "vb_teams.csv ncaa_id must be unique" = !anyDuplicated(vb_teams$ncaa_id),
  "repaired Florida State row" = {
    fsu <- vb_teams %>% filter(team == "Florida State")
    nrow(fsu) == 1 && fsu$team_state == "FL" && fsu$conference == "ACC" && fsu$division == "I"
  }
)

## ----------------------------------------------------------------------------
## teams_canonical.csv - one row per NCAA ID
## ----------------------------------------------------------------------------

# disagreements among teams.csv / teams.json / vb_teams.csv
team_json <- jsonlite::fromJSON("data/teams.json") %>%
  tibble() %>%
  select(-any_of(c("url", "twitter", "stats_name", "private", "roster", "scraper",
                   "skip_seasons", "hidden_rows"))) %>%
  distinct(ncaa_id, .keep_all = TRUE)

name_diffs <- vb_teams %>%
  left_join(teams_config %>% select(ncaa_id, team_config = team), by = "ncaa_id") %>%
  left_join(team_json %>% select(ncaa_id, team_json_name = team), by = "ncaa_id") %>%
  mutate(
    mismatch = (team != team_config) | (!is.na(team_json_name) & team != team_json_name),
    # disagreement audit is rowwise (pmap over each team's registry names)
    conflict_names = pmap_chr(
      list(mismatch, team, team_config, team_json_name),
      function(mis, t, cfg, jn) {
        if (is.na(mis) || !mis) return(NA_character_)
        paste(unique(na.omit(c(t, cfg, jn))), collapse = " | ")
      }
    )
  )

teams_canonical <- vb_teams %>%
  left_join(teams_config %>% select(ncaa_id, team_config = team), by = "ncaa_id") %>%
  left_join(name_diffs %>% select(ncaa_id, is_mismatch = mismatch, conflict_names), by = "ncaa_id") %>%
  transmute(
    ncaa_id,
    team_name = coalesce(team_config, team),
    team_state,
    division,
    metadata_source = coalesce(ifelse(is.na(team_config), NA_character_, "data/teams.csv"),
                               "data/vb_teams.csv"),
    conflict_names,
    row_note = ifelse(isTRUE(is_mismatch),
                      "name disagreement between registries - canonical name chosen from data/teams.csv",
                      NA_character_)
  ) %>%
  arrange(ncaa_id)

stopifnot(
  "team_name must be nonblank" = all(teams_canonical$team_name != ""),
  "team_state must be nonblank" = all(teams_canonical$team_state != "" & !is.na(teams_canonical$team_state)),
  "division controlled vocab" = all(teams_canonical$division %in% c("I", "II", "III", "NAIA", "NA"))
)

## ----------------------------------------------------------------------------
## team_affiliations.csv - effective-dated affiliations
## ----------------------------------------------------------------------------

# Current vb_teams.csv conference labels have an unknown effective season, so
# they are NOT copied backward onto 2015-27. Historical affiliations are
# published missing-conference and reported; adding them takes a reviewed
# season-range override (the basketball repo's pattern).
SEASONS_ACTIVE <- sprintf("%d-%02d", 2015:2026, 16:27)

team_affiliations <- teams_canonical %>%
  transmute(
    ncaa_id,
    first_season = "2015-16",
    last_season  = "2026-27",
    division,
    conference = NA_character_,
    source = "data/vb_teams.csv current labels; effective season unknown",
    note = paste0(
      "division carried from current registry; conference intentionally blank ",
      "until reviewed season ranges exist"
    )
  )

## ----------------------------------------------------------------------------
## positions.csv / classes.csv - the unique, validated lookups
## ----------------------------------------------------------------------------

# data/raw_positions.csv and data/cleaned_positions.csv were byte-identical
# copies of each other; positions.csv is now the single active source.
VB_CLASS_VOCAB <- c(
  "Freshman", "Sophomore", "Junior", "Senior", "Graduate Student",
  "Fifth Year", "Sixth Year"
)

POS_VOCAB <- c("OH", "MB", "S", "L", "DS", "OPP", "RS", "PH", "UT", "NONE", "UNKNOWN")

# Token aliases found inside the legacy lookup itself.
POS_TOKEN_FIX <- c(OP = "OPP", MH = "MB", PIN = "PH")

# A cleaned code is valid when every slash-separated token maps into the vocab
# (combined codes such as "OH/DS" or "S/RS/OH" stand as recorded).
pos_code_valid <- function(x) {
  if (is.na(x) || x == "") return(FALSE)
  tokens <- str_split_1(str_squish(str_to_upper(x)), "/")
  tokens <- coalesce(POS_TOKEN_FIX[tokens], tokens)
  all(tokens %in% POS_VOCAB)
}

# Key matches are case-insensitive at build time, so collapse case-only
# duplicates here; conflicting codes for one key are an error.
positions <- read_csv("data/cleaned_positions.csv", show_col_types = FALSE) %>%
  transmute(position_raw = position, position_code = cleaned_position) %>%
  mutate(
    key = str_to_upper(str_squish(position_raw)),
    position_code = map_chr(position_code, function(x) {
      if (is.na(x) || x == "") return(NA_character_)
      tokens <- str_split_1(str_squish(str_to_upper(x)), "/")
      tokens <- coalesce(POS_TOKEN_FIX[tokens], tokens)
      if (all(tokens %in% POS_VOCAB)) paste(tokens, collapse = "/") else NA_character_
    })
  ) %>%
  filter(!is.na(position_code)) %>%
  distinct(key, position_code)

stopifnot("conflicting position codes for one raw key" = !anyDuplicated(positions$key))

positions <- positions |> transmute(position_raw = key, position_code) |> arrange(position_raw)

# Guard against conflicting cleanings of one raw key after manual edits.
pos_conflicts <- positions %>%
  group_by(position_raw) %>%
  filter(n() > 1) %>%
  ungroup()
stopifnot("positions.csv has conflicting raw keys" = nrow(pos_conflicts) == 0)

# classes.csv: years_cleaned.csv carries count and redshirt columns.
# Keys are collapsed case-insensitively (build-time validation demands a
# single mapping per folded key).
classes <- read_csv("data/years_cleaned.csv", show_col_types = FALSE, na = "") %>%
  rename(year_clean_canon = `year-clean`) %>%
  mutate(
    key = str_to_upper(str_squish(year)),
    year_clean_kept = case_when(
      year_clean_canon %in% VB_CLASS_VOCAB ~ year_clean_canon,
      str_squish(year_clean_canon) == "" ~ NA_character_,
      # unrecognized clean values (count 0/typos) are dropped and fall to the
      # pattern fallback at build time
      TRUE ~ NA_character_
    ),
    redshirt_flag = redshirt == "1" | redshirt == 1
  ) %>%
  transmute(key, year_clean_kept, redshirt_flag) %>%
  distinct() %>%
  group_by(key) %>%
  summarise(
    year_clean = {
      nonblank <- year_clean_kept[!is.na(year_clean_kept)]
      if (length(nonblank) == 1) nonblank else if (length(nonblank) == 0) NA_character_ else
        stop("conflicting class mappings for key ", key[1])
    },
    redshirt = any(redshirt_flag),
    .groups = "drop"
  ) %>%
  # Keep redshirt-marked keys even when the class year itself is unknown
  # ("Rs." = redshirt with unresolved class).
  filter(!is.na(year_clean) | redshirt) %>%
  transmute(year_raw = key, year_clean, redshirt) %>%
  arrange(year_raw)

## ----------------------------------------------------------------------------
## raw_snapshots.csv - registered raw season snapshots
## ----------------------------------------------------------------------------

SEASON_STARTS <- 2015:2026
raw_files <- tibble(
  start = SEASON_STARTS,
  raw_path = file.path("data", paste0("rosters_", SEASON_STARTS, ".csv")),
  canonical = sprintf("%d-%02d", SEASON_STARTS, (SEASON_STARTS + 1) %% 100)
) %>% filter(file.exists(raw_path))

stopifnot("all 12 raw season files must exist" = nrow(raw_files) == 12)

raw_snapshots <- raw_files %>%
  transmute(
    season = canonical,
    snapshot_id = paste0(canonical, "_1"),
    raw_path,
    # The 2015-2025 files carry the bare year; the 2026 file already stores
    # the full academic-year label.
    raw_season_value = ifelse(start == 2026, "2026-27", as.character(start)),
    sha256 = vapply(raw_path, sha256_file, character(1)),
    retrieved_at = format(as.POSIXct(file.info(raw_path)$mtime, tz = "UTC"), "%Y-%m-%dT%H:%M:%SZ"),
    active = TRUE,
    notes = "original scraper vintage"
  ) %>%
  arrange(season)

dir.create("data/reference", showWarnings = FALSE, recursive = TRUE)

write_csv(teams_canonical, "data/reference/teams_canonical.csv", na = "")
write_csv(team_affiliations, "data/reference/team_affiliations.csv", na = "")
write_csv(positions, "data/reference/positions.csv", na = "")
write_csv(classes, "data/reference/classes.csv", na = "")
write_csv(raw_snapshots, "data/reference/raw_snapshots.csv", na = "")

cat("Reference tables written:\n")
cat("  teams_canonical.csv:  ", nrow(teams_canonical), "teams\n")
cat("  team_affiliations.csv:", nrow(team_affiliations), "rows\n")
cat("  positions.csv:        ", nrow(positions), "raw keys\n")
cat("  classes.csv:          ", nrow(classes), "raw keys\n")
cat("  raw_snapshots.csv:    ", nrow(raw_snapshots), "snapshots\n")
