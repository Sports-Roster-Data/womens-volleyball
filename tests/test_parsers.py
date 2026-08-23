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


def test_parse_roster_name_div_variant():
    # Newer Sidearm layout: name only in .sidearm-roster-player-name, with a
    # jersey-number prefix that must be stripped (Portland State, Yale).
    html = load("parse_roster", "sidearm_name_div.html")
    roster = rosters.parse_roster(TEAM, html, "2025-26")

    assert len(roster) == 1
    p = roster[0]
    assert p.name == "Kalyssa Taggart"      # "1 Kalyssa Taggart" -> name only
    assert p.jersey == "1"
    assert p.hometown == "Tampa, Fla."
    assert p.high_school == "Berkeley Prep"


def test_parse_roster_skips_nameless_card():
    html = load("parse_roster", "sidearm_nameless_card.html")
    # A card with no aria-label and no <h3> must be skipped, not raise and
    # abort the whole team (regression: Yale, ncaa_id 813).
    roster = rosters.parse_roster(TEAM, html, "2025-26")

    assert len(roster) == 1
    assert roster[0].name == "Real Player"


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


def test_parse_iowa_state_cards():
    team = {"ncaa_id": 311, "team": "Iowa State", "url": "https://cyclones.com/sports/womens-volleyball"}
    html = load("parse_iowa_state", "cyclones.html")
    roster = rosters.parse_iowa_state_cards(html, team, "2025-26")

    # 50 rendered cards (card + list view, plus staff) -> 19 unique players.
    assert len(roster) == 19
    assert all(p.name for p in roster)          # no coach/empty cards leak in
    assert all(p.jersey for p in roster)        # every player has a jersey stamp
    assert len({p.url for p in roster}) == 19   # de-duplicated by URL

    pam = next(p for p in roster if p.name == "Pam McCune")
    assert pam.position == "MB"
    assert pam.year == "Sr."
    assert pam.jersey == "1"
    assert pam.hometown == "Upper Marlboro, Md."
    assert pam.high_school == "St. John's"


def test_parse_clemson_cards():
    team = {"ncaa_id": 147, "team": "Clemson", "url": "https://clemsontigers.com/sports/volleyball/"}
    html = load("parse_clemson", "clemson.html")
    roster = rosters.parse_clemson_cards(html, team, "2025-26")

    assert len(roster) == 16
    assert all(p.name for p in roster)
    assert all(p.jersey for p in roster)

    ozoh = next(p for p in roster if p.name == "Ihuoma Ozoh")
    assert ozoh.jersey == "1"                    # "#1" -> "1"
    assert ozoh.position == "Middle Hitter"
    assert ozoh.year == "Freshman"
    assert ozoh.height == "6′3″"
    assert ozoh.hometown == "Haze Crest, Ill."
    assert ozoh.high_school == "Homewood-Flossmoor HS"


def test_parse_nebraska_table():
    team = {"ncaa_id": 463, "team": "Nebraska", "url": "https://huskers.com/sports/volleyball"}
    html = load("parse_nebraska", "huskers.html")
    roster = rosters.parse_nebraska_table(html, team, "2025-26")

    assert len(roster) == 16
    assert all(p.name and p.jersey for p in roster)

    reilly = next(p for p in roster if p.name == "Bergen Reilly")
    assert reilly.jersey == "2"
    assert reilly.position == "Setter"
    assert reilly.year == "Senior"
    assert reilly.height == "6-1"
    assert reilly.hometown == "Sioux Falls, S.D."
    assert reilly.high_school == "O'Gorman"


@pytest.mark.parametrize("town,expected", [
    ("Austin, Texas / Westlake HS", ("Austin, Texas", "Westlake HS")),
    ("Denver, Colorado", ("Denver, Colorado", None)),
    ("", ("", None)),
    (None, ("", None)),
    ("A / B / C", ("A", "B / C")),  # only first slash splits
])
def test_split_town_high_school(town, expected):
    assert rosters.split_town_high_school(town) == expected
