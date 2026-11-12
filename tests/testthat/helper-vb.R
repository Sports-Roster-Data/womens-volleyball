# Load the cleaning module relative to this test directory.
# testthat resets the wd per test file, so remember the repo root here and
# source the module by absolute path.
repo_root <- if (file.exists("R/roster_cleaning.R")) getwd() else file.path(getwd(), "..", "..")
repo_root <- normalizePath(repo_root)
suppressPackageStartupMessages({
  library(readr)
})
source(file.path(repo_root, "R", "roster_cleaning.R"))

vb_ref_path <- function(...) file.path(repo_root, "data", "reference", ...)

vb_test_refs <- function() {
  list(
    positions = readr::read_csv(vb_ref_path("positions.csv"), show_col_types = FALSE, na = ""),
    classes = readr::read_csv(vb_ref_path("classes.csv"), show_col_types = FALSE, na = ""),
    state = readr::read_csv(vb_ref_path("state_variants.csv"), show_col_types = FALSE, na = ""),
    country = readr::read_csv(vb_ref_path("country_variants.csv"), show_col_types = FALSE, na = "")
  )
}


vb_tmp_sha256 <- function(path) {
  out <- system2("shasum", c("-a", "256", path), stdout = TRUE)[1]
  str_extract(out, "^[0-9a-f]{64}")
}

vb_write_raw_file <- function(rows, path, season_col) {
  lines <- c("ncaa_id,team,player_id,name,year,hometown,high_school,previous_school,height,position,jersey,url,season",
             rows)
  writeLines(lines, path)
}

