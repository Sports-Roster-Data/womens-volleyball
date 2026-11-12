#!/usr/bin/env Rscript
# Build cleaned, audited volleyball roster season files from registered raw
# snapshots.
#
#   Rscript scripts/build_rosters.R --season 2026-27
#   Rscript scripts/build_rosters.R --all
#   Rscript scripts/build_rosters.R --all --allow-partial
#   Rscript scripts/build_rosters.R --all --render
#
# Seasons are processed chronologically so prior-season enrichment (the
# high-school backfill) is deterministic. Outputs are atomically promoted via
# temp-file + rename and contain no generated timestamps, so repeated builds
# are byte-identical.
#
#   data/clean/vb_rosters_<season>.csv
#   data/derived/baseline/vb_rosters_<season>.csv
#   data/derived/quarantine/vb_rosters_<season>.csv
#   data/derived/audits/<season>/{row_issues,duplicate_review,team_coverage,
#                                 quality_summary,reconciliation}.csv

suppressPackageStartupMessages({
  library(tidyverse)
})
source("R/roster_cleaning.R")  # run from the repository root

args <- commandArgs(trailingOnly = TRUE)
get_arg <- function(flag, default = NULL) {
  idx <- match(flag, args)
  if (!is.na(idx) && idx < length(args)) return(args[idx + 1])
  default
}
has_flag <- function(flag) flag %in% args

SEASON_ARG    <- get_arg("--season", NULL)
ALL_SEASONS   <- has_flag("--all")
ALLOW_PARTIAL <- has_flag("--allow-partial")
RENDER        <- has_flag("--render") ||
  identical(get_arg("--render", NULL), "true")

if (is.null(SEASON_ARG) && !ALL_SEASONS) {
  stop("pass --season YYYY-YY or --all")
}

refs <- vb_load_reference_tables()
corrections <- vb_load_corrections()

seasons <- if (!is.null(SEASON_ARG)) {
  if (!vb_valid_season_name(SEASON_ARG)) {
    stop("--season expects a full academic-year label such as 2026-27; received ",
         SEASON_ARG)
  }
  SEASON_ARG
} else {
  refs$raw_snapshots |>
    filter(active == TRUE) |>
    pull(season)
}
seasons <- seasons[order(as.integer(str_sub(seasons, 1, 4)))]

vb_atomic_write_csv <- function(df, path) {
  tmp <- paste0(path, ".tmp")
  readr::write_csv(df, tmp, na = "", quote = "all")
  if (!file.rename(tmp, path)) {
    file.copy(tmp, path, overwrite = TRUE)
    unlink(tmp)
  }
  path
}

for (season in seasons) {
  message("=== building ", season, " ===")
  out <- vb_clean_season(
    season,
    refs = refs,
    corrections = corrections,
    allow_partial = ALLOW_PARTIAL
  )

  season_dir <- file.path("data", "derived", "audits", season)
  dir.create(season_dir, showWarnings = FALSE, recursive = TRUE)
  dir.create("data/clean", showWarnings = FALSE, recursive = TRUE)
  dir.create(file.path("data", "derived", "baseline"), showWarnings = FALSE, recursive = TRUE)
  dir.create(file.path("data", "derived", "quarantine"), showWarnings = FALSE, recursive = TRUE)

  clean_path <- file.path("data", "clean", paste0("vb_rosters_", season, ".csv"))
  baseline_path <- file.path("data", "derived", "baseline", paste0("vb_rosters_", season, ".csv"))
  quar_path <- file.path("data", "derived", "quarantine", paste0("vb_rosters_", season, ".csv"))

  vb_atomic_write_csv(out$clean, clean_path)

  baseline <- out$raw |>
    transmute(
      schema_version = VB_SCHEMA_VERSION,
      snapshot_id, source_file, source_row_number, source_row_id,
      season_raw, season,
      ncaa_id = as.character(ncaa_id), team, player_id,
      across(all_of(c("name_raw", "jersey_raw", "year_raw", "hometown_raw",
                      "high_school_raw", "previous_school_raw", "height_raw",
                      "position_raw", "url_raw")), as.character),
      corrections_applied, classification, classification_reason
    ) |>
    select(any_of(VB_BASELINE_SCHEMA))
  vb_atomic_write_csv(baseline, baseline_path)

  quarantined <- out$raw |>
    filter(classification == "quarantined") |>
    transmute(
      snapshot_id, source_file, source_row_number, source_row_id,
      season, ncaa_id = as.character(ncaa_id), team, player_id,
      across(all_of(c("name_raw", "jersey_raw", "year_raw", "hometown_raw",
                      "high_school_raw", "previous_school_raw", "height_raw",
                      "position_raw", "url_raw")), as.character),
      quarantine_reason = coalesce(classification_reason, screen_reason)
    ) |>
    select(any_of(VB_QUARANTINE_SCHEMA))
  vb_atomic_write_csv(quarantined, quar_path)

  vb_atomic_write_csv(vb_row_issues(out$raw), file.path(season_dir, "row_issues.csv"))
  vb_atomic_write_csv(vb_duplicate_review_table(out$raw, corrections$row),
                      file.path(season_dir, "duplicate_review.csv"))
  vb_atomic_write_csv(vb_team_coverage(out$raw, out$clean, refs$teams_canonical),
                      file.path(season_dir, "team_coverage.csv"))
  vb_atomic_write_csv(
    vb_quality_summary(out$raw, out$clean, out$stale,
                       cell_corrections_applied = out$cell_corrections_applied),
    file.path(season_dir, "quality_summary.csv")
  )
  vb_atomic_write_csv(vb_reconciliation_table(out$raw, out$clean),
                      file.path(season_dir, "reconciliation.csv"))
  if (!is.null(out$stale)) {
    vb_atomic_write_csv(out$stale, file.path(season_dir, "stale_roster.csv"))
  }

  stale_n <- if (is.null(out$stale)) 0L else nrow(out$stale)
  message(sprintf(
    "  %-9s raw=%-6d clean=%-6d quarantined=%-4d exact_dups=%-3d reviewed_dups=%-3d excluded=%-3d stale_teams=%d",
    season, out$n_raw, out$n_accepted,
    out$reconciled$quarantined, out$reconciled$exact_duplicate_removals,
    out$reconciled$reviewed_duplicate_removals,
    out$reconciled$reviewed_exclusions, stale_n
  ))
}

# Optional notebook render (params$season per season) after the build output.
if (RENDER) {
  for (season in seasons) {
    rmarkdown::render(
      "analysis/cleaning.qmd",
      params = list(season = season),
      quiet = TRUE,
      knit_root_dir = normalizePath(getwd()),
      output_file = paste0("cleaning_", season, ".html")
    )
    message("  rendered analysis/cleaning_", season, ".html")
  }
}

message("build complete")
