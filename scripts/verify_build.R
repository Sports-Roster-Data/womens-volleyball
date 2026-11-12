# Final verification against the plan's completion criteria.
# Run after scripts/build_rosters.R --all:
#   Rscript scripts/verify_build.R

suppressPackageStartupMessages({
  library(tidyverse)
  library(jsonlite)
})
options(width = 200)

cat("=== 1. Tests ===\n")
cat("  R unit/integration tests: testthat::test_dir (run separately)\n")
cat("  Python tests: uv run pytest (run separately)\n")

cat("\n=== 2. Raw snapshot hashes ===\n")
snaps <- read_csv("data/reference/raw_snapshots.csv", show_col_types = FALSE)
ok <- map_lgl(seq_len(nrow(snaps)), function(i) {
  actual <- str_extract(
    system2("shasum", c("-a", "256", snaps$raw_path[i]), stdout = TRUE)[1],
    "^[0-9a-f]{64}")
  actual == snaps$sha256[i]
})
cat("  active snapshots with matching hashes:", sum(ok[snaps$active]), "of", sum(snaps$active), "\n")
stopifnot(all(ok[snaps$active]))

cat("\n=== 3. Reconciliation across all seasons ===\n")
seasons <- pull(snaps[snaps$active, ], season)
total_raw <- 0L
total_clean <- 0L
total_quar <- 0L
total_removed <- 0L
for (s in seasons) {
  rec <- read_csv(file.path("data", "derived", "audits", s, "reconciliation.csv"),
                  show_col_types = FALSE)
  total_raw <- total_raw + sum(rec$rows)
  total_clean <- total_clean + rec$rows[rec$classification == "accepted"]
  total_quar <- total_quar + rec$rows[rec$classification == "quarantined"]
  total_removed <- total_removed +
    rec$rows[rec$classification == "removed_exact_duplicate"] +
    rec$rows[rec$classification == "removed_reviewed_duplicate"]
}
cat(sprintf("  raw rows=%d clean=%d quarantined=%d dup-removals=%d\n",
            total_raw, total_clean, total_quar, total_removed))
# raw rows must equal 176,693 (12 files minus headers), team-seasons 10,572
cat("  expected raw rows: 176693\n")

ts_count <- sum(map_int(seasons, function(s) {
  n_distinct(read_csv(file.path("data", "clean", paste0("vb_rosters_", s, ".csv")),
                      show_col_types = FALSE)$ncaa_id) +
    length(unique(read_csv(file.path("data", "derived", "audits", s, "team_coverage.csv"),
                           show_col_types = FALSE)$ncaa_id[
      read_csv(file.path("data", "derived", "audits", s, "team_coverage.csv"),
               show_col_types = FALSE)$rows_raw > 0 &
      read_csv(file.path("data", "derived", "audits", s, "team_coverage.csv"),
               show_col_types = FALSE)$published == 0]))
}))
cat("  raw team-seasons published or acknowledged:", ts_count, "(expected 10,572)\n")

cat("\n=== 4. Controlled vocabularies and duplicate ids ===\n")
POS_CODES <- c("OH", "MB", "S", "L", "DS", "OPP", "RS", "PH", "UT")
CLASSES <- c("Freshman", "Sophomore", "Junior", "Senior", "Graduate Student",
             "Fifth Year", "Sixth Year")
bad_code <- 0L
dup_ids <- 0L
for (s in seasons) {
  clean <- read_csv(file.path("data", "clean", paste0("vb_rosters_", s, ".csv")),
                    show_col_types = FALSE)
  dup_ids <- dup_ids + anyDuplicated(clean$source_row_id)
  pc <- unlist(str_split(str_to_upper(clean$position_clean), "/"))
  bad_code <- bad_code + sum(!pc[!is.na(pc) & pc != ""] %in% POS_CODES)
  bad_code <- bad_code + sum(!clean$year_clean[!is.na(clean$year_clean) &
                                                 clean$year_clean != ""] %in% CLASSES)
}
cat("  duplicate source_row_id across clean files:", dup_ids, "\n")
cat("  out-of-vocabulary position/class codes:", bad_code, "\n")

cat("\n=== 5. Nonempty-raw -> empty-derived with no status ===\n")
JUNK <- c("NA", "N.A.", "N/A", "n/a", "none", "None", "NONE", "null", "NULL",
          "-", "--", "?", "TBA", "TBD")
not_data <- function(x) {
  v <- str_squish(as.character(x))
  is.na(v) | v == "" | v %in% JUNK
}
unexplained <- 0L
for (s in seasons) {
  clean <- read_csv(file.path("data", "clean", paste0("vb_rosters_", s, ".csv")),
                    show_col_types = FALSE)
  unexplained <- unexplained +
    sum(!not_data(clean$height_raw) & is.na(clean$total_inches) &
          !(clean$height_status %in% c("unparsed", "implausible", "missing"))) +
    sum(!not_data(clean$position_raw) & is.na(clean$position_clean) &
          clean$position_status != "unparsed") +
    sum(!not_data(clean$year_raw) & is.na(clean$year_clean) &
          clean$year_status != "unparsed")
}
cat("  unexplained nonempty-raw-to-empty-derived values:", unexplained, "\n")

cat("\n=== 6. 2026-27 quality vs the pre-pipeline baseline (158 heights / 383 positions / 199 classes) ===\n")
q26 <- read_csv(file.path("data", "derived", "audits", "2026-27", "quality_summary.csv"),
                show_col_types = FALSE)
q26 |> print(n = 22)

cat("\n=== 7. Stale-roster flags (published as warnings; removals need review) ===\n")
for (s in seasons) {
  p <- file.path("data", "derived", "audits", s, "stale_roster.csv")
  if (file.exists(p)) {
    st <- read_csv(p, show_col_types = FALSE)
    if (nrow(st)) cat(sprintf("  %s: %s\n", s, paste(st$ncaa_id, collapse = ", ")))
  }
}

cat("\n=== verification complete ===\n")
