# Two-season end-to-end fixture: raw -> baseline -> corrections -> clean.
# Covers known duplicate groups, structural quarantines, cell corrections
# (including a stale one), row corrections, and chronological backfill.

fixture_raw <- function() {
  c(
    # ncaa_id,team,player_id,name,year,hometown,high_school,previous_school,height,position,jersey,url,season
    # exact duplicate pair (all 13 fields identical): second row is removed
    "7,Test U,T101,Jane Doe,Freshman,\"Austin, TX\",Westlake HS,NA,6-0,OH,1,example.com/roster/jane-doe,2015",
    "7,Test U,T101,Jane Doe,Freshman,\"Austin, TX\",Westlake HS,NA,6-0,OH,1,example.com/roster/jane-doe,2015",
    # conflicting duplicate group: same name, different player ids
    "7,Test U,T102,Ann Smith,Sophomore,\"Dallas, TX\",Plano East,NA,5-10,MB,2,example.com/roster/ann-smith-a,2015",
    "7,Test U,T103,Ann Smith,Sophomore,\"Dallas, TX\",Plano West,NA,5-10,MB,3,example.com/roster/ann-smith-b,2015",
    # staff row, numeric name, shifted row
    "7,Test U,,Bob Coach,Head Coach,,,,,,,,2015",
    "7,Test U,T104,42,Freshman,\"Austin, TX\",Westlake HS,NA,6-1,S,5,example.com/roster/number-forty,2015",
    "7,Test U,T105,Kate Lane,Freshman,\"Austin, TX\",Westlake HS,NA,Fr.,OH,11,example.com/roster/kate-lane,2015",
    # plausible player with broken height and unknown position
    "7,Test U,T106,Sue Tall,,,\"Austin HS\",NA,6-9.5,Kicker,22,example.com/roster/sue-tall,2015",
    # returning player with missing high school -> backfilled from 2014-15
    "7,Test U,T101,Jane Doe,Sophomore,\"Austin, TX\",,NA,6-0,OH,1,example.com/roster/jane-doe,2015",
    # player whose previous school is HS-like: reclassified to high school
    "7,Test U,T107,Al Reed,Junior,\"Austin, TX\",,Riverside HS,6-4,L,7,example.com/roster/al-reed,2015"
  )
}

fixture_raw_prev <- function() {
  c(
    "7,Test U,T101,Jane Doe,Freshman,\"Austin, TX\",Westlake HS,NA,6-0,OH,1,example.com/roster/jane-doe,2015"
  )
}

vb_fixture_tables <- function(dir) {
  refs <- list(
    raw_snapshots = tibble(
      season = c("2015-16", "2016-17"),
      snapshot_id = c("2015-16_1", "2016-17_1"),
      raw_path = c(file.path(dir, "raw-2015-16.csv"), file.path(dir, "raw-2016-17.csv")),
      raw_season_value = c("2015", "2015"),
      sha256 = c(vb_tmp_sha256(file.path(dir, "raw-2015-16.csv")),
                 vb_tmp_sha256(file.path(dir, "raw-2016-17.csv"))),
      retrieved_at = c("2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
      active = c(TRUE, TRUE),
      notes = "fixture"
    ),
    teams_canonical = tibble(ncaa_id = 7, team_name = "Test University", team_state = "TX"),
    team_affiliations = tibble(ncaa_id = 7, first_season = "2015-16", last_season = "2060-61",
                               division = "I", conference = "", source = "fixture", note = ""),
    positions = tibble(position_raw = c("OH", "MB", "S", "L", "DS"),
                       position_code = c("OH", "MB", "S", "L", "DS")),
    classes = tibble(year_raw = c("FRESHMAN", "FRESHMAN", "SOPHOMORE", "SOPHOMORE", "JUNIOR", "JUNIOR"),
                     year_clean = c("Freshman", "Freshman", "Sophomore", "Sophomore", "Junior", "Junior"),
                     redshirt = c(FALSE, FALSE, FALSE, FALSE, FALSE, FALSE)),
    state_variants = tibble(
      raw_key = c("TX", "TEXAS"), state_code = c("TX", "TX"),
      state_name = c("Texas", "Texas"), census_region = c("South", "South"),
      is_territory = c(FALSE, FALSE)
    ),
    country_variants = tibble(raw_key = "BRAZIL", iso3 = "BRA",
                              country_name = "Brazil", region = "South America",
                              admin_region = NA_character_),
    school_mappings = tibble(mapping_id = character(0), school_type = character(0),
                             raw_school_key = character(0), canonical_school = character(0),
                             confidence = character(0), source = character(0)),
    source_exclusions = tibble(ncaa_id = character(0), season = character(0),
                               reason = character(0), reviewed_on = character(0),
                               reviewer = character(0), note = character(0)),
    coverage_exceptions = tibble(ncaa_id = character(0), season = character(0),
                                 kind = character(0), approved_by = character(0),
                                 reviewed_on = character(0), note = character(0))
  )
  refs
}

test_that("end-to-end fixture: raw -> corrections -> clean", {
  dir <- tempfile(pattern = "vb-fx-")
  dir.create(dir)
  on.exit(unlink(dir, recursive = TRUE), add = TRUE)

  raw_2016 <- gsub(",2015$", ",2016", fixture_raw())
  vb_write_raw_file(raw_2016, file.path(dir, "raw-2016-17.csv"))
  vb_write_raw_file(fixture_raw_prev(), file.path(dir, "raw-2015-16.csv"))

  refs <- vb_fixture_tables(dir)
  expect_true(all(file.exists(refs$raw_snapshots$raw_path)))

  # 2016-17: no corrections
  corrections_empty <- list(
    cell = tibble(source_row_id = character(0), field = character(0),
                  expected_value = character(0), new_value = character(0),
                  reason = character(0), evidence = character(0),
                  reviewed_on = character(0)),
    row = tibble(source_row_id = character(0), action = character(0),
                 keeper_source_row_id = character(0), evidence = character(0),
                 reason = character(0), reviewed_on = character(0))
  )
  out <- vb_clean_season("2016-17", refs = refs, corrections = corrections_empty,
                         prev_clean = NULL, allow_partial = TRUE)

  raw <- out$raw
  n_raw <- nrow(raw)
  expect_equal(n_raw, length(raw_2016))
  # exact duplicate removed (kept the first); the two distinct Jane rows
  # conflict on their shared profile URL and are quarantined for review
  expect_true(all(raw$classification[raw$source_row_number == 3] ==
                    "removed_exact_duplicate"))
  expect_equal(sum(raw$classification == "removed_exact_duplicate"), 1)
  expect_equal(sum(raw$classification == "quarantined"), 7)
  expect_equal(sum(raw$classification == "accepted"), 2)  # Sue Tall, Al Reed

  # staff/numeric/shifted rows quarantined with reasons
  screen_rows <- raw$classification_reason[raw$source_row_number %in% c(6, 7, 8)]
  expect_equal(sum(str_detect(screen_rows, "staff/title|numeric-only|carries")),
               3, label = "three structural quarantines")

  # conflicting duplicate group (Ann Smith pair ids 102/103) quarantines both
  ann_rows <- raw |> filter(grepl("Ann Smith", name_raw))
  expect_true(all(ann_rows$classification == "quarantined"))
  expect_true(all(str_detect(ann_rows$classification_reason, "unresolved duplicate group")))

  clean <- out$clean
  expect_false(any(grepl("Ann Smith", clean$name)))
  expect_false(any(grepl("Bob Coach", clean$name)))
  expect_false(any(grepl("^42$", clean$name_raw)))

  # plausible player with broken height and unknown position publishes with
  # field warnings (a bad field does not prove the row is not a player)
  sue <- clean[grepl("Sue Tall", clean$name), ]
  expect_equal(nrow(sue), 1)
  expect_equal(sue$height_status, "unparsed")
  expect_equal(sue$position_status, "unparsed")

  # HS-like previous school reclassified out of previous_school
  al <- clean[grepl("Al Reed", clean$name), ]
  expect_equal(al$high_school_clean, "Riverside HS")
  expect_true(is.na(al$previous_school_clean) | al$previous_school_clean == "")

  # reconciliation: raw = clean + quarantine + dup + exclusions
  r <- out$reconciled
  expect_true(r$balanced)
  expect_equal(r$raw_rows, n_raw)
  expect_equal(r$clean_rows + r$quarantined + r$exact_duplicate_removals +
                 r$reviewed_duplicate_removals + r$reviewed_exclusions, n_raw)

  # team metadata join by id within the affiliation range
  expect_true(all(clean$division == "I"))
  expect_true(all(clean$team_name_status == "matched_id_name_differs"))  # Test U vs Test University
})

test_that("reviewed dispositions and backfill in the fixture", {
  dir <- tempfile(pattern = "vb-fx2-")
  dir.create(dir)
  # 2014-15 published clean: Jane Doe has a high school -> 2015-16 backfills it
  prev_clean <- tibble(
    schema_version = "2.0", snapshot_id = "2014-15_1",
    source_file = "fixture", source_row_number = 2L,
    source_row_id = "2014-15_1#00002", season_raw = "2014", season = "2014-15",
    ncaa_id = "7", team = "Test U", team_name_status = "matched", player_id = "T101",
    name_raw = "Jane Doe", jersey_raw = "1", year_raw = "Freshman",
    hometown_raw = "Austin, TX", high_school_raw = "Westlake HS",
    previous_school_raw = "", height_raw = "6-0", position_raw = "OH",
    url_raw = "example.com/roster/jane-doe",
    name = "Jane Doe", name_key = "janedoe", jersey = "1", jersey_status = "parsed",
    profile_url = "example.com/roster/jane-doe", url_status = "absolute",
    height_clean = "6-0", height_ft = 6L, height_in = 0L, total_inches = 72L,
    height_status = "parsed", height_reason = "",
    position_clean = "OH", primary_position = "OH", secondary_position = "",
    position_status = "parsed", position_reason = "",
    year_clean = "Freshman", redshirt = "FALSE", year_status = "parsed",
    year_reason = "", high_school_clean = "Westlake HS", high_school_status = "unmapped",
    high_school_reason = "", high_school_mapping_id = "", high_school_confidence = "",
    high_school_backfill = "FALSE", high_school_backfill_season = "",
    previous_school_clean = "", previous_school_type = "", previous_school_ncaa_id = "",
    previous_school_status = "",
    hometown_clean = "Austin, TX", state = "TX", state_name = "Texas",
    region = "South", country = "USA", hometown_status = "parsed", hometown_reason = "",
    team_state = "TX", division = "I", conference = ""
  )

  raw_rows <- c(
    "7,Test U,T101,Jane Doe,Sophomore,\"Austin, TX\",,NA,6-0,OH,1,example.com/roster/jane-doe,2015",
    # conflicting-but-distinct pair resolved by reviewed keep_distinct
    "7,Test U,T201,Pat Lane,Junior,\"Corpus Christi, TX\",Moody,NA,6-2,RS,8,example.com/roster/pat-a,2015",
    "7,Test U,T202,Pat Lane,Junior,\"Corpus Christi, TX\",Miller,NA,6-2,RS,9,example.com/roster/pat-b,2015"
  )
  vb_write_raw_file("7,Test U,T101,Jane Doe,Sophomore,\"Austin, TX\",,NA,6-0,OH,1,example.com/roster/jane-doe,2016",
                    file.path(dir, "raw-2016-17.csv"))
  vb_write_raw_file(raw_rows, file.path(dir, "raw-2015-16.csv"))
  refs <- vb_fixture_tables(dir)

  corrections <- list(
    cell = tibble(source_row_id = character(0), field = character(0),
                  expected_value = character(0), new_value = character(0),
                  reason = character(0), evidence = character(0),
                  reviewed_on = character(0)),
    row = tibble(
      source_row_id = c("2015-16_1#00003", "2015-16_1#00004"),
      action = c("keep_distinct", "keep_distinct"),
      keeper_source_row_id = c(NA_character_, NA_character_),
      evidence = c("distinct profile ids", "distinct profile ids"),
      reason = c("adjacent profile ids are distinct bios",
                 "adjacent profile ids are distinct bios"),
      reviewed_on = "2026-11-11"
    )
  )

  out <- vb_clean_season("2015-16", refs = refs, corrections = corrections,
                         prev_clean = prev_clean, allow_partial = FALSE)
  clean <- out$clean
  jane <- clean[grepl("Jane Doe", clean$name), ]
  expect_equal(nrow(jane), 1)
  expect_equal(jane$high_school_clean, "Westlake HS")
  expect_equal(jane$high_school_backfill, "TRUE")
  expect_equal(jane$high_school_backfill_season, "2014-15")
  expect_equal(jane$high_school_status, "backfilled")

  keep_rows <- clean[grepl("Pat Lane", clean$name), ]
  expect_equal(nrow(keep_rows), 2)
  expect_true(all(keep_rows$jersey_raw %in% c("8", "9")))

  # the reviewed keep_distinct rows publish; the review_quarantine row is
  # quarantined and its restore twin publishes
  expect_equal(nrow(clean), 3)
})

test_that("corrections: application, stale skipping, and validation", {
  dir <- tempfile(pattern = "vb-fx3-")
  dir.create(dir)
  on.exit(unlink(dir, recursive = TRUE), add = TRUE)
  vb_write_raw_file(gsub(",2015$", ",2016", fixture_raw()), file.path(dir, "raw-2016-17.csv"))
  refs <- vb_fixture_tables(dir)

  cell <- tibble(
    source_row_id = c("2016-17_1#00002", "2016-17_1#00004"),
    field = c("height", "jersey"),
    expected_value = c("6-0", "99"),
    new_value = c("6-1", "12"),
    reason = c("typo on the roster page", "recovered from a team photo"),
    evidence = c("page screenshot", "page screenshot"),
    reviewed_on = "2026-11-11"
  )
  corrections <- list(
    cell = cell,
    row = tibble(source_row_id = character(0), action = character(0),
                 keeper_source_row_id = character(0), evidence = character(0),
                 reason = character(0), reviewed_on = character(0))
  )
  out <- vb_clean_season("2016-17", refs = refs, corrections = corrections,
                         prev_clean = NULL, allow_partial = TRUE)
  raw <- out$raw
  # first correction applied (expected value matched)
  expect_equal(raw$height_raw[which(raw$source_row_number == 2)], "6-1")
  # stale correction (expected value not present) is skipped and reported
  expect_true(any(str_detect(raw$corrections_stale_note, "jersey correction is stale")))
})

test_that("raw snapshot validation fails loudly", {
  dir <- tempfile(pattern = "vb-fx4-")
  dir.create(dir)
  on.exit(unlink(dir, recursive = TRUE), add = TRUE)
  rows <- gsub(",2015$", ",2016", fixture_raw())
  vb_write_raw_file(rows, file.path(dir, "raw-2016-17.csv"))
  refs <- vb_fixture_tables(dir)

  # tamper with the snapshot after registration -> hash mismatch
  writeLines(c(readLines(file.path(dir, "raw-2016-17.csv")), ""), file.path(dir, "raw-2016-17.csv"))
  expect_error(vb_clean_season("2016-17", refs = refs, allow_partial = TRUE),
               "snapshot hash mismatch")

  # mixed season values in one snapshot
  dir2 <- tempfile(pattern = "vb-fx5-")
  dir.create(dir2)
  on.exit(unlink(dir2, recursive = TRUE), add = TRUE)
  vb_write_raw_file(fixture_raw(), file.path(dir2, "raw-2016-17.csv"))
  rows_mixed <- fixture_raw()
  rows_mixed[3] <- gsub(",2015$", ",2042", rows_mixed[3])
  vb_write_raw_file(rows_mixed, file.path(dir2, "raw-2016-17.csv"))
  refs_mixed <- vb_fixture_tables(dir2)
  refs_mixed$raw_snapshots$sha256[1] <- vb_tmp_sha256(file.path(dir2, "raw-2016-17.csv"))
  refs_mixed$raw_snapshots$sha256[2] <- vb_tmp_sha256(file.path(dir2, "raw-2016-17.csv"))
  expect_error(vb_clean_season("2016-17", refs = refs_mixed, allow_partial = TRUE),
               "season value")

  # wrong schema
  dir3 <- tempfile(pattern = "vb-fx6-")
  dir.create(dir3)
  on.exit(unlink(dir3, recursive = TRUE), add = TRUE)
  writeLines(c("ncaa_id,team,name,season", "7,Test U,X,2016"), file.path(dir3, "raw-2016-17.csv"))
  refs_bad <- vb_fixture_tables(dir3)
  refs_bad$raw_snapshots$sha256[1] <- vb_tmp_sha256(file.path(dir3, "raw-2016-17.csv"))
  expect_error(vb_clean_season("2016-17", refs = refs_bad, allow_partial = TRUE),
               "schema mismatch")
})
