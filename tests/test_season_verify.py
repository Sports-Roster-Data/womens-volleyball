"""Tests for the season verifier, school-field hygiene, and resume cache.

Ported behaviors from the field-hockey scraper: pages that positively identify
another season are rejected (a current-roster page still stamped with last
season's year must not be scraped under this season's label), high-school-like
values are moved out of previous_school, and per-team results are cached so a
run can resume.
"""
import json
from pathlib import Path

from bs4 import BeautifulSoup

import rosters


def html(title: str = '', headings: list = None, body: str = '<h1>filler</h1>') -> BeautifulSoup:
    parts = []
    if title:
        parts.append(f'<html><head><title>{title}</title></head>'
                     f'<body>{"".join(f"<h2>{h}</h2>" for h in (headings or []))}</body></html>')
    else:
        parts.append(f'<html><body>{"".join(f"<h2>{h}</h2>" for h in (headings or []))}{body}</body></html>')
    return BeautifulSoup(''.join(parts), 'html.parser')


TEAM = {"ncaa_id": 999, "team": "Test University", "url": "https://example.com/sports/wvball"}


# ---------------------------------------------------------------------------
# SeasonVerifier
# ---------------------------------------------------------------------------

def test_classify_matched_by_range_in_title():
    assert rosters.SeasonVerifier.classify(
        html(title="2025-26 Women's Volleyball Roster"), "2025-26") == "matched"


def test_classify_matched_by_four_digit_range():
    assert rosters.SeasonVerifier.classify(
        html(title="Volleyball Roster 2025-2026"), "2025-26") == "matched"


def test_classify_matched_by_standalone_start_year():
    assert rosters.SeasonVerifier.classify(
        html(title="Roster", headings=["2025 Volleyball Roster"]), "2025-26") == "matched"


def test_classify_matched_overrides_stray_heading_years():
    # Title match wins over a later heading carrying a different year.
    assert rosters.SeasonVerifier.classify(
        html(title="2025 Volleyball Roster", headings=["Archives from 2024"]),
        "2025-26") == "matched"


def test_classify_rejects_second_year_only_evidence():
    # The trailing "26" in "2025-26" must never satisfy a different request.
    assert rosters.SeasonVerifier.classify(
        html(title="2025-26 Women's Volleyball Roster"), "2026-27") in ("mismatch", "unknown")


def test_classify_mismatch_on_stale_current_roster_page():
    assert rosters.SeasonVerifier.classify(
        html(title="2025 Volleyball Roster"), "2026-27") == "mismatch"


def test_classify_mismatch_on_wrong_range():
    assert rosters.SeasonVerifier.classify(
        html(title="Volleyball", headings=["2024-25 Roster"]), "2026-27") == "mismatch"


def test_classify_unknown_without_year_evidence():
    assert rosters.SeasonVerifier.classify(
        html(title="Women's Volleyball - University"), "2025-26") == "unknown"


def test_classify_unknown_without_headings():
    page = BeautifulSoup('<html><body><p>no headings here</p></body></html>', 'html.parser')
    assert rosters.SeasonVerifier.classify(page, "2025-26") == "unknown"


def test_verify_roster_season_rejects_only_mismatch():
    assert not rosters.verify_roster_season(html(title="2024 Volleyball Roster"), "2026-27", TEAM)
    assert rosters.verify_roster_season(html(title="2026 Volleyball Roster"), "2026-27", TEAM)
    # No year evidence at all -> accept (unlike the field-hockey scraper).
    assert rosters.verify_roster_season(html(title="Women's Volleyball"), "2026-27", TEAM)


# ---------------------------------------------------------------------------
# School-field hygiene
# ---------------------------------------------------------------------------

def test_reclassify_moves_explicit_high_school_from_previous():
    row = {'high_school': '', 'previous_school': 'Twin Valley HS'}
    assert rosters.reclassify_high_school_from_previous(row) is True
    assert row['high_school'] == 'Twin Valley HS'
    assert row['previous_school'] == ''


def test_reclassify_splits_slash_combined_value():
    row = {'high_school': '', 'previous_school': 'Twin Valley HS / Liberty'}
    rosters.reclassify_high_school_from_previous(row)
    assert row['high_school'] == 'Twin Valley HS'
    assert row['previous_school'] == 'Liberty'


def test_reclassify_leaves_colleges_alone():
    row = {'high_school': '', 'previous_school': 'Ohio State'}
    assert rosters.reclassify_high_school_from_previous(row) is False
    assert row['previous_school'] == 'Ohio State'
    assert row['high_school'] == ''


def test_reclassify_skips_when_high_school_present():
    row = {'high_school': 'Westlake HS', 'previous_school': 'Bishop Gorman HS'}
    rosters.reclassify_high_school_from_previous(row)
    assert row['previous_school'] == 'Bishop Gorman HS'


def test_dedupe_clears_previous_school_duplicate():
    row = {'high_school': 'Westlake HS', 'previous_school': 'Westlake  HS'}
    assert rosters.dedupe_school_fields(row) is True
    assert row['previous_school'] == ''
    assert row['high_school'] == 'Westlake HS'


def test_dedupe_keeps_distinct_schools():
    row = {'high_school': 'Westlake HS', 'previous_school': 'Austin CC'}
    assert rosters.dedupe_school_fields(row) is False


def test_player_to_dict_applies_hygiene():
    player = rosters.Player(
        team_id=999, team="Test University", season="2026-27",
        name="Jane Smith", previous_school="Bishop Gorman High School")
    d = player.to_dict()
    assert d['high_school'] == 'Bishop Gorman High School'
    assert d['previous_school'] == ''


def test_player_to_row_applies_hygiene_to_dict_players():
    # JS-scraper dicts go through the same hygiene as Player objects.
    row = rosters.player_to_row({
        'team_id': 999, 'team': 'Test University', 'id': '',
        'name': 'Jane Smith', 'year': 'Sr.', 'hometown': 'Austin, Texas',
        'high_school': 'Westlake HS', 'previous_school': 'Westlake  HS',
        'height': "6'1", 'position': 'OH', 'jersey': '5',
        'url': 'https://example.com/jane', 'season': '2026-27',
    }, '2026-27')
    assert row[6] == 'Westlake HS'        # high_school
    assert row[7] == ''                   # previous_school deduped
    assert row[5] == 'Austin, Texas'      # whitespace cleaned


# ---------------------------------------------------------------------------
# Resume cache
# ---------------------------------------------------------------------------

def test_cache_round_trip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert rosters._read_cached_rows("2026-27", 999) is None
    rosters._write_cached_rows("2026-27", 999, [[999, "Test University", "jane"]])
    assert rosters._read_cached_rows("2026-27", 999) == [[999, "Test University", "jane"]]
    assert rosters._read_cached_rows("2025-26", 999) is None


def test_cache_corrupt_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rosters._write_cached_rows("2026-27", 999, [[999, "T", "x"]])
    cache: Path = tmp_path / "data" / "cache" / "rosters_2026-27" / "999.json"
    cache.write_text("{not json")
    assert rosters._read_cached_rows("2026-27", 999) is None
