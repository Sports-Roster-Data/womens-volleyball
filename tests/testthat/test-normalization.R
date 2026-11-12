# Unit and regression tests for R/roster_cleaning.R transformations.

idx <- function(v, val) which(v == val)

test_that("season label helper", {
  expect_equal(vb_season_label(2015), "2015-16")
  expect_equal(vb_season_label(2026), "2026-27")
  expect_equal(vb_season_label(2029), "2029-30")
})

test_that("fold keys are ASCII and display keeps diacritics", {
  expect_equal(vb_name_key("José López-Navarro"), "joselopeznavarro")
  expect_equal(vb_school_key("St. Mary's HS"), "stmaryshs")
  expect_equal(vb_url_key("//www.example.com/roster/x/"), "example.com/roster/x")
  expect_equal(vb_name_key("México"), "mexico")
})

test_that("junk strings become missing", {
  expect_true(is.na(vb_as_missing("NA")))
  expect_true(is.na(vb_as_missing("none")))
  expect_equal(vb_as_missing("  Jane  Doe  "), "Jane Doe")
})

test_that("heights parse, screen and status", {
  df <- tibble(height_raw = c("6'0\"", "5' 10''", "5-8", "6′0″", "5'-2\"",
                              "5'10.5\"  ", "6'15\"", "55", "3-4", "Height: 6-1",
                              "", "NA"))
  out <- vb_clean_heights(df)
  expect_equal(out$height_clean, c("6-0", "5-10", "5-8", "6-0", "5-2", NA, NA, NA,
                                   NA, "6-1", NA, NA))
  expect_equal(out$height_clean[8], NA_character_)
  expect_equal(out$height_status[out$height_raw == "5-8"], "parsed")
  expect_equal(out$height_status[out$height_raw == "Height: 6-1"], "parsed")
  expect_equal(out$height_status[out$height_raw == ""], "missing")
  expect_equal(out$height_status[out$height_raw == "NA"], "missing")
  expect_equal(out$height_status[out$height_raw == "5'10.5\"  "], "unparsed")
  expect_equal(out$height_status[out$height_raw == "6'15\""], "unparsed")
  expect_equal(out$height_status[out$height_raw == "3-4"], "implausible")
  expect_equal(out$total_inches[out$height_raw == "5'-2\""], 62)
  expect_equal(out$total_inches[out$height_raw == "Height: 6-1"], 73)
  expect_true(all(out$height_in[!is.na(out$height_in)] %in% 0:11))
  # the 55-84 plausibility screen: 8-12 = 96 inches is filtered out
  df2 <- tibble(height_raw = "8-12")
  # 8-12 fails the inches 0-11 rule; an in-range-foot but out-of-range-inch
  # value is unparsed, and the implausible status is for parsed values that
  # fail the 55-84 screen
  expect_equal(vb_clean_heights(df2)$height_status, "unparsed")
  expect_equal(vb_clean_heights(tibble(height_raw = "3-4"))$height_status, "implausible")
})

test_that("position lookup and fallback cover every vocabulary entry", {
  refs <- vb_test_refs()
  raws <- c("OH", "oh", "Outside", "Outside Hitter", "MB", "middle", "Middle Blocker",
            "MH", "S", "Setter", "SETTER", "L", "Libero", "DS", "Defensive Specialist",
            "OPP", "Opposite", "RS", "Right Side", "PH", "Pin Hitter", "UT", "Utility", "UTL",
            "OH/DS", "L/DS", "S/RS", "MB/OPP", NA, "", "N/A", "Kicker", "Goalkeeper",
            "OH/DS/S", "Primary")
  out <- vb_clean_positions(tibble(position_raw = raws), refs$positions)
  expect_true(all(out$position_status %in% c("parsed", "unparsed", "missing")))
  expect_equal(out$position_clean[[1]], "OH")
  expect_equal(out$position_clean[idx(raws, "Outside Hitter")], "OH")
  expect_equal(out$position_clean[idx(raws, "Middle Blocker")], "MB")
  expect_equal(out$position_clean[idx(raws, "OH/DS")], "OH/DS")
  expect_equal(out$position_clean[idx(raws, "Primary")], NA_character_)
  expect_equal(out$primary_position[idx(raws, "OH/DS")], "OH")
  expect_equal(out$secondary_position[idx(raws, "OH/DS")], "DS")
  # more than two tokens stay auditable, not truncated
  expect_equal(out$position_clean[idx(raws, "OH/DS/S")], "OH/DS/S")
  expect_equal(out$secondary_position[idx(raws, "OH/DS/S")], "DS/S")
  expect_equal(out$position_status[idx(raws, "Kicker")], "unparsed")
})

test_that("classes: lookup, grad-year inference, redshirt flags", {
  refs <- vb_test_refs()
  raws <- c("Freshman", "FR.", "R-So.", "Graduate Student", "GR", "5th", "Sixth Year",
            "2029", "'29", "2028", "2nd year", "", NA, "Redshirt", "Rs.", "N/A")
  out <- vb_clean_classes(tibble(year_raw = raws), refs$classes, "2026-27")
  expect_equal(out$year_clean[which(out$year_raw == "Freshman")], "Freshman")
  expect_equal(out$year_clean[which(out$year_raw == "2029")], "Sophomore") # class of 2029 in 2026-27
  expect_equal(out$year_clean[which(out$year_raw == "2028")], "Junior")
  expect_true(out$redshirt[idx(raws, "R-So.")])
  expect_false(out$redshirt[which(out$year_raw == "Freshman")])
  expect_true(is.na(out$redshirt[which(out$year_raw == "N/A")]))
  # an explicit redshirt marker with no class year is redshirt=TRUE, class
  # missing, and audited as unparsed
  expect_true(out$redshirt[which(out$year_raw == "Redshirt")])
  expect_equal(out$year_clean[which(out$year_raw == "Redshirt")], NA_character_)
  expect_equal(out$year_status[is.na(out$year_raw)], "missing")
  expect_equal(out$year_status[which(out$year_raw == "N/A")], "missing")
})

test_that("hometowns parse states, provinces, countries and regions", {
  refs <- vb_test_refs()
  rows <- tibble(
    hometown_raw = c("Austin, Texas", "Long Beach, Calif.", "Louisville, KY",
                     "Tulsa, Okla.", "Honolulu, Hawai'i", "Tampa, Fla.",
                     "San Juan, PR", "Toronto, ON", "Calgary, Alberta",
                     "Sao Paulo, Brazil", "Rio de Janeiro, Brasil",
                     "Warsaw, Poland", "Belgrade, Serbia", "Rijeka, Croatia",
                     "Ankara, Turkey", "Istanbul, Türkiye", "Melbourne, Australia",
                     "Brisbane, Victoria", "Austin Texas", "DALLAS TEXAS",
                     "Lima", NA, "NA"),
    hometown_source = c(NA))
  rows$hometown_source <- rows$hometown_raw
  out <- vb_clean_hometowns(rows, refs$state, refs$country)
  expect_equal(out$state[1], "TX")
  expect_equal(out$country[1], "USA")
  expect_equal(out$state[4], "OK")
  expect_equal(out$state[7], "PR")
  expect_equal(out$country[7], "USA")
  expect_equal(out$country[8], "CAN")
  expect_equal(out$region[8], "Ontario")
  expect_equal(out$country[9], "CAN")
  expect_equal(out$region[9], "Alberta")
  expect_equal(out$country[10], "BRA")
  expect_equal(out$country[11], "BRA")
  expect_equal(out$country[12], "POL")
  expect_equal(out$country[13], "SRB")
  expect_equal(out$country[14], "HRV")
  expect_equal(out$country[15], "TUR")
  expect_equal(out$country[16], "TUR")
  expect_equal(out$country[17], "AUS")
  expect_equal(out$region[17], "Oceania")
  expect_equal(out$state[20], "TX")
  expect_equal(out$country[20], "USA")
  expect_equal(out$hometown_status[21], "unparsed")
  expect_equal(out$hometown_status[22], "missing")
  expect_equal(out$hometown_status[nrow(out)], "missing")
})
