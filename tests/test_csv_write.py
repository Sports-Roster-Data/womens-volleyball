"""Tests for the atomic CSV writer and the -teams merge behavior."""
import csv

import rosters


def read_names(path):
    rows = list(csv.reader(open(path)))
    assert rows[0] == rosters.CSV_HEADER
    return [r[3] for r in rows[1:]]


def row(ncaa_id, name):
    return [str(ncaa_id), "Team", "", name, "", "", "", "", "", "", "", "", "2025-26"]


def test_full_run_overwrites(tmp_path):
    path = tmp_path / "rosters_2025-26.csv"
    rosters.write_rosters_csv(str(path), [row(100, "a"), row(100, "b")], replace_ids=None)
    rosters.write_rosters_csv(str(path), [row(200, "c")], replace_ids=None)
    assert read_names(str(path)) == ["c"]


def test_partial_run_replaces_only_scraped_team(tmp_path):
    path = tmp_path / "rosters_2025-26.csv"
    # Seed with two teams.
    rosters.write_rosters_csv(
        str(path), [row(100, "a"), row(100, "b"), row(200, "c")], replace_ids=None)
    # Re-scrape only team 100 -> its rows replaced, team 200 preserved.
    rosters.write_rosters_csv(str(path), [row(100, "a2")], replace_ids={"100"})
    assert read_names(str(path)) == ["c", "a2"]


def test_partial_run_preserves_file_when_nothing_scraped(tmp_path):
    path = tmp_path / "rosters_2025-26.csv"
    rosters.write_rosters_csv(str(path), [row(100, "a"), row(200, "c")], replace_ids=None)
    # A targeted run where the team returned nothing: replace_ids is empty,
    # so existing data is fully preserved.
    rosters.write_rosters_csv(str(path), [], replace_ids=set())
    assert read_names(str(path)) == ["a", "c"]


def test_no_leftover_temp_file(tmp_path):
    path = tmp_path / "rosters_2025-26.csv"
    rosters.write_rosters_csv(str(path), [row(100, "a")], replace_ids=None)
    assert not (tmp_path / "rosters_2025-26.csv.tmp").exists()
