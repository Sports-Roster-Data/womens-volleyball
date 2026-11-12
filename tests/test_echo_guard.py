"""Tests for the slug-ignoring-echo guard in the season sweep resolver.

Some athletics sites render the current roster for any /roster/<season> URL
(uabsports.com 2026-11: /roster/2024-25 echoed today's team while
/roster/2025 served the real 2025-26 roster). _resolve_echo_candidates must
prefer trustworthy parses over the academic-slug echo that happens to come
first in the sweep.
"""
import pytest
from bs4 import BeautifulSoup

import rosters


TEAM = {"ncaa_id": 641, "team": "Test U", "url": "https://example.com/sports/wvball"}


def _player(name, year, jersey="1", position="OH"):
    return rosters.Player(team_id=TEAM["ncaa_id"], team=TEAM["team"], player_id=None,
                          name=name, year=year, hometown=None, high_school=None,
                          previous_school=None, height="5-9", position=position,
                          jersey=jersey, url=f"/roster/{name.lower().replace(' ', '-')}",
                          season="2025-26")


def _html(season_title=""):
    title = f"<title>{season_title}</title>" if season_title else "<title>Roster</title>"
    return BeautifulSoup(f"<html><head>{title}</head><body>roster</body></html>",
                         features="html.parser")


def _mk(path, html, roster):
    return (path, html, roster)


def test_single_candidate_returned_as_is():
    r = [_player("Jane Smith", "Jr.")]
    got = rosters._resolve_echo_candidates(TEAM, "2024-25", [_mk("/roster/2024-25", _html(), r)], None)
    assert got is r


def test_identical_candidates_collapse():
    r = [_player("Jane Smith", "Jr.")]
    cands = [_mk("/roster/2024-25", _html(), r), _mk("/roster/2024", _html(), r)]
    got = rosters._resolve_echo_candidates(TEAM, "2024-25", cands, None)
    assert got is r


def test_match_titled_page_wins_over_ambiguous():
    true_roster = [_player("Jane Smith", "Sr.")]
    html = _html("2018-19 Women's Volleyball Roster")
    cands = [_mk("/roster/2024-25", _html(), [_player("Echo Person", "Fr.")]),
             _mk("/roster/2018-19", html, true_roster)]
    # season arg in season_qualified 'matched' detection must be the label
    got = rosters._resolve_echo_candidates(TEAM, "2018-19", cands, None)
    assert got is true_roster


def test_echo_of_bare_current_page_is_rejected():
    """Academic slug echoes the current roster; year-only slug has the truth."""
    echo = [_player("Abby Jones", "Jr."), _player("Annastiina Tarkkanen", "Jr.")]
    real = [_player("Abby Jones", "So."), _player("Annastiina Tarkkanen", "So.")]
    cands = [_mk("/roster/2024-25", _html(), echo),
             _mk("/roster/2024", _html(), real)]
    calls = []
    def baseline(team_url, season):
        calls.append(team_url)
        return echo
    got = rosters._resolve_echo_candidates(TEAM, "2024-25", cands, baseline)
    assert got is real
    assert calls  # baseline render actually consulted


def test_all_candidates_match_baseline_keeps_season_qualified_first():
    echo = [_player("Abby Jones", "Jr.")]
    real = [_player("Abby Jones", "So."), _player("Zoe Other", "Fr.")]
    cands = [_mk("/roster/2024-25", _html(), echo),
             _mk("/roster/2024", _html(), real)]
    def baseline(team_url, season):
        return real  # everything differs from baseline here...
    got = rosters._resolve_echo_candidates(TEAM, "2024-25", cands, baseline)
    # baseline == 'real' so echo candidate does not match baseline: both survive;
    # order preference: the season-qualified list order keeps /roster/2024-25 first.
    assert got is echo


def test_season_qualified_wins_over_bare_roster_when_disagreeing():
    bare = [_player("Current Person", "Fr.")]
    season_page = [_player("Past Person", "Sr.")]
    cands = [_mk("/roster/2024", _html(), season_page),
             _mk("/roster", _html(), bare)]
    def baseline(team_url, season):
        return bare
    got = rosters._resolve_echo_candidates(TEAM, "2024-25", cands, baseline)
    assert got is season_page


def test_js_and_plain_sweep_wiring_present():
    # The sweeps must route through the resolver (regression guard against
    # reverting to first-parse-wins).
    import inspect
    assert "_resolve_echo_candidates" in inspect.getsource(rosters._scrape_js_render)
    assert "_resolve_echo_candidates" in inspect.getsource(rosters._scrape_plain_urls)
