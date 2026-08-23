"""Offline parser tests.

These run against saved HTML fixtures in tests/fixtures/ so parsers can be
verified without hitting ~920 live athletics sites. The "missing_elements"
fixtures deliberately omit optional elements to prove the defensive guards
hold (a malformed page should yield partial data, never a crash that aborts
the whole team).
"""
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from conftest import FIXTURES
import rosters


def load(*parts):
    return BeautifulSoup((FIXTURES.joinpath(*parts)).read_text(), features="html.parser")


TEAM = {"ncaa_id": 999, "team": "Test University", "url": "https://example.com/sports/wvball"}


def test_parse_roster_basic():
    html = load("parse_roster", "sidearm_basic.html")
    roster = rosters.parse_roster(TEAM, html, "2025-26")

    assert len(roster) == 2
    jane = roster[0]
    assert jane.name == "Jane Smith"
    assert jane.player_id == "1001"
    assert jane.jersey == "5"
    assert jane.height == "6-1"
    assert jane.hometown == "Austin, Texas"
    assert jane.high_school == "Westlake HS"
    assert jane.previous_school == "Austin CC"
    assert jane.position == "OH"
    assert jane.url.endswith("/roster/jane-smith/1001")

    # Second player takes the text-bold fallback for position.
    assert roster[1].position == "Setter"
    assert all(p.name for p in roster)


def test_parse_roster_missing_elements_does_not_crash():
    html = load("parse_roster", "sidearm_missing_elements.html")
    # Must not raise even though anchors/position/jersey are absent.
    roster = rosters.parse_roster(TEAM, html, "2025-26")

    assert len(roster) == 2
    chris = roster[0]
    assert chris.name == "Chris Nolan"  # falls back to <h3>
    assert chris.hometown == "Reno, Nevada"
    assert chris.url == "https://www.example.com"  # no anchor -> empty href

    dana = roster[1]
    assert dana.position == "N/A"  # empty position div


def test_parse_roster_baskbl_basic():
    team = {"ncaa_id": 999, "team": "Test University", "url": "https://example.com/sports/w-baskbl"}
    html = load("parse_roster_baskbl", "table_basic.html")
    roster = rosters.parse_roster_baskbl(team, html, "2025-26")

    assert len(roster) == 2
    jane = roster[0]
    assert jane.name == "Jane Smith"
    assert jane.jersey == "5"
    assert jane.position == "OH"
    assert jane.hometown == "Austin, Texas"
    assert jane.high_school == "Westlake HS"

    # Second row's hometown cell has no "/" -> town kept, high_school is None.
    assert roster[1].hometown == "Denver, Colorado"
    assert roster[1].high_school is None


@pytest.mark.parametrize("town,expected", [
    ("Austin, Texas / Westlake HS", ("Austin, Texas", "Westlake HS")),
    ("Denver, Colorado", ("Denver, Colorado", None)),
    ("", ("", None)),
    (None, ("", None)),
    ("A / B / C", ("A", "B / C")),  # only first slash splits
])
def test_split_town_high_school(town, expected):
    assert rosters.split_town_high_school(town) == expected
