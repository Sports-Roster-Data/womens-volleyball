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


# ----------------------------------------------------------- season URL slugs

def test_season_url_slugs_year_only_derives_academic_slug():
    # Fall-sport seasons span an academic year; a year-only season must also
    # produce the full slug many sites index rosters under.
    assert rosters.season_url_slugs('2025') == ['2025-26', '2025']


def test_season_url_slugs_full_slug_keeps_year_form():
    # Full-slug seasons keep the bare start-year form for the Nuxt
    # /roster/season/<year> sites (Penn State, Nebraska, Wyoming).
    assert rosters.season_url_slugs('2026-27') == ['2026-27', '2026']
    assert rosters.season_url_slugs('2025-26') == ['2025-26', '2025']


def test_js_render_paths_include_academic_slug(monkeypatch):
    # The render sweep must try the derived full-slug paths before falling
    # back to the bare-year and current-roster forms.
    seen = []

    def fake_render(url, wait_ms=5000):
        seen.append(url)
        return None

    monkeypatch.setattr(rosters, 'fetch_url_with_javascript', fake_render)
    monkeypatch.setattr(rosters, 'verify_roster_season',
                        lambda html, season, team: False)
    roster = rosters._scrape_js_render(
        {"ncaa_id": 1, "team": "T", "url": "https://x.com/sports/wvball"}, '2025')
    assert roster == []
    assert "https://x.com/sports/wvball/roster/2025-26" in seen
    assert "https://x.com/sports/wvball/roster/2025-26" in seen[:3]  # tried early
    assert seen[-1].endswith("/roster")  # current-roster fallback stays last


def test_legacy_index_paths_include_academic_slug(monkeypatch):
    captured = {}

    def fake_plain(team, season, urls):
        captured['urls'] = urls
        return []

    monkeypatch.setattr(rosters, '_scrape_plain_urls', fake_plain)
    monkeypatch.setattr(rosters, '_scrape_js_render', lambda team, season: [])
    rosters._scrape_legacy_index(
        {"ncaa_id": 1, "team": "T", "url": "https://x.com/sports/wvball/index"}, '2025')
    urls = captured['urls']
    assert "https://x.com/sports/wvball/2025-26/roster" in urls[:2]
    assert "https://x.com/sports/wvball/roster/2025-26" in urls
    assert urls[-1] == "https://x.com/sports/wvball/roster"


def test_default_falls_through_to_sweep_on_season_mismatch(monkeypatch):
    # A season-verification rejection on the first (year-only) fetch must not
    # end the team as unparsed before the sweep tries the archival URLs.
    monkeypatch.setattr(rosters, 'fetch_roster',
                        lambda url, season: html('2026 Volleyball Roster'))
    calls = []
    monkeypatch.setattr(rosters, '_scrape_js_render',
                        lambda team, season: calls.append('sweep') or [])
    roster = rosters._scrape_default(
        {"ncaa_id": 1, "team": "T", "url": "https://x.com/sports/wvolleyball"}, '2025')
    assert roster == [] and calls == ['sweep']


def test_js_render_plain_fallback_tries_season_slugs(monkeypatch):
    captured = {}

    def fake_render(url, wait_ms=5000):
        return None

    def fake_plain(team, season, urls):
        captured['urls'] = urls
        return []

    monkeypatch.setattr(rosters, 'fetch_url_with_javascript', fake_render)
    monkeypatch.setattr(rosters, '_scrape_plain_urls', fake_plain)
    monkeypatch.setattr(rosters, 'fetch_roster',
                        lambda url, season: html('2026 Volleyball Roster'))
    roster = rosters._scrape_js_render(
        {"ncaa_id": 1, "team": "T", "url": "https://x.com/sports/blank/index"}, '2025')
    assert roster == []
    urls = captured['urls']
    assert "https://x.com/sports/blank/roster/2025-26" in urls
    assert "https://x.com/sports/blank/roster/2025" in urls


def test_prefer_named_roster_swaps_blank_names(monkeypatch):
    # Multi-table pages can leave the wbkb table parser with blank names;
    # the guard must swap in a fully named parse from the layout sweep.
    blank = [rosters.Player(team_id=1, team="T", player_id=None, name="",
                            year="Jr.", hometown=None, high_school=None,
                            previous_school=None, height=None, position=None,
                            jersey=None, url=None, season="2025")]
    named = [rosters.Player(team_id=1, team="T", player_id=None, name="J Smith",
                            year="Jr.", hometown=None, high_school=None,
                            previous_school=None, height=None, position=None,
                            jersey=None, url=None, season="2025")]
    monkeypatch.setattr(rosters, '_best_parse', lambda html, t, s: named)
    html = BeautifulSoup('<table><tr><td>x</td></tr></table>', 'html.parser')
    team = {"ncaa_id": 1, "team": "T", "url": "https://x.com/sports/wvball"}
    assert rosters._prefer_named_roster(blank, html, team, '2025') == named
    # A roster that already has names, or a sweep that stays blank, keeps the
    # original rows.
    assert rosters._prefer_named_roster(named, html, team, '2025') == named
    partially = named + [dict(blank[0].__dict__ | {'name': ''})]
    monkeypatch.setattr(rosters, '_best_parse', lambda html, t, s: [])
    assert rosters._prefer_named_roster(partially, html, team, '2025') == partially


def test_resolved_roster_base_follows_redirect(monkeypatch):
    # Sites that reorganize sport slugs leave stale team URLs; the sweep must
    # also try season paths under the base the /roster redirect resolves to.
    class R:
        url = 'https://auburntigers.com/sports/volleyball/roster'
    monkeypatch.setattr(rosters.requests, 'head', lambda url, **kw: R())
    base = rosters._resolved_roster_base('https://auburntigers.com/sports/womens-volleyball')
    assert base == 'https://auburntigers.com/sports/volleyball'

    class Same:
        url = 'https://x.com/sports/wvball/roster'
    monkeypatch.setattr(rosters.requests, 'head', lambda url, **kw: Same())
    assert rosters._resolved_roster_base('https://x.com/sports/wvball') is None


def test_js_render_sweeps_redirect_resolved_base(monkeypatch):
    seen = []

    def fake_render(url, wait_ms=5000):
        seen.append(url)
        return None

    monkeypatch.setattr(rosters, 'fetch_url_with_javascript', fake_render)
    monkeypatch.setattr(rosters.requests, 'head', lambda url, **kw: type(
        'R', (), {'url': 'https://x.com/sports/volleyball/roster'})())
    monkeypatch.setattr(rosters, '_scrape_plain_urls', lambda t, s, urls: [])
    monkeypatch.setattr(rosters, 'fetch_roster',
                        lambda url, season: html('2026 Volleyball Roster'))
    rosters._scrape_js_render(
        {"ncaa_id": 1, "team": "T", "url": "https://x.com/sports/wvolleyball"}, '2025')
    assert any(u.startswith("https://x.com/sports/volleyball/roster/") for u in seen)
