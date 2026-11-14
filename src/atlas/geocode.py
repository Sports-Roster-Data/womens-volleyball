"""Offline geocoding of roster hometowns.

Keys are (hometown_clean, state, country) exactly as published in
data/clean. US places resolve against, in order: GeoNames cities500 (has
population, so the largest same-named place in the state wins), Census
Gazetteer places, Census county subdivisions (townships and New England
towns), then a close fuzzy match within the state. International places
resolve against GeoNames by city + country, then first-level region, then a
population-weighted country centroid.

Rows the R build could not parse (hometown_status = unparsed) are re-tried
when the string ends in a recognizable US state or a country; bare city names
with no state stay unmatched rather than being guessed.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import get_close_matches

import pandas as pd

from .load import RAW_GEO, REFERENCE

LSAD = re.compile(
    r"\s+(city and borough|consolidated government|metropolitan government|"
    r"unified government|urban county|charter township|city|town|village|"
    r"borough|cdp|municipality|township|plantation|gore|grant|location|"
    r"purchase|reservation|comunidad|zona urbana|\(balance\)|government)$")
PREFIX = re.compile(r"^(hometown|from)\s*[:\-]?\s*", re.I)
WORD_SUBS = {"st": "saint", "ste": "sainte", "ft": "fort", "mt": "mount",
             "pt": "point", "twp": "township", "n": "north", "s": "south",
             "e": "east", "w": "west"}

GEOCODE_COLS = ["hometown_clean", "state", "country", "lat", "lon",
                "geo_source", "geo_precision", "geo_name", "parsed_state",
                "parsed_country"]


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[.'’`]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return " ".join(WORD_SUBS.get(w, w) for w in s.split())


def strip_lsad(name: str) -> str:
    prev = None
    s = norm(name)
    while prev != s:
        prev, s = s, LSAD.sub("", s)
    return s


def query_variants(city: str) -> list[str]:
    base = norm(PREFIX.sub("", city))
    out = [base]
    stripped = strip_lsad(base)
    if stripped != base:
        out.append(stripped)
    return [v for v in out if v]


class Gazetteer:
    def __init__(self) -> None:
        self.iso3_to_2 = {}
        for line in (RAW_GEO / "countryInfo.txt").read_text("utf-8").splitlines():
            if line and not line.startswith("#"):
                f = line.split("\t")
                self.iso3_to_2[f[1]] = f[0]
        cols = ["geonameid", "name", "asciiname", "alt", "lat", "lon", "fclass",
                "fcode", "cc", "cc2", "admin1", "admin2", "admin3", "admin4",
                "population", "elev", "dem", "tz", "mod"]
        g = pd.read_csv(RAW_GEO / "cities500.txt", sep="\t", header=None,
                        names=cols, dtype=str, keep_default_na=False,
                        quoting=3)
        g["population"] = pd.to_numeric(g.population, errors="coerce").fillna(0)
        g["lat"] = g.lat.astype(float)
        g["lon"] = g.lon.astype(float)
        self.geonames = g
        # (cc, admin1-or-"", normalized name) -> (pop, lat, lon, label)
        self.gn: dict[tuple, tuple] = {}
        for r in g.itertuples():
            names = {norm(r.name), norm(r.asciiname)}
            if r.cc != "US":
                # alternate names help with exonyms (Belgrade/Beograd)
                names |= {norm(a) for a in r.alt.split(",")[:40] if a}
            for n in names:
                for key in ((r.cc, r.admin1, n), (r.cc, "", n)):
                    cur = self.gn.get(key)
                    if cur is None or r.population > cur[0]:
                        self.gn[key] = (r.population, r.lat, r.lon,
                                        f"{r.name} ({r.cc})")
        a1 = pd.read_csv(RAW_GEO / "admin1CodesASCII.txt", sep="\t",
                         header=None, dtype=str, keep_default_na=False)
        # region centroid = population-weighted mean of its cities
        g["a1key"] = g.cc + "." + g.admin1
        w = g.assign(wl=g.lat * (g.population + 1), wo=g.lon * (g.population + 1),
                     w=g.population + 1)
        agg = w.groupby("a1key")[["wl", "wo", "w"]].sum()
        cent = (agg.wl / agg.w).to_frame("lat").join((agg.wo / agg.w).rename("lon"))
        self.region: dict[tuple, tuple] = {}
        for r in a1.itertuples():
            cc = r[1].split(".")[0]
            if r[1] in cent.index:
                c = cent.loc[r[1]]
                for n in {norm(r[2]), norm(r[3])}:
                    self.region[(cc, n)] = (c.lat, c.lon, r[2])
        cagg = w.groupby("cc")[["wl", "wo", "w"]].sum()
        self.country = {cc: (row.wl / row.w, row.wo / row.w)
                        for cc, row in cagg.iterrows()}

        self.place: dict[tuple, tuple] = {}
        for fname, src in (("2024_Gaz_place_national.txt", "census_place"),
                           ("2024_Gaz_cousubs_national.txt", "census_cousub")):
            t = pd.read_csv(RAW_GEO / fname, sep="\t", dtype=str,
                            keep_default_na=False)
            t.columns = [c.strip() for c in t.columns]
            t["ALAND"] = pd.to_numeric(t.ALAND, errors="coerce").fillna(0)
            for r in t.itertuples():
                for n in {strip_lsad(r.NAME), norm(r.NAME)}:
                    key = (src, r.USPS, n)
                    cur = self.place.get(key)
                    if cur is None or r.ALAND > cur[0]:
                        self.place[key] = (r.ALAND, float(r.INTPTLAT),
                                           float(r.INTPTLONG.strip()), r.NAME)
        # Every US populated place (incl. unincorporated communities and
        # neighborhoods like Kingwood, TX), loaded lazily from GeoNames US.txt.
        self.us_all: dict[tuple, tuple] = {}
        rank = {"PPL": 3, "PPLA": 4, "PPLA2": 4, "PPLC": 5, "PPLL": 2, "PPLX": 1}
        with open(RAW_GEO / "US.txt", encoding="utf-8") as fh:
            for line in fh:
                f = line.split("\t")
                if f[6] != "P":
                    continue
                pop = int(f[14] or 0)
                score = (pop, rank.get(f[7], 0))
                for n in {norm(f[1]), norm(f[2])}:
                    key = (f[10], n)
                    cur = self.us_all.get(key)
                    if cur is None or score > cur[0]:
                        self.us_all[key] = (score, float(f[4]), float(f[5]),
                                            f"{f[1]} ({f[7]})")
        cty = pd.read_csv(RAW_GEO / "2024_Gaz_counties_national.txt", sep="\t",
                          dtype=str, keep_default_na=False)
        cty.columns = [c.strip() for c in cty.columns]
        self.county: dict[tuple, tuple] = {}
        for r in cty.itertuples():
            base = norm(r.NAME)
            for n in {base, re.sub(r" (county|parish|borough|census area|"
                                   r"municipality|city and borough)$", "", base)}:
                self.county[(r.USPS, n)] = (float(r.INTPTLAT),
                                            float(r.INTPTLONG.strip()), r.NAME)
        self.names_by_state: dict[str, list[str]] = {}
        for (cc, a1, n) in self.gn:
            if cc == "US" and a1:
                self.names_by_state.setdefault(a1, []).append(n)
        for (src, st, n) in self.place:
            self.names_by_state.setdefault(st, []).append(n)
        self.names_by_state = {k: sorted(set(v))
                               for k, v in self.names_by_state.items()}

    # -- lookups -----------------------------------------------------------
    def us(self, city: str, state: str):
        for q in query_variants(city):
            hit = self.gn.get(("US", state, q))
            if hit:
                return hit[1], hit[2], "geonames", "place", hit[3]
            for src in ("census_place", "census_cousub"):
                hit = self.place.get((src, state, q))
                if hit:
                    return hit[1], hit[2], src, "place", hit[3]
        for q in query_variants(city):
            hit = self.us_all.get((state, q))
            if hit:
                return hit[1], hit[2], "geonames_us", "place", hit[3]
        for q in query_variants(city):
            hit = self.county.get((state, q))
            if hit:
                return hit[0], hit[1], "census_county", "county", hit[2]
        q = query_variants(city)[-1] if query_variants(city) else ""
        if len(q) >= 5:
            close = get_close_matches(q, self.names_by_state.get(state, []),
                                      n=1, cutoff=0.9)
            if close:
                r = self.us(close[0], state)
                if r:
                    return r[0], r[1], r[2], "fuzzy", r[4]
        return None

    def intl(self, city: str, iso3: str):
        cc = self.iso3_to_2.get(iso3, "")
        if not cc:
            return None
        for q in query_variants(city):
            hit = self.gn.get((cc, "", q))
            if hit:
                return hit[1], hit[2], "geonames", "place", hit[3]
            reg = self.region.get((cc, q))
            if reg:
                return reg[0], reg[1], "geonames_admin1", "region", reg[2]
        if cc in self.country:
            lat, lon = self.country[cc]
            return lat, lon, "geonames_country", "country", cc
        return None


def load_variants() -> tuple[dict, dict]:
    sv = pd.read_csv(REFERENCE / "state_variants.csv", dtype=str,
                     keep_default_na=False)
    cv = pd.read_csv(REFERENCE / "country_variants.csv", dtype=str,
                     keep_default_na=False)
    states = {norm(k).upper(): v for k, v in zip(sv.raw_key, sv.state_code)}
    states.update({norm(v).upper(): v for v in sv.state_code})
    countries = {norm(k).upper(): v for k, v in zip(cv.raw_key, cv.iso3)}
    # Fallbacks for keys the R reference tables lack (e.g. "KENTUCKY", "AZ",
    # "BULGARIA"); reviewed variants above always win.
    for line in (RAW_GEO / "admin1CodesASCII.txt").read_text("utf-8").splitlines():
        code, name, ascii_name, _ = line.split("\t")
        if code.startswith("US."):
            st = code[3:]
            for k in (st, name, ascii_name):
                states.setdefault(norm(k).upper(), st)
    for line in (RAW_GEO / "countryInfo.txt").read_text("utf-8").splitlines():
        if line and not line.startswith("#"):
            f = line.split("\t")
            for k in (f[1], f[4]):
                countries.setdefault(norm(k).upper(), f[1])
    return states, countries


def reparse(hometown: str, states: dict, countries: dict) -> tuple[str, str]:
    """For unparsed strings: ('City, AZ') -> ('AZ', 'USA'); ('Sofia,
    Bulgaria') -> ('', 'BGR'). Returns ('', '') when nothing is recognized."""
    parts = [p.strip() for p in hometown.split(",") if p.strip()]
    if len(parts) < 2:
        return "", ""
    tail = norm(parts[-1]).upper()
    if tail in states:
        return states[tail], "USA"
    if tail in countries:
        return "", countries[tail]
    return "", ""


def geocode_keys(keys: pd.DataFrame, gaz: Gazetteer | None = None) -> pd.DataFrame:
    """keys: distinct (hometown_clean, state, country). Returns one row per
    key with coordinates (NaN when unmatched) and provenance."""
    gaz = gaz or Gazetteer()
    states, countries = load_variants()
    rows = []
    for k in keys.itertuples(index=False):
        ht, st, co = k.hometown_clean, k.state, k.country
        pst, pco = st, co
        if ht and not st and not co:
            pst, pco = reparse(ht, states, countries)
        city = ht.split(",")[0].strip() if ht else ""
        res = None
        if city and pco == "USA" and pst:
            res = gaz.us(city, pst)
        elif city and pco and pco != "USA":
            res = gaz.intl(city, pco)
        if res is None:
            rows.append([ht, st, co, float("nan"), float("nan"), "", "unmatched",
                         "", pst, pco])
        else:
            lat, lon, src, prec, label = res
            rows.append([ht, st, co, round(lat, 5), round(lon, 5), src, prec,
                         label, pst, pco])
    out = pd.DataFrame(rows, columns=GEOCODE_COLS)
    return out.sort_values(["country", "state", "hometown_clean"],
                           kind="stable").reset_index(drop=True)
