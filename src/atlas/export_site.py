"""Write the compact JSON files the static site in web/ reads."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .load import ROOT, load_rosters, read_out, seasons

WEB_DATA = ROOT / "web" / "data"
RECENT = 3  # seasons used for footprints and similarity


def _clean(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        f = float(v)
        return None if math.isnan(f) else round(f, 4)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    return v


def records(df: pd.DataFrame) -> list[dict]:
    return [{k: _clean(v) for k, v in row.items()}
            for row in df.to_dict(orient="records")]


def dump(obj, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), sort_keys=True,
                               default=_clean), encoding="utf-8")


def export() -> int:
    ss = seasons()
    recent = ss[-RECENT:]
    r = load_rosters()
    geo = read_out("player_seasons_geo.csv")
    prog = read_out("program_season_geo.csv")
    div = read_out("division_season_geo.csv")
    bal = read_out("state_balance.csv")
    flows = read_out("state_flows.csv")
    links = read_out("player_links.csv")
    tr = read_out("transfers.csv").fillna("")
    coh = read_out("cohorts.csv")
    pipes = read_out("intl_pipelines.csv").fillna("")
    routes = read_out("intl_routes.csv").fillna("")
    camp = pd.read_csv(ROOT / "data" / "reference" / "geo" / "team_locations.csv",
                       dtype={"ncaa_id": str}, keep_default_na=False)
    n = 0

    # ---- program index + similarity ------------------------------------
    latest = prog.sort_values("season").drop_duplicates("ncaa_id", keep="last")
    idx = latest[["ncaa_id", "team", "division", "campus_state",
                  "conference_current", "season", "median_miles",
                  "in_state_share", "international_share"]].merge(
        camp[["ncaa_id", "lat", "lon"]], on="ncaa_id", how="left")
    idx = idx.rename(columns={"season": "last_season"})
    idx["conference_current"] = idx.conference_current.fillna("")

    g = geo[geo.season.isin(recent)].copy()
    g["origin"] = np.where(g.home_country.fillna("") == "USA",
                           g.home_state.fillna(""), g.home_country.fillna(""))
    g = g[g.origin != ""]
    foot = (g.groupby(["ncaa_id", "origin"]).size().unstack(fill_value=0))
    foot = foot.div(foot.sum(axis=1), axis=0)
    norm = np.sqrt((foot ** 2).sum(axis=1))
    unit = foot.div(norm, axis=0)
    sim = unit.values @ unit.values.T
    ids = list(foot.index)
    neighbours = {}
    for i, pid in enumerate(ids):
        order = np.argsort(-sim[i])
        neighbours[pid] = [[ids[j], round(float(sim[i, j]), 3)]
                           for j in order if j != i][:6]
    dump(records(idx), WEB_DATA / "programs.json"); n += 1

    # ---- overview -------------------------------------------------------
    lt = tr[tr.kind == "linked"]
    tby = (lt.groupby(["to_first_season", "from_division", "to_division"])
           .size().rename("n").reset_index())
    coh_div = (coh.groupby(["division", "cohort"])[
        ["freshmen", "transferred", "left", "completed", "still_on_roster"]]
        .sum().reset_index())
    coh_div = coh_div[coh_div.division.isin(["I", "II", "III"])]
    obs = coh.groupby("cohort").seasons_observable.first()
    coh_div["seasons_observable"] = coh_div.cohort.map(obs)
    reported = (tr[tr.kind != "linked"].groupby(["to_first_season", "kind",
                                                  "to_division"]).size()
                .rename("n").reset_index())
    dump({"seasons": ss,
          "division": records(div),
          "transfers": records(tby),
          "reported_transfers": records(reported),
          "cohorts": records(coh_div),
          "totals": {"player_seasons": int(len(r)),
                     "programs": int(r.ncaa_id.nunique()),
                     "people": int(links.person_id.nunique()),
                     "linked_transfers": int(len(lt))}},
         WEB_DATA / "overview.json"); n += 1

    # ---- per-program files ---------------------------------------------
    from .geography import DISTANCE_PRECISIONS
    pts = geo[geo.geo_precision.isin(DISTANCE_PRECISIONS)
              & geo.home_lat.notna()].copy()
    pts["k"] = (pts.home_lat.round(3).astype(str) + ","
                + pts.home_lon.round(3).astype(str))
    pts_g = (pts.groupby(["ncaa_id", "season", "home_lat", "home_lon",
                          "home_state", "home_country"])
             .size().rename("n").reset_index())
    names = (r[["source_row_id", "name", "hometown_clean"]]
             .merge(links[["source_row_id", "person_id"]], on="source_row_id")
             .drop_duplicates("person_id", keep="last")
             .set_index("person_id"))
    intl_p = pipes.groupby(["ncaa_id", "season", "home_country",
                            "country_name"]).players.sum().reset_index()
    for pid, p in prog.groupby("ncaa_id"):
        pp = pts_g[pts_g.ncaa_id == pid]
        t_in = lt[lt.to_ncaa_id == pid]
        t_out = lt[lt.from_ncaa_id == pid]
        rep_in = tr[(tr.kind != "linked") & (tr.to_ncaa_id == pid)]
        c = coh[coh.ncaa_id == pid]
        meta = idx[idx.ncaa_id == pid].iloc[0]
        dump({
            "ncaa_id": pid,
            "team": meta.team, "division": meta.division,
            "state": meta.campus_state, "conference": meta.conference_current,
            "lat": _clean(meta.lat), "lon": _clean(meta.lon),
            "seasons": records(p.drop(columns=["ncaa_id", "team", "division",
                                               "campus_state",
                                               "conference_current"])),
            "points": [[s_, round(a, 3), round(b, 3), st or co, int(k)]
                       for s_, a, b, st, co, k in pp[[
                           "season", "home_lat", "home_lon", "home_state",
                           "home_country", "n"]].itertuples(index=False)],
            "transfers_in": [[x.to_first_season, x.from_ncaa_id, x.from_team,
                              x.from_division, names.name.get(x.person_id, ""),
                              x.tier] for x in t_in.itertuples()],
            "transfers_out": [[x.to_first_season, x.to_ncaa_id, x.to_team,
                               x.to_division, names.name.get(x.person_id, ""),
                               x.tier] for x in t_out.itertuples()],
            "reported_in": [[x.to_first_season, x.kind,
                             x.previous_school_reported] for x in rep_in.itertuples()],
            "cohorts": records(c.drop(columns=["ncaa_id", "team", "division"])),
            "international": records(intl_p[intl_p.ncaa_id == pid].drop(
                columns="ncaa_id")),
            "neighbours": neighbours.get(pid, []),
        }, WEB_DATA / "program" / f"{pid}.json")
        n += 1

    # ---- states ---------------------------------------------------------
    st_names = pd.read_csv(ROOT / "data" / "reference" / "state_variants.csv",
                           dtype=str, keep_default_na=False)
    st_names = (st_names.drop_duplicates("state_code")
                .set_index("state_code").state_name.to_dict())
    dump({"balance": records(bal), "names": st_names,
          "flows": records(flows)}, WEB_DATA / "states.json"); n += 1

    # ---- transfers table -------------------------------------------------
    tt = lt.assign(name=lt.person_id.map(names.name).fillna(""))
    dump([[x.to_first_season, x.name, x.from_ncaa_id, x.from_team,
           x.from_division, x.from_last_class, x.to_ncaa_id, x.to_team,
           x.to_division, x.to_first_class, x.tier, x.evidence, x.home_state]
          for x in tt.sort_values(["to_first_season", "name"]).itertuples()],
         WEB_DATA / "transfers.json"); n += 1

    # ---- international --------------------------------------------------
    by_country = (pipes.groupby(["season", "division", "home_country",
                                 "country_name"])
                  [["players", "new_arrivals"]].sum().reset_index())
    arcs = (pipes[pipes.season.isin(recent)]
            .groupby(["home_country", "ncaa_id", "team"]).players.sum()
            .reset_index())
    cent = (geo[(geo.home_country.fillna("") != "USA")
                & geo.home_lat.notna()]
            .groupby("home_country")[["home_lat", "home_lon"]].median()
            .round(3).reset_index())
    rt = (routes.groupby(["first_season", "division", "route"]).size()
          .rename("n").reset_index())
    dump({"by_country": records(by_country), "arcs": records(arcs),
          "centroids": records(cent), "routes": records(rt),
          "recent": recent}, WEB_DATA / "international.json"); n += 1
    return n
