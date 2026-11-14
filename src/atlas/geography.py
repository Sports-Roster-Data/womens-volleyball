"""Recruiting-atlas tables: miles from home, in-state share, catchment
radii and state-to-state flows."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .load import GEO, ROOT, load_rosters

EARTH_MILES = 3958.8
# precisions trusted for distance statistics; country/region centroids are
# kept for the international view but excluded from distance summaries
DISTANCE_PRECISIONS = ("place", "fuzzy", "county", "place_inferred_state")


def haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_MILES * np.arcsin(np.sqrt(a))


def load_campuses() -> pd.DataFrame:
    c = pd.read_csv(GEO / "team_locations.csv", dtype={"ncaa_id": str,
                    "unitid": str}, keep_default_na=False)
    c["campus_state"] = np.where(c.ipeds_state != "", c.ipeds_state, c.team_state)
    c["campus_country"] = np.where(c.ncaa_id == "30127", "CAN", "USA")
    c.loc[c.campus_country != "USA", "campus_state"] = ""
    return c[["ncaa_id", "lat", "lon", "campus_state", "campus_country",
              "ipeds_name", "match_method"]].rename(
        columns={"lat": "campus_lat", "lon": "campus_lon"})


def load_conferences() -> pd.DataFrame:
    t = pd.read_csv(ROOT / "data" / "vb_teams.csv", dtype=str,
                    keep_default_na=False)
    return (t[["ncaa_id", "conference"]].drop_duplicates("ncaa_id")
            .rename(columns={"conference": "conference_current"}))


def player_seasons_geo(rosters: pd.DataFrame | None = None) -> pd.DataFrame:
    r = rosters if rosters is not None else load_rosters()
    g = pd.read_csv(GEO / "hometown_geocodes.csv", dtype=str,
                    keep_default_na=False)
    g["lat"] = pd.to_numeric(g.lat)
    g["lon"] = pd.to_numeric(g.lon)
    m = r.merge(g, on=["hometown_clean", "state", "country"], how="left")
    m = m.merge(load_campuses(), on="ncaa_id", how="left")
    m["geo_precision"] = m.geo_precision.fillna("no_hometown")
    m["home_state"] = m.parsed_state.fillna("")
    m["home_country"] = m.parsed_country.fillna("")
    m = infer_bare_cities(m)
    m["distance_miles"] = haversine(m.lat, m.lon, m.campus_lat,
                                    m.campus_lon).round(1)
    us_both = (m.home_country == "USA") & (m.home_state != "") & (m.campus_state != "")
    m["in_state"] = np.where(us_both, m.home_state == m.campus_state, pd.NA)
    m["international"] = np.where(m.home_country != "",
                                  m.home_country != "USA", pd.NA)
    cols = ["source_row_id", "season", "ncaa_id", "team", "division",
            "home_state", "home_country", "lat", "lon", "geo_precision",
            "campus_state", "campus_lat", "campus_lon", "distance_miles",
            "in_state", "international"]
    out = m[cols].rename(columns={"lat": "home_lat", "lon": "home_lon"})
    return out.sort_values("source_row_id", kind="stable").reset_index(drop=True)


def infer_bare_cities(m: pd.DataFrame) -> pd.DataFrame:
    """Many roster sites print an in-state player's hometown as a bare city
    ("Rowland Heights") and add the state only for out-of-state players.
    Geocode a bare city against the campus's state when that place exists
    there; flag it as place_inferred_state. Leaving these out would make
    such programs look far more national than they are."""
    path = GEO / "bare_city_geocodes.csv"
    if not path.exists():
        return m
    b = pd.read_csv(path, dtype=str, keep_default_na=False)
    b["lat"] = pd.to_numeric(b.lat)
    b["lon"] = pd.to_numeric(b.lon)
    key = ["hometown_clean", "campus_state"]
    bare = ((m.geo_precision == "unmatched") & (m.home_state == "")
            & (m.home_country == "") & ~m.hometown_clean.str.contains(","))
    hit = m.loc[bare, key].merge(b, on=key, how="left")
    hit.index = m.index[bare]
    ok = hit.lat.notna()
    idx = hit.index[ok]
    m.loc[idx, "lat"] = hit.loc[ok, "lat"]
    m.loc[idx, "lon"] = hit.loc[ok, "lon"]
    m.loc[idx, "home_state"] = hit.loc[ok, "campus_state"]
    m.loc[idx, "home_country"] = "USA"
    m.loc[idx, "geo_precision"] = "place_inferred_state"
    return m


def bare_city_keys(rosters: pd.DataFrame) -> pd.DataFrame:
    camp = load_campuses()[["ncaa_id", "campus_state"]]
    r = rosters[(rosters.state == "") & (rosters.country == "")
                & (rosters.hometown_clean != "")
                & ~rosters.hometown_clean.str.contains(",")]
    r = r.merge(camp, on="ncaa_id")
    return (r[r.campus_state != ""][["hometown_clean", "campus_state"]]
            .drop_duplicates().sort_values(["campus_state", "hometown_clean"]))


def _share(s: pd.Series) -> float:
    s = s.dropna().astype(bool)
    return round(float(s.mean()), 4) if len(s) else np.nan


def summarize(geo: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    d = geo.copy()
    trusted = d.geo_precision.isin(DISTANCE_PRECISIONS)
    d["dist"] = d.distance_miles.where(trusted)

    def agg(x: pd.DataFrame) -> pd.Series:
        dist = x.dist.dropna()
        return pd.Series({
            "players": len(x),
            "players_with_distance": len(dist),
            "median_miles": round(dist.median(), 1) if len(dist) else np.nan,
            "mean_miles": round(dist.mean(), 1) if len(dist) else np.nan,
            "p80_miles": round(dist.quantile(0.8), 1) if len(dist) else np.nan,
            "within_100_share": round((dist <= 100).mean(), 4) if len(dist) else np.nan,
            "over_500_share": round((dist > 500).mean(), 4) if len(dist) else np.nan,
            "in_state_share": _share(x.in_state),
            "international_share": _share(x.international),
            "home_states": x.loc[x.home_country == "USA", "home_state"]
                           .replace("", np.nan).nunique(),
            "home_countries": x.home_country.replace("", np.nan).nunique(),
        })

    out = d.groupby(by, sort=True).apply(agg, include_groups=False).reset_index()
    for c in ("players", "players_with_distance", "home_states", "home_countries"):
        out[c] = out[c].astype(int)
    return out


def program_season_geo(geo: pd.DataFrame) -> pd.DataFrame:
    out = summarize(geo, ["ncaa_id", "season"])
    meta = (geo[["ncaa_id", "team", "division", "campus_state"]]
            .drop_duplicates("ncaa_id", keep="last"))
    out = meta.merge(out, on="ncaa_id").merge(load_conferences(), on="ncaa_id",
                                              how="left")
    return out.sort_values(["ncaa_id", "season"]).reset_index(drop=True)


def division_season_geo(geo: pd.DataFrame) -> pd.DataFrame:
    d = geo[geo.division.isin(["I", "II", "III"])]
    return summarize(d, ["division", "season"])


def state_flows(geo: pd.DataFrame) -> pd.DataFrame:
    us = geo[(geo.home_country == "USA") & (geo.home_state != "")]
    f = (us.groupby(["season", "division", "home_state", "campus_state"])
         .size().rename("players").reset_index())
    return f.sort_values(["season", "division", "home_state", "campus_state"]
                         ).reset_index(drop=True)


def state_balance(geo: pd.DataFrame) -> pd.DataFrame:
    """Per state and season: players it produces vs roster spots its
    programs fill, and how many of its own players stay home."""
    us = geo[geo.home_country == "USA"]
    produced = us[us.home_state != ""].groupby(["season", "home_state"]).size()
    hosted = geo[geo.campus_state != ""].groupby(["season", "campus_state"]).size()
    stayed = us[us.in_state == True].groupby(["season", "home_state"]).size()  # noqa: E712
    out = pd.DataFrame({"produced": produced}).rename_axis(["season", "state"])
    out = out.join(hosted.rename("roster_spots").rename_axis(["season", "state"]),
                   how="outer")
    out = out.join(stayed.rename("stayed_home").rename_axis(["season", "state"]),
                   how="left").fillna(0).astype(int).reset_index()
    out["net_export"] = out.produced - out.roster_spots
    out["stay_home_share"] = (out.stayed_home / out.produced.replace(0, np.nan)).round(4)
    return out.sort_values(["season", "state"]).reset_index(drop=True)
