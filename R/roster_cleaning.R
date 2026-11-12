# Women's volleyball roster cleaning: reusable transformations and validations.
#
# This module is the single implementation of the cleaning pipeline. The
# notebook (analysis/cleaning.qmd) renders audits from build outputs; it does
# not re-implement transformations. scripts/build_rosters.R drives it.
#
# Build order per season:
#   active raw snapshot -> structural baseline -> normalization -> corrections
#   -> validated clean season
# Cell/row corrections (corrections/*.csv) apply to raw values / row
# dispositions before normalization, so every published value stays
# reproducible from raw + corrections + reference tables. Repeated builds are
# byte-identical: no generated timestamps land inside data files.

suppressPackageStartupMessages({
  library(tidyverse)
})

VB_RAW_SCHEMA <- c(
  "ncaa_id", "team", "player_id", "name", "year", "hometown",
  "high_school", "previous_school", "height", "position", "jersey", "url",
  "season"
)

# The 13 snapshot source fields after the reader's *_raw rename (used for the
# exact-duplicate key).
VB_KEY_FIELDS <- c(
  "ncaa_id", "team", "player_id", "name_raw", "year_raw", "hometown_raw",
  "high_school_raw", "previous_school_raw", "height_raw", "position_raw",
  "jersey_raw", "url_raw", "season_raw"
)

# Fields cell corrections may edit. Provenance fields are never editable.
VB_EDITABLE_FIELDS <- c(
  "ncaa_id", "team", "player_id", "name", "year", "hometown",
  "high_school", "previous_school", "height", "position", "jersey", "url"
)

RAW_TO_CLEAN_FIELD <- c(
  ncaa_id = "ncaa_id", team = "team", player_id = "player_id",
  name = "name_raw", year = "year_raw", hometown = "hometown_raw",
  high_school = "high_school_raw", previous_school = "previous_school_raw",
  height = "height_raw", position = "position_raw", jersey = "jersey_raw",
  url = "url_raw"
)

VB_POSITION_VOCAB <- c("OH", "MB", "S", "L", "DS", "OPP", "RS", "PH", "UT")
VB_CLASS_VOCAB <- c(
  "Freshman", "Sophomore", "Junior", "Senior", "Graduate Student",
  "Fifth Year", "Sixth Year"
)
VB_SCHEMA_VERSION <- "2.0"

# Source strings that mean "no value", not data.
VB_JUNK_STRINGS <- c("NA", "N.A.", "N/A", "n/a", "none", "None", "NONE",
                     "null", "NULL", "-", "--", "?", "TBA", "TBD")

VB_US_TERRITORY_CODES <- c("PR", "GU", "VI", "AS", "MP")

.vb_env <- new.env(parent = emptyenv())

vb_env_reset <- function() {
  if (exists("teams_registry", envir = .vb_env, inherits = FALSE)) {
    rm("teams_registry", envir = .vb_env)
  }
  invisible(NULL)
}

# Published clean schema, in column order. The complete data dictionary lives
# in docs/data_dictionary.md.
VB_CLEAN_SCHEMA <- c(
  # identity/provenance
  "schema_version", "snapshot_id", "source_file", "source_row_number",
  "source_row_id", "season_raw", "season", "ncaa_id", "team",
  "team_name_status", "player_id",
  # raw roster values
  "name_raw", "jersey_raw", "year_raw", "hometown_raw", "high_school_raw",
  "previous_school_raw", "height_raw", "position_raw", "url_raw",
  # basic fields
  "name", "name_key", "jersey", "jersey_status", "profile_url", "url_status",
  # height
  "height_clean", "height_ft", "height_in", "total_inches",
  "height_status", "height_reason",
  # position
  "position_clean", "primary_position", "secondary_position",
  "position_status", "position_reason",
  # class
  "year_clean", "redshirt", "year_status", "year_reason",
  # schools
  "high_school_clean", "high_school_status", "high_school_reason",
  "high_school_mapping_id", "high_school_confidence",
  "high_school_backfill", "high_school_backfill_season",
  "previous_school_clean", "previous_school_type", "previous_school_ncaa_id",
  "previous_school_status",
  # geography
  "hometown_clean", "state", "state_name", "region", "country",
  "hometown_status", "hometown_reason",
  # team metadata (affiliation, effective-dated)
  "team_state", "division", "conference"
)

# Structural baseline schema (raw + provenance + corrections + classification).
VB_BASELINE_SCHEMA <- c(
  "schema_version", "snapshot_id", "source_file", "source_row_number",
  "source_row_id", "season_raw", "season", "ncaa_id", "team", "player_id",
  "name_raw", "jersey_raw", "year_raw", "hometown_raw", "high_school_raw",
  "previous_school_raw", "height_raw", "position_raw", "url_raw",
  "corrections_applied", "classification", "classification_reason",
  "team_name_status"
)

# Quarantine schema: every raw value preserved plus the reason.
VB_QUARANTINE_SCHEMA <- c(
  "snapshot_id", "source_file", "source_row_number", "source_row_id",
  "season", "ncaa_id", "team", "player_id", "name_raw", "jersey_raw",
  "year_raw", "hometown_raw", "high_school_raw", "previous_school_raw",
  "height_raw", "position_raw", "url_raw", "quarantine_reason"
)


## ---------------------------------------------------------------------------
## Small helpers
## ---------------------------------------------------------------------------

vb_text_display <- function(x) {
  as.character(x) |> str_replace_all("\\s+", " ") |> str_squish()
}

vb_is_blank <- function(x) {
  is.na(x) | (vb_text_display(x) == "")
}

# Returns the display value, or NA when the source value is junk.
vb_as_missing <- function(x) {
  v <- vb_text_display(x)
  if_else(is.na(v) | v == "" | v %in% VB_JUNK_STRINGS, NA_character_, v)
}

# ASCII-folded match key. Display values keep diacritics; only keys fold.
vb_fold_ascii <- function(x) {
  if (!requireNamespace("stringi", quietly = TRUE)) {
    key <- as.character(x) |>
      str_replace_all(c(
        "[\u00C0-\u00C5]" = "a", "[\u00C8-\u00CB]" = "e", "[\u00CC-\u00CF]" = "i",
        "[\u00D2-\u00D6]" = "o", "[\u00D9-\u00DC]" = "u", "\u00D1" = "n",
        "\u00C7" = "c", "\u00D8" = "o", "\u00C6" = "ae", "\u0152" = "oe"
      ))
  } else {
    key <- stringi::stri_trans_general(as.character(x), "latin-ascii")
  }
  key |> str_to_lower() |> str_replace_all("[^a-z0-9]", "")
}

vb_name_key <- function(x) vb_fold_ascii(x)
vb_school_key <- function(x) vb_fold_ascii(x)

vb_url_key <- function(x) {
  x |>
    vb_as_missing() |>
    str_to_lower() |>
    str_replace("^https?://", "") |>
    str_replace("^//", "") |>
    str_replace("^www\\.", "") |>
    str_replace("/+$", "")
}

vb_season_label <- function(start_year) {
  sprintf("%d-%02d", as.integer(start_year), (as.integer(start_year) + 1) %% 100)
}


## ---------------------------------------------------------------------------
## Reference tables and corrections
## ---------------------------------------------------------------------------

vb_read_reference_csv <- function(path) {
  read_csv(path, show_col_types = FALSE, na = "")
}

vb_load_reference_tables <- function(reference_dir = "data/reference") {
  required <- c(
    "raw_snapshots.csv", "teams_canonical.csv", "team_affiliations.csv",
    "positions.csv", "classes.csv", "state_variants.csv",
    "country_variants.csv", "school_mappings.csv", "source_exclusions.csv",
    "coverage_exceptions.csv"
  )
  missing <- required[!file.exists(file.path(reference_dir, required))]
  if (length(missing) > 0) {
    stop("required reference table(s) missing: ", paste(missing, collapse = ", "))
  }
  tables <- map(file.path(reference_dir, required), vb_read_reference_csv) |>
    set_names(str_remove(required, "\\.csv$"))

  if (anyDuplicated(str_to_upper(str_squish(tables$positions$position_raw)))) {
    stop("positions.csv has duplicate raw keys")
  }
  if (anyDuplicated(str_to_upper(str_squish(as.character(tables$classes$year_raw))))) {
    stop("classes.csv has duplicate raw keys")
  }
  if (anyDuplicated(str_to_upper(str_squish(tables$state_variants$raw_key)))) {
    stop("state_variants.csv has duplicate raw keys")
  }
  if (anyDuplicated(str_to_upper(str_squish(tables$country_variants$raw_key)))) {
    stop("country_variants.csv has duplicate raw keys")
  }
  bad_state <- setdiff(unique(tables$state_variants$state_code),
                       c(state.abb, "DC", VB_US_TERRITORY_CODES, "NA"))
  if (length(bad_state) > 0) {
    stop("state_variants.csv has non-controlled state codes: ",
         paste(bad_state, collapse = ", "))
  }
  if (anyDuplicated(tables$school_mappings$mapping_id)) {
    stop("school_mappings.csv mapping_id values must be unique")
  }
  tables
}

vb_load_corrections <- function(corrections_dir = "corrections") {
  cell_path <- file.path(corrections_dir, "cell_corrections.csv")
  row_path <- file.path(corrections_dir, "row_corrections.csv")
  cell <- if (file.exists(cell_path)) vb_read_reference_csv(cell_path) else tibble()
  row <- if (file.exists(row_path)) vb_read_reference_csv(row_path) else tibble()

  if (nrow(cell) > 0) {
    if (anyDuplicated(paste(cell$source_row_id, cell$field))) {
      stop("cell_corrections.csv: duplicate source_row_id + field entries")
    }
    bad <- cell |> filter(!field %in% VB_EDITABLE_FIELDS)
    if (nrow(bad) > 0) {
      stop("cell_corrections.csv: field(s) not editable: ",
           paste(unique(bad$field), collapse = ", "))
    }
    if (any(str_squish(as.character(cell$reason)) == "")) {
      stop("cell_corrections.csv: every correction needs a reason")
    }
  }
  if (nrow(row) > 0) {
    if (anyDuplicated(paste(row$source_row_id, row$action))) {
      stop("row_corrections.csv: duplicate source_row_id + action entries")
    }
    allowed <- c("quarantine", "restore", "drop_duplicate", "keep_distinct")
    bad <- row |> filter(!action %in% allowed)
    if (nrow(bad) > 0) {
      stop("row_corrections.csv: unknown action(s): ",
           paste(unique(bad$action), collapse = ", "))
    }
    if (any(str_squish(as.character(row$reason)) == "")) {
      stop("row_corrections.csv: every row correction needs a reason")
    }
    need_keeper <- row |> filter(action == "drop_duplicate")
    if (nrow(need_keeper) > 0 && any(is.na(need_keeper$keeper_source_row_id))) {
      stop("row_corrections.csv: drop_duplicate entries need keeper_source_row_id")
    }
  }
  list(cell = cell, row = row)
}

# Canonical team registry used for id joins, name checks and previous-school
# institutional matching (cached because every season's build joins by it).
vb_teams_registry <- function(reference_dir = "data/reference") {
  if (exists("teams_registry", envir = .vb_env, inherits = FALSE)) {
    return(.vb_env$teams_registry)
  }
  if (exists("teams_from_refs", envir = .vb_env, inherits = FALSE)) {
    return(.vb_env$teams_from_refs)
  }
  teams <- vb_read_reference_csv(file.path(reference_dir, "teams_canonical.csv")) |>
    transmute(
      ncaa_id = as.character(ncaa_id),
      team_name, team_state,
      key = str_to_upper(vb_fold_ascii(team_name))
    ) |>
    distinct(key, .keep_all = TRUE)
  assign("teams_registry", teams, envir = .vb_env)
  teams
}

# Set the registry from loaded reference tables (used by the season build so
# fixtures and tests can supply their own teams_canonical).
vb_teams_registry_set <- function(teams_canonical) {
  teams <- teams_canonical |>
    transmute(
      ncaa_id = as.character(ncaa_id),
      team_name = as.character(team_name),
      team_state = as.character(team_state),
      key = str_to_upper(vb_fold_ascii(team_name))
    ) |>
    distinct(key, .keep_all = TRUE)
  assign("teams_from_refs", teams, envir = .vb_env)
  teams
}



## ---------------------------------------------------------------------------
## Snapshots: registration, hash validation, provenance
## ---------------------------------------------------------------------------

vb_active_snapshot <- function(snapshots, season) {
  snap <- snapshots |> filter(season == !!season, active == TRUE)
  if (nrow(snap) == 0) {
    stop("no active raw snapshot registered for season ", season,
         " (data/reference/raw_snapshots.csv)")
  }
  if (nrow(snap) > 1) {
    stop("more than one active snapshot for season ", season, ": ",
         paste(snap$snapshot_id, collapse = ", "))
  }
  as_tibble(snap)
}

vb_read_raw_snapshot <- function(snapshot_row) {
  if (!file.exists(snapshot_row$raw_path)) {
    stop("raw snapshot file missing: ", snapshot_row$raw_path)
  }
  actual_sha <- suppressWarnings(
    str_extract(system2("shasum", c("-a", "256", snapshot_row$raw_path),
                        stdout = TRUE)[1], "^[0-9a-f]{64}")
  )
  if (is.na(actual_sha) || actual_sha != snapshot_row$sha256) {
    stop("snapshot hash mismatch for ", snapshot_row$raw_path,
         " - the raw source changed since registration (expected ",
         snapshot_row$sha256, ", found ", actual_sha,
         "). Re-register with scripts/register_snapshot.py after review.")
  }
  raw <- read_csv(
    snapshot_row$raw_path,
    show_col_types = FALSE,
    col_types = cols(.default = "c"),
    # Keep literal "NA"/"N/A" source strings; vb_as_missing() handles junk.
    na = character(0)
  )
  if (!identical(sort(names(raw)), sort(VB_RAW_SCHEMA))) {
    stop("raw snapshot ", snapshot_row$raw_path, " schema mismatch: expected [",
         paste(VB_RAW_SCHEMA, collapse = ", "), "] got [",
         paste(names(raw), collapse = ", "), "]")
  }
  # Source roster cells immediately become *_raw fields; identity columns
  # (ncaa_id, team, player_id) and the season contract keep their names.
  raw <- raw |>
    rename(
      name_raw = name, year_raw = year, hometown_raw = hometown,
      high_school_raw = high_school, previous_school_raw = previous_school,
      height_raw = height, position_raw = position, jersey_raw = jersey,
      url_raw = url, season_raw = season
    )

  # Season contract: every row must carry a season value that canonicalizes to
  # the snapshot's registered season. Mixed or unregistered values stop the
  # build (structural source error, not a player-level problem).
  raw$season_raw <- coalesce(as.character(raw$season_raw), "")
  # A row's season value may be the bare starting year ("2015") or an already
  # full academic-year label ("2026-27"); both must canonicalize to the
  # registered season.
  canon <- map_chr(str_squish(raw$season_raw), function(x) {
    if (str_detect(x, "^\\d{4}$")) return(vb_season_label(x))
    if (str_detect(x, "^\\d{4}-\\d{2}$")) {
      start <- str_sub(x, 1, 4) |> as.integer()
      if (vb_season_label(start) == x) return(x)
    }
    NA_character_
  })
  if (anyNA(canon)) {
    stop("raw snapshot ", snapshot_row$raw_path, ": unparseable season value(s): ",
         paste(unique(raw$season_raw[is.na(canon)]), collapse = ", "))
  }
  if (length(unique(canon)) != 1 || unique(canon) != snapshot_row$season) {
    stop("raw snapshot ", snapshot_row$raw_path, ": rows carry season value(s) (",
         paste(unique(raw$season_raw[canon != snapshot_row$season]), collapse = ", "),
         ") that do not canonicalize to the registered season ", snapshot_row$season)
  }

  raw |>
    mutate(
      source_row_number = as.integer(row_number()) + 1L,  # file row; header is 1
      snapshot_id = snapshot_row$snapshot_id,
      source_file = snapshot_row$raw_path,
      source_sha256 = snapshot_row$sha256,
      season = snapshot_row$season
    ) |>
    mutate(source_row_id = vb_source_row_id(snapshot_id, source_row_number))
}

vb_source_row_id <- function(snapshot_id, source_row_number) {
  str_c(snapshot_id, "#", sprintf("%05d", as.integer(source_row_number)))
}


## ---------------------------------------------------------------------------
## Cell and row corrections (durable, reviewed)
## ---------------------------------------------------------------------------

vb_apply_cell_corrections <- function(raw, cell_corrections) {
  # Corrections target raw fields by source_row_id with an expected value.
  # Stale expected values (already fixed upstream) are reported and skipped;
  # a correction for an unknown row fails the build.
  raw$corrections_applied <- NA_character_
  raw$corrections_stale_note <- NA_character_
  if (nrow(cell_corrections) == 0) {
    return(raw)
  }
  raw_snapshot_id <- unique(raw$snapshot_id)[1]
  snapshot_prefix <- raw_snapshot_id
  correction <- cell_corrections |>
    transmute(
      source_row_id = as.character(source_row_id),
      field,
      expected_value = ifelse(is.na(as.character(expected_value)), "",
                              as.character(expected_value)),
      new_value = as.character(new_value)
    ) |>
    # corrections for other seasons' snapshots are applied in their own build
    filter(str_detect(source_row_id, paste0("^", snapshot_prefix, "#")))

  unknown <- correction |> filter(!source_row_id %in% raw$source_row_id)
  if (nrow(unknown) > 0) {
    stop("cell_corrections.csv: source_row_id(s) not in this snapshot: ",
         paste(unknown$source_row_id, collapse = ", "))
  }

  row_idx <- match(correction$source_row_id, raw$source_row_id)
  col_idx <- RAW_TO_CLEAN_FIELD[correction$field]
  current <- mapply(function(i, c) as.character(coalesce(raw[[c]][i], "")),
                    row_idx, col_idx)
  applied <- raw$corrections_applied
  stale_note <- character(nrow(raw))

  for (k in seq_along(row_idx)) {
    if (current[k] != correction$expected_value[k]) {
      stale_note[row_idx[k]] <- paste0(
        str_squish(paste(stale_note[row_idx[k]], "|")),
        cor_field_note(correction, k, current[k])
      )
      next
    }
    raw[[col_idx[k]]][row_idx[k]] <- correction$new_value[k]
    applied[row_idx[k]] <- paste0(applied[row_idx[k]], correction$field[k], ";")
  }

  raw |>
    mutate(
      corrections_applied = ifelse(applied == "", NA_character_, applied),
      corrections_stale_note = ifelse(stale_note == "", NA_character_, stale_note)
    )
}

cor_field_note <- function(correction, k, current) {
  paste0(correction$field[k], " correction is stale (file value: ", current,
         "; expected: ", correction$expected_value[k], "; new_value skipped)")
}

# Row dispositions from review, by action.
vb_row_correction_actions <- function(row_corrections, action) {
  row_corrections |>
    filter(.data$action == !!action) |>
    pull(source_row_id) |>
    as.character()
}


## ---------------------------------------------------------------------------
## Structural screens. Failures below the "stop the build" severity quarantine
## the individual row; every raw value is preserved next to the reason.
## Heuristic column permutation is deliberately absent: shifted rows are
## repaired only through reviewed corrections.
## ---------------------------------------------------------------------------

vb_screen_staff_name <- function(x) {
  v <- str_squish(coalesce(as.character(x), ""))
  staff_anywhere <- str_detect(
    v,
    regex("\\b(head |assistant |asst\\.? |associate |volunteer |graduate |student |undergraduate |interim |director of |athletic trainer |video |volunteerasstant )?(coach|coordinator|director|manager|trainer|analyst|operations|strength and conditioning|administrator|advisor|intern)\\b",
          ignore_case = TRUE)
  )
  staff_anywhere
}

vb_screen_numeric_only <- function(x) {
  str_detect(str_squish(coalesce(as.character(x), "")), "^\\d+$")
}

vb_shifted_signature <- function(name_raw, year_raw, height_raw, position_raw, jersey_raw) {
  # Strong shifted-column signatures; quarantine reasons, not repair recipes.
  name_looks_height <- str_detect(coalesce(name_raw, ""),
                                  "^[4567][-–']\\d{1,2}(''|\")?\\s*$")
  height_looks_position <- str_detect(
    coalesce(height_raw, ""),
    regex("^\\s*(OH|MB|DS|S|L|RS|OPP|MH|LIB|SET)(/(OH|MB|DS|S|L|RS|OPP|MH|LIB|SET))*\\s*$",
          ignore_case = TRUE)
  )
  height_looks_class <- str_detect(
    coalesce(height_raw, ""),
    regex("^\\s*(fr\\.?|so\\.?|jr\\.?|sr\\.?|freshman|sophomore|junior|senior|graduate|grad\\.)\\s*$",
          ignore_case = TRUE)
  )
  pos_looks_staff <- str_detect(coalesce(position_raw, ""),
                                regex("coach|director|manager|trainer|operations|volunteer",
                                      ignore_case = TRUE))
  year_looks_position <- str_detect(coalesce(year_raw, ""),
                                    regex("^\\s*(OH|MB|DS|S|L|RS|OPP|MH|LIB|SET)\\s*$",
                                          ignore_case = TRUE)) |
    str_detect(coalesce(year_raw, ""), "^\\s*[A-Z]{1,5}(/[A-Z]{1,5})+\\s*$") |
    str_detect(coalesce(year_raw, ""), regex(
      "^\\s*(out(side|side hitter)|mid(dle)?\\.? ?(blocker|blocker)?|libero|defen(sive|der) ?(specialist)?|setter|utility)\\s*(/.*)?$",
      ignore_case = TRUE))
  name_looks_class <- str_detect(
    coalesce(name_raw, ""),
    regex("^\\s*(fr\\.?|fy\\.?|so\\.?|jr\\.?|sr\\.?|freshman|sophomore|junior|senior|graduate|gr\\.?)\\s*$",
          ignore_case = TRUE)
  )
  jersey_looks_position <- str_detect(coalesce(jersey_raw, ""),
                                      "^\\s*(OH|MB|DS|S|L|RS|OPP|MH|LIB)\\s*$")
  ifelse(
    name_looks_height, "name column carries a height value",
    ifelse(
      height_looks_position, "height column carries a position value",
      ifelse(
        height_looks_class, "height column carries a class value",
        ifelse(
          pos_looks_staff, "position column carries staff/title text",
          ifelse(
            year_looks_position, "class column carries a position code",
            ifelse(
              name_looks_class, "name column carries a class value",
              ifelse(jersey_looks_position, "jersey column carries a position code",
                     NA_character_)
            )
          )
        )
      )
    )
  )
}


## ---------------------------------------------------------------------------
## Normalization: heights
## ---------------------------------------------------------------------------

vb_clean_heights <- function(df) {
  norm <- df$height_raw |>
    as.character() |>
    str_replace_all("(?i)height:\\s*", "") |>
    str_replace_all("''", "") |>
    str_replace_all("[\"\u201D\u2033]", "") |>
    str_remove_all("\\s") |>
    str_replace_all("[\u2018\u2019'\u2032]", "-") |>
    str_remove_all("(?<=\\d)\\.$")

  norm <- str_replace_all(norm, "-{2,}", "-")
  parsed <- str_match(norm, "^([1-8])-(\\d{1,2})$")
  ft <- suppressWarnings(as.integer(parsed[, 2]))
  inch <- suppressWarnings(as.integer(parsed[, 3]))
  bad_inches <- !is.na(inch) & (inch < 0 | inch > 11)
  ft[bad_inches] <- NA_integer_
  inch[bad_inches] <- NA_integer_

  height_clean <- ifelse(!is.na(ft) & !is.na(inch), paste0(ft, "-", inch), NA_character_)
  total_inches <- ifelse(!is.na(ft) & !is.na(inch), ft * 12L + inch, NA_integer_)

  # Plausibility screen carried over from the notebook (reviewable rule):
  # college players fall in 55..84 total inches.
  implausible <- !is.na(total_inches) & !dplyr::between(total_inches, 55, 84)
  height_clean[implausible] <- NA_character_
  ft[implausible] <- NA_integer_
  inch[implausible] <- NA_integer_
  total_inches[implausible] <- NA_integer_

  missing <- vb_is_blank(df$height_raw) |
    (vb_text_display(df$height_raw) %in% VB_JUNK_STRINGS)
  df |>
    mutate(
      height_ft = ft,
      height_in = inch,
      total_inches = total_inches,
      height_clean = height_clean,
      height_status = case_when(
        missing ~ "missing",
        implausible ~ "implausible",
        !is.na(height_clean) ~ "parsed",
        TRUE ~ "unparsed"
      ),
      height_reason = case_when(
        missing ~ "no height reported",
        implausible ~ paste0("parses to ", str_squish(coalesce(as.character(df$height_raw), "")),
                             " but falls outside the 55-84 inch plausibility screen"),
        !is.na(height_clean) ~ NA_character_,
        TRUE ~ paste0("no recognizable height format: ", vb_text_display(df$height_raw))
      )
    )
}


## ---------------------------------------------------------------------------
## Normalization: positions
## ---------------------------------------------------------------------------

VB_POSITION_TOKEN_FIX <- c(OP = "OPP", MH = "MB", PIN = "PH", DH = "OPP", LIB = "L")

vb_position_fallback <- function(x) {
  map_chr(x, function(p) {
    if (vb_is_blank(p)) return(NA_character_)
    p0 <- p |>
      str_to_upper() |>
      str_squish() |>
      str_replace_all(c(
        "OUTSIDE HITTER" = "OH", "MIDDLE BLOCKER" = "MB",
        "DEFENSIVE SPECIALIST" = "DS", "RIGHT-SIDE HITTER" = "RS",
        "RIGHT SIDE HITTER" = "RS", "RIGHT-SIDE" = "RS",
        "RIGHT SIDE" = "RS", "PIN HITTER" = "PH", "PIN-HITTER" = "PH"
      )) |>
      str_squish()
    tokens <- str_split_1(p0, "\\s*/\\s*") |> str_squish()
    tokens <- tokens[tokens != ""]
    fixed <- VB_POSITION_TOKEN_FIX[tokens]
    tokens <- ifelse(is.na(fixed), tokens, fixed)
    if (length(tokens) == 0 || anyNA(tokens) || !all(tokens %in% VB_POSITION_VOCAB)) {
      return(NA_character_)
    }
    paste(tokens, collapse = "/")
  })
}

vb_clean_positions <- function(df, positions_lookup) {
  raw <- df$position_raw |> vb_as_missing()
  key <- str_to_upper(str_squish(coalesce(raw, "")))
  lookup <- positions_lookup |>
    transmute(key = str_to_upper(str_squish(position_raw)), position_code) |>
    distinct(key, .keep_all = TRUE)
  mapped <- tibble(key = key) |>
    left_join(lookup, by = "key", relationship = "many-to-one")
  codes <- coalesce(mapped$position_code, vb_position_fallback(df$position_raw))

  df |>
    mutate(
      position_clean = codes,
      primary_position = str_replace(codes, "^([^/]+).*$", "\\1"),
      secondary_position = str_replace(codes, "^[^/]+(/)?", ""),
      position_status = case_when(
        is.na(raw) ~ "missing",
        !is.na(codes) ~ "parsed",
        TRUE ~ "unparsed"
      ),
      position_reason = case_when(
        is.na(raw) ~ "position not reported",
        !is.na(mapped$position_code) ~ NA_character_,
        !is.na(codes) ~
          "fallback mapping - add the variant to data/reference/positions.csv",
        TRUE ~ paste0("unrecognized position vocabulary: ", vb_text_display(df$position_raw))
      )
    )
}


## ---------------------------------------------------------------------------
## Normalization: class years
## ---------------------------------------------------------------------------

VB_REDSHIRT_PREFIX <- "^(R-|RS-?|RED-?|RS\\.|R\\.)"

vb_fallback_class <- function(x) {
  map_chr(x, function(v) {
    if (vb_is_blank(v)) return(NA_character_)
    v0 <- v |>
      str_to_upper() |>
      str_squish() |>
      str_remove("^ACADEMIC ") |>
      str_remove(paste0(VB_REDSHIRT_PREFIX, "\\s*")) |>
      str_squish()
    case_when(
      str_detect(v0, "^GR|GRAD") ~ "Graduate Student",
      str_detect(v0, "SIXTH|SEVENTH|6TH") ~ "Sixth Year",
      str_detect(v0, "FIFTH|5TH") ~ "Fifth Year",
      str_detect(v0, "^FR|FRESH|1ST|FIRST|^1$") ~ "Freshman",
      str_detect(v0, "^SO|SOPH|2ND|SECOND|^2$") ~ "Sophomore",
      str_detect(v0, "^JR|JUNIOR|3RD|THIRD|^3$") ~ "Junior",
      str_detect(v0, "^SR|SENIOR|4TH|FOURTH|^4$") ~ "Senior",
      TRUE ~ NA_character_
    )
  })
}

vb_redshirt_marker <- function(x) {
  v <- str_to_upper(coalesce(x, ""))
  str_detect(v, "REDSHIRT") | str_detect(v, VB_REDSHIRT_PREFIX)
}

vb_clean_classes <- function(df, classes_lookup, season) {
  raw <- as.character(df$year_raw) |> vb_as_missing()
  key <- str_to_upper(str_squish(coalesce(raw, "")))
  lookup <- classes_lookup |>
    transmute(
      key = str_to_upper(str_squish(as.character(year_raw))),
      year_clean_lu = na_if(as.character(year_clean), ""),
      redshirt_lu = (as.character(redshirt) %in% c("TRUE", "1", "TRUE"))
    ) |>
    distinct(key, .keep_all = TRUE)
  mapped <- tibble(key = key) |>
    left_join(lookup, by = "key", relationship = "many-to-one")

  # Reported class years may be graduation years (2029 / '29 = the season's
  # senior class).
  season_end <- as.integer(str_sub(season, 1, 4)) + 1L
  # Reported graduation years map to class: the season's senior class
  # graduates in <season_start + 1> (e.g. season 2026-27: 2027 = Senior).
  classes_hi_to_lo <- c("Senior", "Junior", "Sophomore", "Freshman")
  grad_years <- season_end + 0:3
  grad_year_map <- tibble(
    key = c(as.character(grad_years), paste0("'", grad_years %% 100)),
    year_clean_grad = rep(classes_hi_to_lo, 2)
  )
  grad <- tibble(key = key) |>
    left_join(grad_year_map, by = "key") |>
    pull(year_clean_grad)

  # Graduation years are season-dependent: they must resolve through the
  # season's grad map, never through the cross-season lookup (a "2029" entry
  # captured in 2018 means a different class today).
  pattern <- vb_fallback_class(raw)
  explicit_rs <- vb_redshirt_marker(raw) | mapped$redshirt_lu
  is_grad_year_key <- str_detect(key, "^\\d{4}$|^'\\d{2}$")
  year_clean <- ifelse(
    is_grad_year_key,
    coalesce(grad, pattern),
    coalesce(mapped$year_clean_lu, grad, pattern)
  )

  df |>
    mutate(
      year_clean = year_clean,
      # TRUE only for an explicit redshirt marker; FALSE for a recognized
      # non-redshirt value; missing when the raw value is not recognized.
      redshirt = case_when(
        explicit_rs ~ TRUE,
        is.na(raw) ~ NA,
        !is.na(year_clean) ~ FALSE,
        TRUE ~ NA
      ),
      year_status = case_when(
        is.na(raw) ~ "missing",
        !is.na(year_clean) ~ "parsed",
        TRUE ~ "unparsed"
      ),
      year_reason = case_when(
        is.na(raw) ~ "class year not reported",
        !is.na(mapped$year_clean_lu) ~ NA_character_,
        !is.na(grad) ~ "inferred from a reported graduation year",
        !is.na(year_clean) ~
          "pattern fallback - add the variant to data/reference/classes.csv",
        TRUE ~ paste0("unrecognized class year: ", vb_text_display(df$year_raw))
      )
    )
}


## ---------------------------------------------------------------------------
## Normalization: schools
## ---------------------------------------------------------------------------

VB_HS_MARKER <- paste0(
  "(?:\\bH\\.?S\\.?\\b|High School|Secondary School|Grammar School|",
  "Preparatory School|Prep School|Academy|Gymnasium|Lyceum|\\bSchool(?:\\s+of\\b|\\b))"
)

# Historical raw hometowns sometimes carry "City, State / School" or
# "City, State (School)" with the school fields blank. When high_school is
# blank, the school part moves there (ported from the field-hockey scraper).
vb_split_combined_hometown <- function(hometown, high_school) {
  ht <- as.character(hometown)
  hs <- as.character(high_school)
  has_hs <- !vb_is_blank(hs)
  slash <- str_match(ht, "^(.+?)\\s*/\\s*(.+)$")
  paren <- str_match(ht, "^(.+?)\\s*\\(([^)]+)\\)\\s*$")
  tibble(
    hometown_split = !has_hs & !vb_is_blank(ht) & (!is.na(slash[, 1]) | !is.na(paren[, 1])),
    hometown_new = case_when(
      has_hs | vb_is_blank(ht) ~ ht,
      !is.na(slash[, 1]) ~ slash[, 2],
      !is.na(paren[, 1]) ~ paren[, 2],
      TRUE ~ ht
    ),
    high_school_new = case_when(
      has_hs | vb_is_blank(ht) ~ hs,
      !is.na(slash[, 1]) ~ slash[, 3],
      !is.na(paren[, 1]) ~ paren[, 3],
      TRUE ~ hs
    )
  )
}

vb_reclassify_high_school <- function(high_school, previous_school) {
  # Move an explicitly high-school-like previous_school value into high_school
  # (only when high_school is blank). Ambiguous bare school names and generic
  # colleges are left alone - the field-hockey scraper's rule.
  hs <- as.character(high_school)
  ps <- as.character(previous_school)
  hs_blank <- vb_is_blank(hs)
  has_marker <- !vb_is_blank(ps) & str_detect(ps, regex(VB_HS_MARKER, ignore_case = TRUE))
  slash <- str_match(
    ps,
    regex(paste0("^(.+?", VB_HS_MARKER, ")\\s*/\\s*(.+)$"), ignore_case = TRUE)
  )
  transfer <- str_match(
    ps,
    regex(paste0("^(.+?", VB_HS_MARKER, ")\\s+\\(((?:UC\\s+.+)|(?:.*University.*))\\)$"),
          ignore_case = TRUE)
  )
  reclassified <- hs_blank & has_marker
  tibble(
    high_school_reclassified = reclassified,
    high_school_new = case_when(
      !reclassified ~ hs,
      !is.na(slash[, 1]) ~ str_squish(slash[, 2]),
      !is.na(transfer[, 1]) ~ str_squish(transfer[, 2]),
      TRUE ~ str_squish(ps)
    ),
    previous_school_new = case_when(
      !reclassified ~ ps,
      !is.na(slash[, 1]) ~ str_squish(slash[, 3]),
      !is.na(transfer[, 1]) ~ str_squish(transfer[, 3]),
      TRUE ~ NA_character_
    )
  )
}

vb_clean_schools <- function(df, school_mappings) {
  hs_display <- df$high_school_source |> vb_text_display()
  hs_value <- hs_display |> vb_as_missing()
  hs_key <- str_to_upper(vb_school_key(hs_value))

  mappings <- school_mappings |>
    transmute(
      key = str_to_upper(str_squish(as.character(raw_school_key))),
      mapping_id, canonical_school, school_type, confidence, source
    ) |>
    distinct(key, .keep_all = TRUE)
  mapped <- tibble(key = hs_key) |>
    left_join(mappings, by = "key", relationship = "many-to-one")

  ps_display <- df$previous_school_source |> vb_text_display()
  ps_value <- ps_display |> vb_as_missing()
  ps_key <- str_to_upper(vb_school_key(ps_value))
  teams <- vb_teams_registry()
  ps_team <- tibble(key = ps_key) |>
    left_join(teams, by = "key", relationship = "many-to-one") |>
    pull(ncaa_id)

  df |>
    mutate(
      high_school_clean = hs_value,
      high_school_status = case_when(
        is.na(hs_value) ~ "missing",
        !is.na(mapped$canonical_school) ~ "mapped",
        TRUE ~ "unmapped"
      ),
      high_school_reason = case_when(
        is.na(hs_value) ~ "high school not reported",
        !is.na(mapped$canonical_school) ~
          paste0("mapped via data/reference/school_mappings.csv (", mapped$mapping_id, ")"),
        TRUE ~ "no reviewed mapping in data/reference/school_mappings.csv"
      ),
      high_school_mapping_id = mapped$mapping_id,
      high_school_confidence = mapped$confidence,
      high_school_backfill = FALSE,
      high_school_backfill_season = NA_character_,
      previous_school_clean = ps_value,
      previous_school_type = case_when(
        is.na(ps_value) ~ NA_character_,
        str_detect(ps_value, regex(VB_HS_MARKER, ignore_case = TRUE)) ~ "High School",
        str_detect(str_to_upper(ps_value),
                   "JUCO|JR\\.? ?COLLEGE|JUNIOR COLLEGE|COMMUNITY COLLEGE|CITY COLLEGE|(?:^| )CC\\b") ~
          "Junior College",
        str_detect(str_to_upper(ps_value), "COLLEGE|UNIVERSITY|^UNIV\\b|ACADEMY") ~ "College",
        TRUE ~ "Unknown"
      ),
      previous_school_ncaa_id = as.character(ps_team),
      previous_school_status = case_when(
        is.na(ps_value) ~ "missing",
        !is.na(ps_team) ~ "matched",
        TRUE ~ "unparsed"
      )
    )
}


## ---------------------------------------------------------------------------
## Normalization: geography
## ---------------------------------------------------------------------------

vb_clean_hometowns <- function(df, state_variants, country_variants) {
  state_lookup <- state_variants |>
    transmute(
      key = str_to_upper(str_squish(raw_key)),
      state_code, state_name, census_region, is_territory
    ) |>
    distinct(key, .keep_all = TRUE)
  country_lookup <- country_variants |>
    transmute(
      key = str_to_upper(str_squish(raw_key)),
      iso3, country_name, region, admin_region
    ) |>
    distinct(key, .keep_all = TRUE)

  ht_display <- str_replace(coalesce(df$hometown_source, ""), "(?i)(hometown|height):\\s*", "") |>
    vb_text_display() |>
    str_replace("\\s*\\.$", "")

  # Periods collapse, not spread: "N.Y." must become "NY", not "N Y".
  ht_upper <- str_to_upper(str_replace_all(coalesce(ht_display, ""), "\\.", "")) |>
    str_squish()

  # Candidate token: the trailing comma piece ("CITY, STATE / school" already
  # had its school part moved by the schools step; state text may still trail
  # a slash when high_school was already present).
  piece <- ht_upper |>
    str_squish() |>
    str_split(",(?![^(]*\\))") |>
    map(~ {
      p <- str_squish(.x)
      p <- p[p != ""]
      if (length(p) == 0) NA_character_ else p[length(p)]
    }) |>
    unlist()
  # Take the state-ish token before a possible "/ school" remainder.
  piece_main <- piece |>
    str_squish() |>
    str_replace("\\s*/.*$", "") |>
    str_squish()
  # Compound "ALBERTA CANADA" / "ON CAN"
  piece_clean <- piece_main |>
    str_replace("(\\s+CANADA|\\s+CAN|\\s+AUSTRALIA|\\s+USA|\\s+USA\\.)$", "") |>
    str_squish()

  # No-comma hometowns: try the trailing words ("DALLAS TEXAS", "LIMA PERU").
  state_regex <- paste0(
    "\\b(", paste(paste0("(?i:", sort(unique(state_lookup$key[nchar(state_lookup$key) >= 4])), ")"),
                  collapse = "|"), ")\\s*$"
  )
  trailing_state_key <- str_match(ht_upper, regex(str_sub(state_regex, 3)))[, 2]
  trailing_state <- state_lookup$state_code[match(trailing_state_key, state_lookup$key)]

  state_code <- coalesce(
    state_lookup$state_code[match(piece_clean, state_lookup$key)],
    trailing_state
  )
  # Bare country tokens ("CANADA") are not states even when they hit a state key.
  state_code[!is.na(piece_clean) & piece_clean %in% c("CAN", "CANADA", "AUS", "AUSTRALIA")] <- NA_character_

  # The compound token ("ALBERTA CANADA") may itself be the country key.
  country_iso <- coalesce(
    country_lookup$iso3[match(piece_clean, country_lookup$key)],
    country_lookup$iso3[match(piece_main, country_lookup$key)],
    country_lookup$iso3[match(piece, country_lookup$key)]
  )
  country_code <- case_when(
    !is.na(state_code) ~ "USA",
    !is.na(country_iso) ~ country_iso,
    TRUE ~ NA_character_
  )
  country_region <- case_when(
    !is.na(state_code) ~
      coalesce(state_lookup$census_region[match(state_code, state_lookup$state_code)],
               NA_character_),
    !is.na(country_iso) ~
      coalesce(country_lookup$admin_region[match(piece_clean, country_lookup$key)],
               country_lookup$region[match(piece_clean, country_lookup$key)]),
    TRUE ~ NA_character_
  )

  df |>
    mutate(
      hometown_clean = ht_display,
      state = state_code,
      state_name = state_lookup$state_name[match(state_code, state_lookup$state_code)],
      region = country_region,
      country = country_code,
      hometown_status = case_when(
        vb_is_blank(df$hometown_source) |
          (str_squish(coalesce(as.character(df$hometown_source), "")) %in% VB_JUNK_STRINGS) ~ "missing",
        !is.na(state_code) | !is.na(country_code) ~ "parsed",
        TRUE ~ "unparsed"
      ),
      hometown_reason = case_when(
        vb_is_blank(df$hometown_source) |
          (str_squish(coalesce(as.character(df$hometown_source), "")) %in% VB_JUNK_STRINGS) ~
          "hometown not reported",
        !is.na(state_code) | !is.na(country_code) ~ NA_character_,
        TRUE ~ paste0("no state/province/country recognized in: ", ht_display)
      )
    )
}


## ---------------------------------------------------------------------------
## Normalization: URLs and jerseys
## ---------------------------------------------------------------------------

vb_clean_urls <- function(df) {
  url <- df$url_raw |> vb_as_missing()
  df |>
    mutate(
      profile_url = url,
      url_status = case_when(
        is.na(url) ~ "missing",
        str_detect(url, "^https?://") ~ "absolute",
        str_detect(url, "^//") ~ "absolute",
        str_detect(url, "^/") ~ "relative",
        TRUE ~ "invalid"
      )

    )
}

vb_clean_jersey <- function(df) {
  j <- df$jersey_raw |> vb_as_missing()
  df |>
    mutate(
      jersey = j,
      jersey_status = case_when(
        is.na(j) ~ "missing",
        str_detect(j, "^\\d{1,2}$") ~ "parsed",
        str_detect(j, "/") ~ "composite",
        TRUE ~ "unparsed"
      ),
      jersey_reason = case_when(
        is.na(j) ~ "jersey not reported",
        str_detect(j, "^\\d{1,2}$") ~ NA_character_,
        str_detect(j, "/") ~ "multiple numbers recorded (kept as-is)",
        TRUE ~ "unrecognized jersey format"
      )
    )
}


## ---------------------------------------------------------------------------
## Structural duplicate handling (Phase 5)
## ---------------------------------------------------------------------------

vb_mark_exact_duplicates <- function(raw) {
  # Automatically remove only rows identical across all 13 raw fields within
  # a snapshot; the earliest row is retained and removals are logged.
  key13 <- raw |>
    transmute(across(all_of(VB_KEY_FIELDS), ~ as.character(coalesce(.x, "")))) |>
    pmap_chr(paste)
  dups <- duplicated(key13, fromLast = FALSE)  # first occurrence kept
  raw |>
    mutate(
      duplicate_key13 = key13,
      classification_dup = ifelse(dups, "removed_exact_duplicate", NA_character_)
    )
}

vb_duplicate_group_review <- function(raw) {
  # Groups within a team-season that share a strong candidate key: profile
  # URL, nonblank player ID, or normalized name. Matching name/jersey is
  # evidence, not proof; rows are never collapsed merely because both
  # jerseys are missing.
  cand_url <- vb_url_key(raw$url)
  cand_pid <- as.character(coalesce(raw$player_id, ""))
  cand_name <- vb_name_key(raw$name)

  has_url <- !is.na(cand_url) & cand_url != ""
  has_pid <- cand_pid != ""
  group_id_url <- ifelse(has_url, paste0("url|", raw$ncaa_id, "|", cand_url), NA_character_)
  group_id_pid <- ifelse(has_pid, paste0("pid|", raw$ncaa_id, "|", cand_pid), NA_character_)
  group_id_name <- ifelse(!is.na(cand_name) & cand_name != "",
                          paste0("name|", raw$ncaa_id, "|", cand_name), NA_character_)

  raw |>
    mutate(group_id_url, group_id_pid, group_id_name)
}

vb_resolve_duplicate_groups <- function(raw, row_corrections) {
  # Groups within a team-season that share a strong candidate key: profile
  # URL, nonblank player ID, or normalized name. Matching name/jersey is
  # evidence, not proof; rows are never collapsed merely because both jerseys
  # are missing. Reviewed dispositions come from row_corrections.csv
  # (drop_duplicate with a keeper, keep_distinct). Unresolved conflicting
  # groups quarantine their rows.
  # Only this snapshot's corrections apply here; other seasons' rows are
  # validated in their own build.
  snapshot_prefix <- unique(raw$snapshot_id)[1]
  drops <- row_corrections |>
    filter(action == "drop_duplicate",
           str_detect(coalesce(as.character(source_row_id), ""),
                      paste0("^", snapshot_prefix, "#")))
  keeps <- row_corrections |>
    filter(action == "keep_distinct",
           str_detect(coalesce(as.character(source_row_id), ""),
                      paste0("^", snapshot_prefix, "#")))
  for (vec in list(drops$source_row_id, keeps$source_row_id)) {
    bad_ids <- setdiff(as.character(na.omit(vec)), as.character(raw$source_row_id))
    if (length(bad_ids) > 0) {
      stop("row_corrections.csv: source_row_id(s) not in this snapshot: ",
           paste(bad_ids, collapse = ", "))
    }
  }
  bad_keepers <- setdiff(
    as.character(na.omit(drops$keeper_source_row_id)),
    as.character(raw$source_row_id)
  )
  if (length(bad_keepers) > 0) {
    stop("row_corrections.csv: drop_duplicate keeper not in this snapshot: ",
         paste(bad_keepers, collapse = ", "))
  }

  # Group counting runs on the non-exact-duplicate rows only; statuses are
  # then joined back onto every raw row.
  kept <- raw |>
    filter(is.na(classification_dup) | classification_dup != "removed_exact_duplicate") |>
    mutate(
      cand_url = vb_url_key(url_raw),
      cand_pid = str_squish(coalesce(as.character(player_id), "")),
      cand_name = vb_name_key(coalesce(as.character(name_raw), ""))
    )
  if (nrow(kept) == 0) {
    return(raw |>
      mutate(
        cand_url = NA_character_, cand_pid = "", cand_name = NA_character_,
        group_id = NA_character_, candidate_key = NA_character_,
        dup_review_status = NA_character_
      ))
  }
  rows <- kept
  group_id_url <- ifelse(!is.na(rows$cand_url) & rows$cand_url != "" & !is.na(rows$ncaa_id),
                         paste0("url|", rows$ncaa_id, "|", rows$cand_url), NA_character_)
  group_id_pid <- ifelse(rows$cand_pid != "" & !is.na(rows$ncaa_id),
                         paste0("pid|", rows$ncaa_id, "|", rows$cand_pid), NA_character_)
  group_id_name <- ifelse(!is.na(rows$cand_name) & rows$cand_name != "" & !is.na(rows$ncaa_id),
                          paste0("name|", rows$ncaa_id, "|", rows$cand_name), NA_character_)

  n_in_group <- function(col) {
    tab <- table(na.omit(col))
    as.integer(ifelse(is.na(col), 0L, tab[match(col, names(tab))]))
  }
  # A shared profile URL is strong evidence only when the recorded names agree
  # or one name is missing: bare site-root URLs are shared by entire rosters,
  # so grouping by them alone would collide unrelated players. (The URL key is
  # compared within a URL group, so blank-vs-name still counts.)
  url_group_name_ok <- function(rows) {
    out <- rep(FALSE, nrow(rows))
    tab <- table(na.omit(rows$group_id_url))
    urls_in_multi <- names(tab[tab > 1L])
    for (u in urls_in_multi) {
      idx <- which(rows$group_id_url == u)
      names_in_group <- rows$cand_name[idx]
      nonblank <- names_in_group[!is.na(names_in_group) & names_in_group != ""]
      if (length(nonblank) <= 1 || n_distinct(nonblank) == 1) {
        out[idx] <- TRUE
      }
    }
    out
  }
  rows$group_id_url <- group_id_url
  rows$group_id_pid <- group_id_pid
  rows$group_id_name <- group_id_name
  url_conflict_ok <- rep(FALSE, nrow(rows))
  multi_urls <- names(table(na.omit(rows$group_id_url))[table(na.omit(rows$group_id_url)) > 1L])
  if (length(multi_urls) > 0) {
    in_multi <- !is.na(rows$group_id_url) & rows$group_id_url %in% multi_urls
    idx_multi <- which(in_multi)
    url_conflict_ok[idx_multi] <- url_group_name_ok(rows[idx_multi, ])
  }

  rows <- rows |>
    mutate(
      url_group_conflict = url_conflict_ok,
      group_id = coalesce(
        ifelse(url_group_conflict, group_id_url, NA_character_),
        ifelse(n_in_group(group_id_pid) > 1L, group_id_pid, NA_character_),
        ifelse(n_in_group(group_id_name) > 1L, group_id_name, NA_character_)
      ),
      candidate_key = case_when(
        url_group_conflict ~ "profile_url",
        n_in_group(group_id_pid) > 1L ~ "player_id",
        n_in_group(group_id_name) > 1L ~ "name",
        TRUE ~ NA_character_
      ),
      dup_review_status = case_when(
        source_row_id %in% as.character(drops$source_row_id) ~ "dropped_reviewed",
        # drop_duplicate keepers are resolved by their reviewed disposition
        source_row_id %in% as.character(na.omit(drops$keeper_source_row_id)) ~ "kept_reviewed",
        source_row_id %in% as.character(keeps$source_row_id) ~ "kept_reviewed",
        !is.na(candidate_key) ~ "unresolved",
        TRUE ~ "single"
      )
    )
  # Re-attach statuses to the full raw frame; exact duplicates get NA
  # statuses and take no further part in duplicate review.
  status_cols <- c("cand_url", "cand_pid", "cand_name", "group_id",
                   "candidate_key", "dup_review_status")
  raw |>
    select(-any_of(status_cols)) |>
    left_join(
      rows |> select(source_row_id, any_of(status_cols)),
      by = "source_row_id",
      relationship = "many-to-one"
    )
}


## ---------------------------------------------------------------------------
## Stale-roster audit (ported from the basketball repo / notebook)
## ---------------------------------------------------------------------------

vb_stale_roster_audit <- function(clean, prev_clean) {
  # A team-season is stale when it shares at least five profile URLs with the
  # previous season AND identical names/classes for at least 90% of the
  # compared roster. A flag is an audit finding; only reviewed entries in
  # data/reference/source_exclusions.csv remove rows from publication.
  if (is.null(prev_clean) || nrow(prev_clean) == 0 ||
      !("profile_url" %in% names(prev_clean) && "year_clean" %in% names(prev_clean))) {
    return(NULL)
  }
  cur <- clean |>
    filter(!is.na(profile_url), profile_url != "", !is.na(year_clean)) |>
    transmute(
      ncaa_id = as.character(ncaa_id),
      url_k = vb_url_key(profile_url),
      name_key,
      year_clean = as.character(year_clean)
    ) |>
    filter(url_k != "")
  prev <- prev_clean |>
    filter(!is.na(profile_url), profile_url != "", !is.na(year_clean)) |>
    transmute(
      ncaa_id = as.character(ncaa_id),
      url_k = vb_url_key(profile_url),
      prev_name_key = vb_name_key(name),
      prev_year = as.character(year_clean)
    ) |>
    filter(url_k != "")

  cur |>
    left_join(prev, by = c("ncaa_id", "url_k"), relationship = "many-to-many") |>
    group_by(ncaa_id) |>
    summarise(
      players = n(),
      shared = sum(!is.na(prev_name_key)),
      identical = sum(!is.na(prev_name_key) & name_key == prev_name_key &
                        year_clean == prev_year),
      .groups = "drop"
    ) |>
    filter(shared >= 5, identical >= 0.9 * players, players > 0) |>
    mutate(flag = "stale_roster_copy")
}


## ---------------------------------------------------------------------------
## High-school backfill (chronological, same team only)
## ---------------------------------------------------------------------------

vb_backfill_high_schools <- function(clean, prev_clean, season) {
  # Backfill missing high schools from the previous season's corrected
  # baseline (its published clean file), preferring a matching nonblank player
  # ID or normalized profile URL within the same team; otherwise requiring
  # exactly one exact normalized-name candidate. Never across teams, never on
  # fuzzy names; conflicting candidates skip and the source season is
  # recorded.
  needed_cols <- c("player_id", "profile_url", "name_key", "high_school_clean")
  if (is.null(prev_clean) || nrow(prev_clean) == 0 ||
      !all(needed_cols %in% names(prev_clean))) {
    return(clean |>
             mutate(high_school_backfill = FALSE,
                    high_school_backfill_season = NA_character_))
  }
  prev_hs <- prev_clean |>
    filter(!is.na(high_school_clean), high_school_clean != "") |>
    transmute(
      ncaa_id = as.character(ncaa_id),
      p_pid = str_squish(coalesce(as.character(player_id), "")),
      p_url = coalesce(profile_url, ""),
      p_name = name_key,
      hs_prev = high_school_clean
    )
  if (nrow(prev_hs) == 0) {
    return(clean |>
             mutate(high_school_backfill = FALSE,
                    high_school_backfill_season = NA_character_))
  }
  prev_hs$season_backfill <- unique(str_squish(coalesce(as.character(prev_clean$season), "")))[1]

  need <- clean |>
    filter(is.na(high_school_clean) | high_school_clean == "") |>
    transmute(
      source_row_id,
      ncaa_id = as.character(ncaa_id),
      n_pid = str_squish(coalesce(as.character(player_id), "")),
      n_url = coalesce(profile_url, ""),
      n_name = name_key
    )
  if (nrow(need) == 0) {
    return(clean |>
             mutate(high_school_backfill = FALSE,
                    high_school_backfill_season = NA_character_))
  }

  # Candidate 1: matching player ID and/or URL within the same team.
  by_key <- need %>%
    inner_join(
      prev_hs |>
        filter(p_pid != "" | p_url != "") |>
        distinct(ncaa_id, p_pid, p_url, hs_prev),
      by = c("ncaa_id", "n_pid" = "p_pid", "n_url" = "p_url"),
      relationship = "many-to-many"
    ) %>%
    filter(hs_prev != "") |>
    distinct(source_row_id, hs_prev)

  # Candidate 2: exactly one exact normalized-name candidate in the team.
  by_name_cnt <- need %>%
    inner_join(
      prev_hs |>
        filter(p_name != "") |>
        distinct(ncaa_id, p_name, hs_prev),
      by = c("ncaa_id", "n_name" = "p_name"),
      relationship = "many-to-many"
    ) %>%
    add_count(source_row_id, name = "n_cand") %>%
    filter(hs_prev != "", n_cand == 1) %>%
    distinct(source_row_id, hs_prev)

  combined <- bind_rows(by_key, by_name_cnt) %>%
    group_by(source_row_id) %>%
    filter(n_distinct(hs_prev) == 1) %>%
    slice(1) %>%
    ungroup()

  backfill_season <- prev_hs$season_backfill[1]
  clean |>
    left_join(
      combined |> transmute(source_row_id, hs_prev),
      by = "source_row_id",
      relationship = "many-to-one"
    ) |>
    mutate(
      hs_backfilled = !is.na(hs_prev),
      high_school_backfill_season = ifelse(hs_backfilled, backfill_season, NA_character_),
      high_school_clean = coalesce(high_school_clean, hs_prev),
      high_school_backfill = hs_backfilled,
      high_school_status = case_when(
        hs_backfilled ~ "backfilled",
        is.na(high_school_clean) | high_school_clean == "" ~ "missing",
        !is.na(high_school_mapping_id) ~ "mapped",
        TRUE ~ "unmapped"
      ),
      high_school_reason = case_when(
        hs_backfilled ~
          paste0("backfilled from the ", high_school_backfill_season,
                 " baseline (matching player identity within the same team)"),
        .default = high_school_reason
      )
    ) |>
    select(-any_of(c('hs_prev', 'backfill_season')))
}


## ---------------------------------------------------------------------------
## Team metadata join (canonical registry + effective-dated affiliations)
## ---------------------------------------------------------------------------

vb_join_team_metadata <- function(clean, affiliations, teams_canonical) {
  aff <- affiliations |>
    transmute(
      ncaa_id = as.character(ncaa_id),
      first_season, last_season,
      division = as.character(division),
      conference = as.character(coalesce(conference, ""))
    )
  clean |>
    mutate(ncaa_id = as.character(ncaa_id)) |>
    left_join(
      teams_canonical |>
        transmute(ncaa_id = as.character(ncaa_id), canonical_team = team_name, team_state),
      by = "ncaa_id",
      relationship = "many-to-one"
    ) |>
    left_join(aff, by = "ncaa_id", relationship = "many-to-one") |>
    mutate(
      in_range = season >= first_season & season <= last_season,
      division = if_else(in_range, coalesce(division, "NA"), NA_character_),
      conference = if_else(in_range, coalesce(conference, ""), NA_character_),
      team_name_status = case_when(
        is.na(canonical_team) ~ "unmatched",
        team == canonical_team ~ "matched",
        TRUE ~ "matched_id_name_differs"
      )
    ) |>
    select(-in_range)
}


## ---------------------------------------------------------------------------
## Output assembly
## ---------------------------------------------------------------------------

vb_prev_season_clean <- function(season, clean_dir = "data/clean") {
  start <- as.integer(str_sub(season, 1, 4))
  prev <- vb_season_label(start - 1L)
  path <- file.path(clean_dir, paste0("vb_rosters_", prev, ".csv"))
  if (!file.exists(path)) return(NULL)
  vb_read_reference_csv(path)
}

# Project to the published clean schema, in the canonical column order.
vb_finalize_clean_schema <- function(df) {
  df |>
    mutate(
      schema_version = VB_SCHEMA_VERSION,
      snapshot_id = as.character(snapshot_id),
      source_row_number = as.integer(source_row_number),
      season_raw = as.character(season_raw),
      ncaa_id = as.character(ncaa_id),
      player_id = coalesce(as.character(player_id), ""),
      across(c(name_raw, jersey_raw, year_raw, hometown_raw, high_school_raw,
               previous_school_raw, height_raw, position_raw, url_raw),
             as.character),
      name = vb_text_display(name),
      name_key = vb_name_key(name),
      redshirt = as.character(redshirt),
      high_school_backfill = as.character(high_school_backfill),
      across(c(height_ft, height_in, total_inches), as.integer),
      across(everything(), as.character)
    ) |>
    select(any_of(VB_CLEAN_SCHEMA))
}

vb_validate_clean <- function(clean, season) {
  problems <- character(0)
  if (anyDuplicated(clean$source_row_id)) problems <- append(problems, "duplicate source_row_id in clean output")
  if (nrow(clean) == 0) problems <- append(problems, empty_to_na <- "clean output is empty")
  missing_schema <- setdiff(VB_CLEAN_SCHEMA, names(clean))
  if (length(missing_schema) > 0) {
    problems <- append(problems, paste0("schema missing: ",
                                        paste(missing_schema, collapse = ", ")))
  }
  if (length(problems) > 0) {
    stop("clean output validation failed for season ", season, ": ",
         paste(problems, collapse = "; "))
  }
  invisible(TRUE)
}


## ---------------------------------------------------------------------------
## Audits and accounting
## ---------------------------------------------------------------------------

vb_row_issues <- function(raw) {
  raw |>
    filter(
      !is.na(classification) & classification != "accepted" |
        !is.na(corrections_stale_note) |
        !is.na(screen_reason)
    ) |>
    transmute(
      snapshot_id, source_row_id, season, ncaa_id = as.character(ncaa_id), team,
      field = ifelse(!is.na(screen_reason) | !is.na(classification),
                     "row", "correction"),
      raw_value = str_squish(coalesce(name_raw, "")),
      issue_code = coalesce(
        classification,
        ifelse(!is.na(corrections_stale_note), "stale_correction", NA_character_)
      ),
      severity = case_when(
        classification %in% c("quarantined", "removed_exact_duplicate",
                              "removed_reviewed_duplicate",
                              "excluded_reviewed_stale") ~ "quarantine",
        TRUE ~ "warning"
      ),
      explanation = coalesce(classification_reason, corrections_stale_note,
                             screen_reason)
    )
}

vb_team_coverage <- function(raw, clean, teams_canonical) {
  counts <- bind_rows(
    raw |>
      transmute(ncaa_id = as.character(ncaa_id),
                rows_raw = 1L,
                quarantined = as.integer(classification == "quarantined"),
                removed_dup = as.integer(classification %in% c(
                  "removed_exact_duplicate", "removed_reviewed_duplicate")),
                excluded = as.integer(classification == "excluded_reviewed_stale"),
                published = 0L),
    clean |>
      transmute(ncaa_id = as.character(ncaa_id),
                rows_raw = 0L, quarantined = 0L, removed_dup = 0L,
                excluded = 0L, published = 1L)
  ) |>
    group_by(ncaa_id) |>
    summarise(across(everything(), sum), .groups = "drop") |>
    left_join(
      teams_canonical |>
        transmute(ncaa_id = as.character(ncaa_id), team = team_name),
      by = "ncaa_id"
    ) |>
    relocate(team, ncaa_id) |>
    arrange(ncaa_id)
  counts
}

vb_quality_summary <- function(raw, clean, stale, cell_corrections_applied = 0L) {
  tibble(
    metric = c(
      "rows_raw", "rows_published", "rows_quarantined",
      "rows_removed_exact_duplicate", "rows_removed_reviewed_duplicate",
      "rows_excluded_reviewed_stale",
      "heights_failed_parsing_nonempty_raw", "positions_unmapped_nonempty_raw",
      "classes_unmapped_nonempty_raw", "state_or_country_unparsed_nonempty_raw",
      "high_school_missing", "high_school_backfilled",
      "cell_corrections_applied", "stale_roster_teams_flagged",
      "height_parse_rate_pct", "position_parse_rate_pct",
      "class_parse_rate_pct", "hometown_parse_rate_pct"
    ),
    value = c(
      nrow(raw),
      nrow(clean),
      sum(raw$classification == "quarantined"),
      sum(raw$classification == "removed_exact_duplicate"),
      sum(raw$classification == "removed_reviewed_duplicate"),
      sum(raw$classification == "excluded_reviewed_stale"),
      sum(!is.na(clean$height_raw) & str_squish(clean$height_raw) != "" &
            is.na(clean$total_inches)),
      sum(!is.na(clean$position_raw) & str_squish(clean$position_raw) != "" &
            is.na(clean$position_clean)),
      sum(!is.na(clean$year_raw) & str_squish(clean$year_raw) != "" &
            is.na(clean$year_clean)),
      sum(clean$hometown_status == "unparsed"),
      sum(is.na(clean$high_school_clean) | clean$high_school_clean == ""),
      sum(clean$high_school_backfill == "TRUE"),
      cell_corrections_applied,
      if (is.null(stale)) 0L else nrow(stale),
      round(100 * sum(!is.na(clean$height_status) & clean$height_status == "parsed") /
              max(sum(clean$height_status != "missing"), 1), 1),
      round(100 * sum(clean$position_status == "parsed") /
              max(sum(clean$position_status != "missing"), 1), 1),
      round(100 * sum(clean$year_status == "parsed") /
              max(sum(clean$year_status != "missing"), 1), 1),
      round(100 * sum(clean$hometown_status == "parsed") /
              max(sum(clean$hometown_status != "missing"), 1), 1)
    )
  )
}

vb_duplicate_review_table <- function(raw, row_corrections) {
  reviewed <- row_corrections
  keeper_of <- setNames(
    as.character(reviewed$keeper_source_row_id[reviewed$action == "drop_duplicate"]),
    as.character(reviewed$source_row_id[reviewed$action == "drop_duplicate"])
  )
  raw |>
    filter(!is.na(candidate_key)) |>
    transmute(
      group_id,
      candidate_key,
      target_source_row_id = source_row_id,
      keeper_source_row_id = unname(keeper_of[source_row_id]),
      review_status = dup_review_status,
      disposition = case_when(
        !is.na(keeper_of[source_row_id]) ~ "drop_duplicate (reviewed)",
        review_status == "kept_reviewed" ~ "keep_distinct (reviewed)",
        review_status == "dropped_reviewed" ~ "dropped (reviewed)",
        review_status == "unresolved" ~ "unresolved - quarantined",
        TRUE ~ NA_character_
      )
    ) |>
    distinct() |>
    arrange(group_id, target_source_row_id)
}

vb_reconciliation_table <- function(raw, clean) {
  tab <- raw |>
    count(classification, name = "rows")
  tab |>
    mutate(
      order = match(classification, c(
        "removed_exact_duplicate", "quarantined", "removed_reviewed_duplicate",
        "excluded_reviewed_stale", "accepted"
      ))
    ) |>
    arrange(order) |>
    transmute(classification, rows) |>
    mutate(
      share_of_raw = rows / sum(rows)
    )
}

vb_reconcile <- function(raw, clean, correction_note = NULL) {
  # Enforce:
  #   raw rows = clean rows + quarantined rows + exact duplicate removals
  #              + reviewed duplicate removals + reviewed source exclusions
  accepted_classification <- "accepted"
  n_raw <- nrow(raw)
  n_clean <- nrow(clean)
  n_quar <- sum(raw$classification == "quarantined")
  n_exact <- sum(raw$classification == "removed_exact_duplicate")
  n_reviewed <- sum(raw$classification == "removed_reviewed_duplicate")
  n_excl <- sum(raw$classification == "excluded_reviewed_stale")
  accounted <- n_clean + n_quar + n_exact + n_reviewed + n_excl
  list(
    balanced = accounted == n_raw && n_clean == sum(raw$classification == accepted_classification),
    raw_rows = n_raw,
    clean_rows = n_clean,
    quarantined = n_quar,
    exact_duplicate_removals = n_exact,
    reviewed_duplicate_removals = n_reviewed,
    reviewed_exclusions = n_excl
  )
}


## ---------------------------------------------------------------------------
## Season build (orchestrates the full pipeline for one season)
## ---------------------------------------------------------------------------

vb_prev_season_for <- function(season) {
  vb_season_label(as.integer(str_sub(season, 1, 4)) - 1L)
}

vb_clean_season <- function(season,
                            refs = vb_load_reference_tables(),
                            corrections = vb_load_corrections(),
                            clean_dir = "data/clean",
                            prev_clean = NULL,
                            allow_partial = FALSE) {
  stopifnot(vb_valid_season_name(season))
  if (is.null(prev_clean)) prev_clean <- vb_prev_season_clean(season, clean_dir)

  vb_env_reset()
  vb_teams_registry_set(refs$teams_canonical)
  snapshot <- vb_active_snapshot(refs$raw_snapshots, season)
  raw <- vb_read_raw_snapshot(snapshot)
  n_raw <- nrow(raw)
  known_team_ids <- as.character(refs$teams_canonical$ncaa_id)

  ## 1. Corrections on raw values -------------------------------------------
  raw <- vb_apply_cell_corrections(raw, corrections$cell)
  applied_cell <- sum(!is.na(raw$corrections_applied))
  stale_cell_notes <- raw |>
    filter(!is.na(corrections_stale_note)) |>
    transmute(source_row_id, note = corrections_stale_note)

  ## 2. Structural screens ---------------------------------------------------
  unknown_team <- !(as.character(raw$ncaa_id) %in% known_team_ids) & !vb_is_blank(raw$ncaa_id)
  shifted <- vb_shifted_signature(raw$name_raw, raw$year_raw, raw$height_raw,
                                  raw$position_raw, raw$jersey_raw)
  screen_reason <- dplyr::case_when(
    vb_is_blank(raw$name_raw) ~ "blank player name",
    vb_screen_numeric_only(raw$name_raw) ~ "numeric-only name",
    unknown_team ~ "team not in the canonical registry",
    vb_screen_staff_name(raw$name_raw) ~ "staff/title entry, not a player",
    !is.na(shifted) ~ shifted,
    TRUE ~ NA_character_
  )

  quarantine_ids <- as.character(vb_row_correction_actions(corrections$row, "quarantine"))
  restore_ids <- as.character(vb_row_correction_actions(corrections$row, "restore"))

  raw <- raw |>
    mutate(
      screen_reason,
      review_quarantine = as.character(source_row_id) %in% quarantine_ids,
      review_restore = as.character(source_row_id) %in% restore_ids
    )

  ## 3. Exact duplicates, then duplicate-group review ------------------------
  raw <- vb_mark_exact_duplicates(raw)
  raw <- vb_resolve_duplicate_groups(raw, corrections$row)

  raw <- raw |>
    mutate(
      classification = case_when(
        !is.na(classification_dup) ~ "removed_exact_duplicate",
        review_quarantine ~ "quarantined",
        !is.na(screen_reason) & !review_restore ~ "quarantined",
        dup_review_status == "dropped_reviewed" ~ "removed_reviewed_duplicate",
        dup_review_status == "unresolved" ~ "quarantined",
        TRUE ~ "accepted"
      ),
      classification_reason = case_when(
        classification == "removed_exact_duplicate" ~
          "identical to another row across all 13 raw fields (earliest row retained)",
        classification == "quarantined" & review_quarantine ~
          "reviewed quarantine in corrections/row_corrections.csv",
        classification == "quarantined" ~ coalesce(
          screen_reason,
          paste0("unresolved duplicate group (", coalesce(candidate_key, "unknown"),
                 "): ", coalesce(group_id, "unknown"))
        ),
        classification == "removed_reviewed_duplicate" ~
          "reviewed drop_duplicate in corrections/row_corrections.csv",
        TRUE ~ NA_character_
      )
    )

  ## 4. Normalization of accepted rows --------------------------------------
  accepted <- raw |>
    filter(classification == "accepted") |>
    arrange(source_row_number) |>
    select(-any_of(c("screen_reason", "review_quarantine", "review_restore")))

  accepted <- accepted |>
    mutate(
      hs_blank = vb_is_blank(high_school_raw),
      high_school_raw = vb_as_missing(high_school_raw),
      previous_school_raw = vb_as_missing(previous_school_raw),
      hometown_raw = vb_as_missing(hometown_raw)
    )
  split_ht <- vb_split_combined_hometown(accepted$hometown_raw, accepted$high_school_raw)
  recl <- vb_reclassify_high_school(split_ht$high_school_new, accepted$previous_school_raw)
  accepted <- accepted |>
    mutate(
      hometown_split_into_school = split_ht$hometown_split,
      high_school_reclassified = recl$high_school_reclassified,
      hometown_source = split_ht$hometown_new,
      high_school_source = recl$high_school_new,
      previous_school_source = recl$previous_school_new
    ) |>
    select(-hs_blank)

  accepted <- accepted |>
    vb_clean_heights() |>
    {\(.df) vb_clean_positions(.df, refs$positions)}() |>
    {\(.df) vb_clean_classes(.df, refs$classes, season)}() |>
    vb_clean_schools(refs$school_mappings) |>
    {\(.df) vb_clean_hometowns(.df, refs$state_variants, refs$country_variants)}() |>
    vb_clean_urls() |>
    vb_clean_jersey() |>
    mutate(name = vb_text_display(coalesce(name_raw, NA_character_)),
           name_key = vb_name_key(name))

  accepted <- vb_backfill_high_schools(accepted, prev_clean, season)

  clean_pre <- vb_finalize_schema(accepted, teams = TRUE)
  stale <- vb_stale_roster_audit(clean_pre, prev_clean)

  ## 5. Reviewed source exclusions (stale rosters etc.) ----------------------
  exclusion_teams <- refs$source_exclusions |>
    transmute(ncaa_id = as.character(ncaa_id), season) |>
    filter(season == !!season)
  excluded_ids <- accepted |>
    mutate(ncaa_id = as.character(ncaa_id)) |>
    inner_join(exclusion_teams, by = c("ncaa_id", "season"),
               relationship = "many-to-many") |>
    pull(source_row_id)

  raw <- raw |>
    mutate(
      classification = case_when(
        source_row_id %in% excluded_ids & classification %in% c("accepted", "excluded_reviewed_stale") ~
          "excluded_reviewed_stale",
        classification == "removed_exact_duplicate" ~ "removed_exact_duplicate",
        TRUE ~ classification
      ),
      classification_reason = ifelse(
        classification == "excluded_reviewed_stale" & is.na(classification_reason),
        "reviewed row exclusion for a flagged stale-roster copy (data/reference/source_exclusions.csv)",
        classification_reason
      )
    )
  accepted <- accepted |>
    filter(!source_row_id %in% excluded_ids)

  ## 6. Finalize -------------------------------------------------------------
  clean <- accepted |>
    vb_join_team_metadata(refs$team_affiliations, refs$teams_canonical)
  clean <- vb_finalize_schema(clean, teams = TRUE) |>
    arrange(as.integer(ncaa_id), name_key, source_row_number)
  vb_validate_clean(clean, season)

  ## 7. Reconcile ------------------------------------------------------------
  reconciled <- vb_reconcile(raw, clean)
  if (!isTRUE(reconciled$balanced)) {
    stop("row reconciliation failed for season ", season, ": accounted=",
         reconciled$clean_rows + reconciled$quarantined +
           reconciled$exact_duplicate_removals + reconciled$reviewed_duplicate_removals +
           reconciled$reviewed_exclusions, " raw_rows=", reconciled$raw_rows)
  }

  ## 8. Coverage requirement --------------------------------------------------
  published_teams <- unique(clean$ncaa_id)
  raw_teams <- unique(as.character(raw$ncaa_id))
  lost_teams <- setdiff(raw_teams, published_teams)
  approved_losts <- refs$coverage_exceptions |>
    transmute(ncaa_id = as.character(ncaa_id), season) |>
    filter(season == !!season) |>
    pull(ncaa_id)
  unapproved_lost <- setdiff(lost_teams, approved_losts)
  unresolved <- sum(raw$classification == "quarantined" &
                      str_detect(coalesce(raw$classification_reason, ""), "unresolved"))
  if (!allow_partial && (length(unapproved_lost) > 0 || unresolved > 0)) {
    stop(
      "build stopped for season ", season, ": ", length(unapproved_lost),
      " team season(s) with raw rows published nothing: ",
      paste(head(unapproved_lost, 20), collapse = ", "),
      "; and ", unresolved, " unresolved duplicate row(s). Review them via ",
      "corrections/ and data/reference/coverage_exceptions.csv, or pass ",
      "--allow-partial."
    )
  }

  list(
    raw = raw,
    clean = clean,
    snapshot = snapshot,
    stale = stale,
    stale_cell_notes = stale_cell_notes,
    n_raw = n_raw,
    n_accepted = nrow(clean),
    cell_corrections_applied = applied_cell,
    reconciled = reconciled,
    lost_teams = unapproved_lost
  )
}

vb_valid_season_name <- function(season) {
  isTRUE(!is.na(season) &&
           str_detect(str_trim(as.character(season)), "^\\d{4}-\\d{2}$"))
}

vb_finalize_schema <- function(df, teams = TRUE) {
  df |>
    mutate(
      schema_version = VB_SCHEMA_VERSION,
      snapshot_id = as.character(snapshot_id),
      source_file, source_row_number = as.integer(source_row_number),
      source_row_id = as.character(source_row_id),
      season_raw = coalesce(as.character(season_raw), ""),
      season = as.character(season),
      ncaa_id = as.character(ncaa_id),
      team = as.character(team),
      player_id = coalesce(str_squish(as.character(player_id)), ""),
      across(c(name_raw, jersey_raw, year_raw, hometown_raw, high_school_raw,
               previous_school_raw, height_raw, position_raw, url_raw),
             as.character),
      name = vb_text_display(as.character(name)),
      name_key = vb_name_key(name),
      jersey = as.character(jersey),
      redshirt = as.character(redshirt),
      high_school_backfill = as.character(high_school_backfill),
      high_school_backfill_season = as.character(high_school_backfill_season),
      across(c(height_ft, height_in, total_inches), as.integer),
      across(c(state, state_name, region, country), as.character)
    ) |>
    select(any_of(VB_CLEAN_SCHEMA))
}
