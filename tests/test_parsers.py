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


def test_parse_roster_table_body_row():
    # Notre Dame-style Nuxt roster-table layout: hidden bio rows and the
    # jersey-less staff row must be skipped; cells map in fixed order.
    team = {"ncaa_id": 513, "team": "Notre Dame", "url": "https://und.com/sports/wvball"}
    html = load("parse_roster_table_body_row", "notre_dame_table.html")
    roster = rosters.parse_roster_table_body_row(html, team, "2026-27")

    assert len(roster) == 2
    assert all(p.name for p in roster)

    hailie = roster[0]
    assert hailie.jersey == "1"
    assert hailie.position == "Outside Hitter"
    assert hailie.height == "6-0"
    assert hailie.year == "Freshman"
    assert hailie.hometown == "Overland Park, KS"
    assert hailie.high_school == "Blue Valley Southwest"
    assert hailie.url == "https://www.und.com/sports/wvball/roster/player/hailie-vaughn"
    assert hailie.player_id == "hailie-vaughn"


def test_parse_sidearm_list_item():
    # Sidearm .sidearm-roster-list-item layout, server-rendered (Bradley, …).
    team = {"ncaa_id": 72, "team": "Bradley", "url": "https://bradleybraves.com/sports/wvball"}
    html = load("parse_sidearm_list_item", "bradley.html")
    roster = rosters.parse_sidearm_list_item(html, team, "2026-27")

    assert len(roster) == 2
    hayden = roster[0]
    assert hayden.name == "Hayden Booth"
    assert hayden.jersey == "1"
    assert hayden.position == "S"
    assert hayden.height == "5' 9\""
    assert hayden.year == "Fr."
    assert hayden.hometown == "Elkhorn, Nebraska"
    assert hayden.high_school == "Elkhorn North High School"
    assert hayden.previous_school == ""
    assert hayden.url.endswith("/roster/hayden-booth/25813")

    gracie = roster[1]
    assert gracie.previous_school == "Michigan"


def test_parse_rosters_list_item():
    # Nuxt .rosters-list-item layout (Washington State, …). Coach card has no
    # fields group and is dropped.
    team = {"ncaa_id": 754, "team": "Washington State", "url": "https://wsucougars.com/sports/womens-volleyball"}
    html = load("parse_rosters_list_item", "wsu.html")
    roster = rosters.parse_rosters_list_item(html, team, "2026-27")

    assert len(roster) == 2
    naomi = roster[0]
    assert naomi.jersey == "1"
    assert naomi.position == "Middle Blocker"
    assert naomi.height == "6′0″"
    assert naomi.year == "Senior"
    assert naomi.hometown == "Las Vegas, Nev."
    assert naomi.high_school == "Palo Verde HS"
    assert naomi.previous_school == "Portland State"
    assert naomi.url == "https://www.wsucougars.com/sports/womens-volleyball/roster/player/naomi-white"

    eliana = roster[1]
    assert eliana.hometown == "Meridian, Idaho"
    assert eliana.high_school == "Centennial HS"
    assert eliana.previous_school == ""


def test_parse_roster_wbkb_span_wrapped_name_stays_aligned():
    # Presto-style tables wrap the jersey and name in <span> inside <td>, and
    # some rows have a genuinely empty cell. The parser must keep the wrapped
    # text and preserve cell alignment instead of shifting every field left.
    team = {"ncaa_id": 1050, "team": "Defiance", "url": "https://www.defianceathletics.com/sports/wvball/index"}
    html = load("parse_roster_span_name", "defiance_style.html")
    roster = rosters.parse_roster_wbkb(team, html, "2026-27")

    # Coach/staff cards are kept, as elsewhere in the pipeline.
    assert len(roster) == 3
    assert roster[2].position == "Head Coach"
    jane = roster[0]
    assert jane.name == "Jane Smith"
    assert jane.jersey == "1"
    assert jane.position == "DS"
    assert jane.height == "5-6"
    assert jane.year == "Fr."
    assert jane.hometown == "Huber Heights, Ohio"
    assert jane.high_school == "Huber Heights HS"

    alex = roster[1]
    assert alex.name == "Alex Doe"
    assert alex.previous_school == "Ohio Northern"
    # Combined town/high-school cell is split, and the explicit previous
    # school column is distinct from the high school.
    assert alex.hometown == "Alvordton, Ohio"
    assert alex.high_school == "Hilltop HS"


def test_parse_roster_wbkb_major_column_aligned():
    # MCLA-style table: an unwanted 'Major' column stays in the cells, and the
    # name cell duplicates 'First Last' (hidden + visible copies). The aligned
    # path must drop the Major cell, collapse the name, and keep fields clean.
    team = {"ncaa_id": 486, "team": "MCLA", "url": "https://athletics.mcla.edu/sports/wvball/index"}
    html = load("parse_roster_span_name", "mcla_major_column.html")
    roster = rosters.parse_roster_wbkb(team, html, "2026-27")

    assert len(roster) == 3
    assert roster[0].name == "Heidymar Diaz-Lopez"
    assert roster[0].jersey == "2"
    assert roster[0].position == "DS"
    assert roster[0].height == "5'-2\""
    assert roster[0].year == "First Year"
    assert roster[0].hometown == "Northampton, MA"
    assert roster[0].high_school == "Smith Vocational High School"
    # The Major cell text must not leak into any field.
    assert all("Radiologic" not in (p.hometown or "") + (p.high_school or "")
               + (p.year or "") + (p.position or "") for p in roster)


def test_parse_roster_wbkb_responsive_duplicate_jersey_cell():
    # Sidearm responsive table (Simpson 2026-27): each row repeats the jersey
    # column in a mobile-only cell (d-md-none) beside the desktop twin
    # (d-none d-md-table-cell), and the name lives in a <th>. The duplicate
    # cell must be dropped so header alignment holds; before the fix the
    # jersey number shifted into the name column and every row was
    # quarantined as "numeric-only name".
    team = {"ncaa_id": 641, "team": "Simpson", "url": "https://www.simpsonathletics.com/sports/wvball/index"}
    html = load("parse_roster_wbkb_responsive", "simpson_style.html")
    roster = rosters.parse_roster_wbkb(team, html, "2026-27")

    assert len(roster) == 2
    hannah = roster[0]
    assert hannah.name == "Hannah Jansen"
    assert hannah.jersey == "1"
    assert hannah.position == "L/OH"
    assert hannah.height == "5-8"
    assert hannah.year == "JR"
    assert hannah.hometown == "Muscatine, Iowa"
    assert hannah.high_school == "Muscatine"
    assert hannah.url.endswith("/bios/jansen_hannah_bekp")

    ava = roster[1]
    assert ava.name == "Ava Martin"
    assert ava.jersey == "12"
    assert ava.position == "MB"
    assert ava.year == "FR"
    # No field may be left holding a bare jersey number.
    assert all(p.name and not p.name.isdigit() for p in roster)


def test_parse_iowa_state_cards_aria_label_name_fallback():
    # Some templates (Colorado St. 2023-24 as archived) drop the name
    # element entirely and carry the name only on the anchors' aria-labels
    # ('Malaya Jones full bio'). Without the fallback every card parses
    # with a blank name.
    team = {"ncaa_id": 156, "team": "Colorado St.", "url": "https://csurams.com/sports/womens-volleyball"}
    html = BeautifulSoup("""
    <div class="s-person-card__content">
      <a aria-label="Malaya Jones full bio" href="/roster/malaya-jones/12345">
        <span class="s-stamp__text">1</span>
      </a>
      <div class="s-person-details__bio-stats-item">Position MB</div>
      <div class="s-person-details__bio-stats-item">Academic Year R-Jr.</div>
      <div class="s-person-details__bio-stats-item">Hgt. 6' 0''</div>
    </div>
    """, features="html.parser")
    roster = rosters.parse_iowa_state_cards(html, team, "2023-24")
    assert len(roster) == 1
    assert roster[0].name == "Malaya Jones"
    assert roster[0].year == "R-Jr."
    assert roster[0].position == "MB"
    assert roster[0].jersey == "1"


def test_parse_roster_list_item():
    # Nuxt roster-list-item layout: Iowa, Notre Dame, Missouri, Northwestern, …
    team = {"ncaa_id": 312, "team": "Iowa", "url": "https://hawkeyesports.com/sports/wvball"}
    html = load("parse_iowa_roster_list", "roster_list_item.html")
    roster = rosters.parse_roster_list_item(html, team, "2026-27")

    assert len(roster) == 2
    assert all(p.name for p in roster)

    berent = next(p for p in roster if p.name == "Allison Berent")
    assert berent.jersey == "0"
    assert berent.position == "L"
    assert berent.year == "4th"
    assert berent.hometown == "Rochester Hills, Mich."
    assert berent.high_school == "Notre Dame Prep"
    assert berent.previous_school == "Alabama"
    assert berent.url.endswith("/sports/wvball/roster/player/allison-berent")

    young = next(p for p in roster if p.name == "Peyton Young")
    assert young.jersey == "1"
    assert young.position == "MH"
    assert young.high_school == "Hoffman Estates"
    assert young.previous_school == ""


def test_parse_s_table_row():
    # Nuxt s-table-body__row layout: Arizona, Mississippi State, Rutgers, Toledo, …
    team = {"ncaa_id": 29, "team": "Arizona", "url": "https://arizonawildcats.com/sports/womens-volleyball"}
    html = load("parse_s_table_row", "s_table.html")
    roster = rosters.parse_s_table_row(html, team, "2026-27")

    # Staff row (non-numeric jersey) is filtered out
    assert len(roster) == 2
    assert all(p.name and p.jersey for p in roster)

    siapani = next(p for p in roster if p.name == "Maria Olga Siapani")
    assert siapani.jersey == "1"
    assert siapani.position == "S"
    assert siapani.year == "Jr."
    assert siapani.hometown == "Limassol, Cyprus"
    assert siapani.high_school == "Grammar School of Limassol"
    assert siapani.previous_school == "UAlbany"
    assert siapani.url.endswith("/sports/womens-volleyball/roster/maria-olga-siapani/19423")

    ginder = next(p for p in roster if p.name == "Brenna Ginder")
    assert ginder.jersey == "5"
    assert ginder.high_school == "Central High School"
    assert ginder.previous_school == ""


def test_parse_roster_card_item():
    # Nuxt roster-card-item layout: Penn State, …
    team = {"ncaa_id": 531, "team": "Penn State", "url": "https://gopsusports.com/sports/womens-volleyball"}
    html = load("parse_roster_card_item", "roster_card_item.html")
    roster = rosters.parse_roster_card_item(html, team, "2026-27")

    assert len(roster) == 2
    assert all(p.name for p in roster)

    northern = next(p for p in roster if p.name == "Kendall Northern")
    assert northern.jersey == "1"
    assert northern.position == "MB"
    assert northern.year == "Freshman"
    assert northern.height == "6′1″"
    assert northern.hometown == "Cincinnati, Ohio"
    assert northern.high_school == "Summit Country Day School"
    assert northern.previous_school == ""
    assert "kendall-northern" in northern.url

    falduto = next(p for p in roster if p.name == "Ava Falduto")
    assert falduto.jersey == "2"
    assert falduto.position == "L/DS"
    assert falduto.year == "Junior"
    assert falduto.previous_school == "Ohio State"
    assert "ava-falduto" in falduto.url


@pytest.mark.parametrize("town,expected", [
    ("Austin, Texas / Westlake HS", ("Austin, Texas", "Westlake HS")),
    ("Denver, Colorado", ("Denver, Colorado", None)),
    ("", ("", None)),
    (None, ("", None)),
    ("A / B / C", ("A", "B / C")),  # only first slash splits
])
def test_split_town_high_school(town, expected):
    assert rosters.split_town_high_school(town) == expected
