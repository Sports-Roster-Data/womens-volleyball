"""Tests for the atlas: pure helpers on synthetic data, plus checks on the
built tables when they exist (skipped otherwise)."""
from __future__ import annotations

import pandas as pd
import pytest

from atlas import link
from atlas.campus import similarity, tokens
from atlas.geocode import norm, query_variants, reparse, strip_lsad
from atlas.geography import haversine, summarize
from atlas.load import OUT, write_csv


# ---- geography helpers ----------------------------------------------------

def test_haversine_known_distance():
    # Kansas City, MO -> Abilene, TX is roughly 600 miles
    d = haversine(39.0997, -94.5786, 32.4487, -99.7331)
    assert 540 < d < 620


def test_norm_and_lsad():
    assert norm("St. Louis") == "saint louis"
    assert norm("Ft Worth") == "fort worth"
    assert strip_lsad("Cranberry township") == "cranberry"
    assert strip_lsad("Abbeville city") == "abbeville"
    assert query_variants("Cranberry Township") == ["cranberry township", "cranberry"]
    assert query_variants("HOMETOWN Belgrade")[0] == "belgrade"


def test_reparse_recognizes_state_and_country():
    states = {"AZ": "AZ", "KENTUCKY": "KY"}
    countries = {"BULGARIA": "BGR"}
    assert reparse("Phoenix, AZ", states, countries) == ("AZ", "USA")
    assert reparse("Louisville, Kentucky", states, countries) == ("KY", "USA")
    assert reparse("Sofia, Bulgaria", states, countries) == ("", "BGR")
    assert reparse("San Diego", states, countries) == ("", "")


def test_summarize_shares_and_trusted_distances():
    geo = pd.DataFrame({
        "division": ["I"] * 4,
        "season": ["2025-26"] * 4,
        "geo_precision": ["place", "place", "country", "unmatched"],
        "distance_miles": [10.0, 30.0, 5000.0, None],
        "in_state": [True, False, pd.NA, pd.NA],
        "international": [False, False, True, pd.NA],
        "home_state": ["TX", "OK", "", ""],
        "home_country": ["USA", "USA", "TUR", ""],
    })
    s = summarize(geo, ["division", "season"]).iloc[0]
    assert s.players == 4
    assert s.players_with_distance == 2          # country centroid excluded
    assert s.median_miles == 20.0
    assert s.in_state_share == 0.5
    assert round(s.international_share, 3) == 0.333


def test_campus_similarity_prefers_exact_institution():
    assert tokens("Wis.-Oshkosh") == ["wisconsin", "oshkosh"]
    assert similarity("Drake", "Drake University") > similarity("Drake", "Drake State Community College")


# ---- linking helpers --------------------------------------------------------

def test_norm_url_requires_bio_id():
    assert link.norm_url("//www.x.com/roster/jane-doe/123") == "x.com/roster/jane-doe/123"
    assert link.norm_url("/roster/jane-doe") == ""


def test_hs_and_home_normalization():
    assert link.norm_hs("Westfield (Loyola Maryland)") == link.norm_hs("Westfield HS")
    assert link.norm_hs("Reagan HS / Lehigh") == "reagan"
    assert link.hs_match("blue valley west", "blue valley west senior") is True
    assert link.hs_match("allen", "allendale") is False   # partial word
    assert link.norm_home("Fullerton, CA", "CA", "USA") == link.norm_home("Fullerton, Calif", "CA", "USA")


def _rows(specs):
    cols = ["source_row_id", "season", "ncaa_id", "team", "division", "player_id",
            "profile_url", "name_key", "year_clean", "total_inches",
            "high_school_clean", "hometown_clean", "state", "country",
            "previous_school_ncaa_id"]
    base = dict(player_id="", profile_url="", total_inches="70",
                high_school_clean="", hometown_clean="Austin, Texas",
                state="TX", country="USA", previous_school_ncaa_id="",
                division="I")
    out = []
    for i, s in enumerate(specs):
        r = {**base, **s}
        r.setdefault("team", "T" + r["ncaa_id"])
        r["source_row_id"] = f"{r['season']}#{i:05d}"
        out.append(r)
    return pd.DataFrame(out)[cols]


@pytest.fixture
def fake_seasons(monkeypatch):
    ss = ["2015-16", "2016-17", "2017-18", "2018-19", "2019-20"]
    monkeypatch.setattr(link, "all_seasons", lambda: ss)
    return ss


def test_within_team_name_chain_breaks_on_class_reset(fake_seasons):
    r = _rows([
        dict(season="2015-16", ncaa_id="1", name_key="janedoe", year_clean="Junior"),
        dict(season="2016-17", ncaa_id="1", name_key="janedoe", year_clean="Senior"),
        dict(season="2017-18", ncaa_id="1", name_key="janedoe", year_clean="Freshman"),
    ])
    st = link.build_stints(r)
    assert st.iloc[0] == st.iloc[1]
    assert st.iloc[2] != st.iloc[1]


def test_same_name_twice_in_a_season_is_not_merged_by_name(fake_seasons):
    r = _rows([
        dict(season="2015-16", ncaa_id="1", name_key="janedoe", year_clean="Freshman"),
        dict(season="2015-16", ncaa_id="1", name_key="janedoe", year_clean="Senior"),
        dict(season="2016-17", ncaa_id="1", name_key="janedoe", year_clean="Sophomore"),
    ])
    st = link.build_stints(r)
    assert st.nunique() == 3


def test_cross_team_tiers(fake_seasons):
    r = _rows([
        # confirmed: roster names the previous school
        dict(season="2015-16", ncaa_id="1", name_key="anna", year_clean="Freshman"),
        dict(season="2016-17", ncaa_id="2", name_key="anna", year_clean="Sophomore",
             previous_school_ncaa_id="1", hometown_clean="Dallas, Texas"),
        # probable: same hometown, class moves forward, height close
        dict(season="2015-16", ncaa_id="1", name_key="bea", year_clean="Freshman"),
        dict(season="2017-18", ncaa_id="3", name_key="bea", year_clean="Junior", total_inches="71"),
        # rejected: class goes backward and different hometown -> review only
        dict(season="2015-16", ncaa_id="1", name_key="cy", year_clean="Senior"),
        dict(season="2016-17", ncaa_id="4", name_key="cy", year_clean="Freshman",
             hometown_clean="Tulsa, Okla", state="OK"),
    ])
    links, pairs, review = link.link_players(r)
    person = dict(zip(links.source_row_id, links.person_id))
    ids = list(r.source_row_id)
    assert person[ids[0]] == person[ids[1]]
    assert person[ids[2]] == person[ids[3]]
    assert person[ids[4]] != person[ids[5]]
    tiers = dict(zip(pairs.evidence, pairs.tier))
    assert tiers["previous_school_ncaa_id"] == "confirmed"
    assert tiers["hometown"] == "probable"
    assert len(review) == 1


def test_ambiguous_candidates_are_not_linked(fake_seasons):
    r = _rows([
        dict(season="2015-16", ncaa_id="1", name_key="dee", year_clean="Freshman"),
        dict(season="2016-17", ncaa_id="2", name_key="dee", year_clean="Sophomore"),
        dict(season="2016-17", ncaa_id="3", name_key="dee", year_clean="Sophomore"),
    ])
    links, pairs, review = link.link_players(r)
    assert links.person_id.nunique() == 3
    assert review.evidence.str.contains("ambiguous").all()


def test_write_csv_is_deterministic(tmp_path):
    df = pd.DataFrame({"a": [1.23456789, 2.0], "b": ["x", "y"]})
    p1, p2 = tmp_path / "1.csv", tmp_path / "2.csv"
    write_csv(df, p1)
    write_csv(df, p2)
    assert p1.read_bytes() == p2.read_bytes()
    assert b"\r\n" not in p1.read_bytes()


# ---- built outputs (run after scripts/build_atlas.py --step all) -----------

built = pytest.mark.skipif(not (OUT / "division_season_geo.csv").exists(),
                           reason="atlas tables not built")


@built
def test_headline_figures_match_profile():
    d = pd.read_csv(OUT / "division_season_geo.csv").set_index(["division", "season"])
    assert abs(d.loc[("I", "2015-16"), "in_state_share"] - 0.355) < 0.01
    assert abs(d.loc[("I", "2026-27"), "in_state_share"] - 0.251) < 0.01
    assert abs(d.loc[("I", "2015-16"), "international_share"] - 0.065) < 0.005
    assert abs(d.loc[("I", "2026-27"), "international_share"] - 0.103) < 0.005


@built
def test_geocode_coverage_target():
    g = pd.read_csv(OUT / "player_seasons_geo.csv", usecols=["home_country", "geo_precision"],
                    keep_default_na=False)
    us = g[g.home_country == "USA"]
    assert us.geo_precision.isin(["place", "fuzzy"]).mean() >= 0.97


@built
def test_abilene_christian_kansas_city_distance():
    from atlas.load import load_rosters
    r = load_rosters(["source_row_id", "ncaa_id", "hometown_clean"])
    rid = r[(r.ncaa_id == "2") & r.hometown_clean.str.startswith("Kansas City, Mo")].source_row_id
    g = pd.read_csv(OUT / "player_seasons_geo.csv", usecols=["source_row_id", "distance_miles"],
                    dtype={"source_row_id": str})
    d = g[g.source_row_id.isin(set(rid))].distance_miles
    assert len(d) and d.between(500, 650).all()


@built
def test_no_person_holds_two_rows_in_one_season():
    l = pd.read_csv(OUT / "player_links.csv", usecols=["person_id", "season"], dtype=str)
    assert not l.duplicated().any()
