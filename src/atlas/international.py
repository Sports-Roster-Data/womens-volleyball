"""International pipeline: which countries feed which programs, how that
changes over time, and the route each international player took in."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .link import season_index


def _country_names() -> dict[str, str]:
    from .load import RAW_GEO
    out = {}
    for line in (RAW_GEO / "countryInfo.txt").read_text("utf-8").splitlines():
        if line and not line.startswith("#"):
            f = line.split("\t")
            out[f[1]] = f[4]
    return out


def build(r: pd.DataFrame, geo: pd.DataFrame, links: pd.DataFrame,
          transfers: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    s = season_index()
    names = _country_names()
    g = geo[["source_row_id", "home_country", "home_lat", "home_lon",
             "geo_precision"]].copy()
    g["home_country"] = g.home_country.fillna("")
    m = (r[["source_row_id", "season", "ncaa_id", "team", "division", "region",
            "previous_school_type"]]
         .merge(g, on="source_row_id")
         .merge(links[["source_row_id", "person_id", "stint_id"]],
                on="source_row_id"))
    intl = m[(m.home_country != "") & (m.home_country != "USA")].copy()
    intl["si"] = intl.season.map(s)
    first_si = intl.groupby("stint_id").si.transform("min")
    intl["arrival"] = np.where(intl.si == first_si, "new", "returning")

    pipelines = (intl.groupby(["season", "home_country", "ncaa_id", "team",
                               "division"])
                 .agg(players=("source_row_id", "size"),
                      new_arrivals=("arrival", lambda a: int((a == "new").sum())))
                 .reset_index())
    pipelines.insert(2, "country_name", pipelines.home_country.map(names).fillna(""))

    # program summary: international share of known-origin roster spots
    known = m[m.home_country != ""]
    tot = known.groupby(["ncaa_id", "season"]).size().rename("known_origin")
    n_int = intl.groupby(["ncaa_id", "season"]).size().rename("international")
    countries = intl.groupby(["ncaa_id", "season"]).home_country.agg(
        lambda c: "|".join(sorted(set(c)))).rename("countries")
    summary = pd.concat([tot, n_int, countries], axis=1).reset_index()
    summary["international"] = summary.international.fillna(0).astype(int)
    summary["countries"] = summary.countries.fillna("")
    summary["international_share"] = (summary.international
                                      / summary.known_origin).round(4)
    meta = m.drop_duplicates("ncaa_id", keep="last")[["ncaa_id", "team",
                                                       "division"]]
    summary = meta.merge(summary, on="ncaa_id").sort_values(["ncaa_id", "season"])

    # route in, judged on each international player's first NCAA stint
    linked_in = set(transfers.loc[transfers.kind == "linked", "person_id"]
                    + "|" + transfers.loc[transfers.kind == "linked", "to_ncaa_id"])
    juco_in = set(transfers.loc[transfers.kind == "reported_juco", "person_id"])
    first_stint = (intl.sort_values("si").drop_duplicates("person_id"))
    route = np.select(
        [first_stint.person_id.isin(juco_in)
         | (first_stint.previous_school_type == "Junior College"),
         (first_stint.person_id + "|" + first_stint.ncaa_id).isin(linked_in)],
        ["via_junior_college", "via_ncaa_transfer"], "direct")
    routes = pd.DataFrame({
        "person_id": first_stint.person_id,
        "home_country": first_stint.home_country,
        "country_name": first_stint.home_country.map(names).fillna(""),
        "first_season": first_stint.season,
        "first_ncaa_id": first_stint.ncaa_id,
        "first_team": first_stint.team,
        "division": first_stint.division,
        "route": route,
    })
    # anyone who later moved programs
    moved = set(transfers.loc[transfers.kind == "linked", "person_id"])
    routes["later_transferred"] = routes.person_id.isin(moved)
    return (pipelines.sort_values(["season", "home_country", "ncaa_id"])
            .reset_index(drop=True),
            summary.reset_index(drop=True),
            routes.sort_values(["first_season", "person_id"]).reset_index(drop=True))
