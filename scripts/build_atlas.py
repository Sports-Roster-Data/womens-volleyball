"""Build the recruiting atlas, player links, transfers, international
pipelines and the static site data.

    uv run python scripts/build_atlas.py --step fetch      # download gazetteers
    uv run python scripts/build_atlas.py --step campus     # ~2 min, writes team_locations.csv
    uv run python scripts/build_atlas.py --step all        # everything after campus

Reads data/clean only; writes data/reference/geo/ and data/derived/atlas/.
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atlas import load  # noqa: E402

SOURCES = [
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_place_national.zip",
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_cousubs_national.zip",
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_counties_national.zip",
    "https://download.geonames.org/export/dump/cities500.zip",
    "https://download.geonames.org/export/dump/US.zip",
    "https://download.geonames.org/export/dump/countryInfo.txt",
    "https://download.geonames.org/export/dump/admin1CodesASCII.txt",
    "https://nces.ed.gov/ipeds/datacenter/data/HD2023.zip",
]


def log(msg: str) -> None:
    print(f"[atlas] {msg}", flush=True)


def step_fetch() -> None:
    load.RAW_GEO.mkdir(parents=True, exist_ok=True)
    for url in SOURCES:
        dest = load.RAW_GEO / url.rsplit("/", 1)[1]
        if not dest.exists():
            log(f"downloading {dest.name}")
            urllib.request.urlretrieve(url, dest)
        if dest.suffix == ".zip":
            with zipfile.ZipFile(dest) as z:
                for name in z.namelist():
                    # never extract outside RAW_GEO
                    if "/" in name or name.startswith(".."):
                        continue
                    z.extract(name, load.RAW_GEO)


def step_campus() -> None:
    from atlas.campus import match_teams
    df = match_teams()
    missing = df[df.lat.isna()]
    if len(missing):
        raise SystemExit(f"teams without coordinates: {missing.ncaa_id.tolist()}")
    df = df.sort_values("ncaa_id", key=lambda s: s.astype(int))
    load.write_csv(df, load.GEO / "team_locations.csv")
    log(f"team_locations.csv: {len(df)} teams; "
        f"{df.match_method.value_counts().to_dict()}")


def step_geo() -> None:
    from atlas import geography as geo
    from atlas.geocode import geocode_keys
    rosters = load.load_rosters()
    keys = (rosters[rosters.hometown_clean != ""]
            [["hometown_clean", "state", "country"]].drop_duplicates())
    from atlas.geocode import Gazetteer
    gaz = Gazetteer()
    g = geocode_keys(keys, gaz)
    load.write_csv(g, load.GEO / "hometown_geocodes.csv")
    bare = geo.bare_city_keys(rosters)
    found = [gaz.us(h, st) for h, st in zip(bare.hometown_clean, bare.campus_state)]
    bare["lat"] = [f[0] if f and f[3] == "place" else None for f in found]
    bare["lon"] = [f[1] if f and f[3] == "place" else None for f in found]
    bare = bare.dropna(subset=["lat"]).round({"lat": 5, "lon": 5})
    load.write_csv(bare, load.GEO / "bare_city_geocodes.csv")
    review = g[g.geo_precision.isin(["unmatched", "fuzzy"])]
    load.write_csv(review, load.OUT / "review" / "hometown_geocode_review.csv")
    ps = geo.player_seasons_geo(rosters)
    load.write_csv(ps, load.OUT / "player_seasons_geo.csv")
    load.write_csv(geo.program_season_geo(ps), load.OUT / "program_season_geo.csv")
    load.write_csv(geo.division_season_geo(ps), load.OUT / "division_season_geo.csv")
    load.write_csv(geo.state_flows(ps), load.OUT / "state_flows.csv")
    load.write_csv(geo.state_balance(ps), load.OUT / "state_balance.csv")
    log(f"geocoded {len(g)} hometown keys; row precision "
        f"{ps.geo_precision.value_counts().to_dict()}")


def step_link() -> None:
    from atlas import link
    rosters = load.load_rosters()
    links, pairs, review = link.link_players(rosters)
    load.write_csv(links, load.OUT / "player_links.csv")
    load.write_csv(pairs, load.OUT / "stint_links.csv")
    load.write_csv(review, load.OUT / "review" / "possible_links.csv")
    log(f"linked {len(links)} rows into {links.person_id.nunique()} people; "
        f"{links.link_tier.value_counts().to_dict()}")


def step_transfers() -> None:
    from atlas import transfers
    rosters = load.load_rosters()
    links = load.read_out("player_links.csv")
    pairs = load.read_out("stint_links.csv")
    t = transfers.transfer_events(rosters, links, pairs)
    load.write_csv(t, load.OUT / "transfers.csv")
    c = transfers.cohorts(rosters, links, t)
    load.write_csv(c, load.OUT / "cohorts.csv")
    load.write_csv(transfers.validation(rosters, links, t),
                   load.OUT / "review" / "transfer_validation.csv")
    log(f"{len(t)} transfer events; {len(c)} freshman cohort rows")


def step_intl() -> None:
    from atlas import international
    geo = load.read_out("player_seasons_geo.csv")
    links = load.read_out("player_links.csv")
    t = load.read_out("transfers.csv")
    rosters = load.load_rosters()
    p, s, r = international.build(rosters, geo, links, t)
    load.write_csv(p, load.OUT / "intl_pipelines.csv")
    load.write_csv(s, load.OUT / "intl_program_summary.csv")
    load.write_csv(r, load.OUT / "intl_routes.csv")
    log(f"intl pipelines: {len(p)} rows")


def step_site() -> None:
    from atlas import export_site
    n = export_site.export()
    log(f"site data: {n} files")


STEPS = {"fetch": step_fetch, "campus": step_campus, "geo": step_geo,
         "link": step_link, "transfers": step_transfers, "intl": step_intl,
         "site": step_site}
ALL = ["geo", "link", "transfers", "intl", "site"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", choices=[*STEPS, "all"], required=True)
    a = ap.parse_args()
    for s in (ALL if a.step == "all" else [a.step]):
        t0 = time.time()
        STEPS[s]()
        log(f"{s} done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
