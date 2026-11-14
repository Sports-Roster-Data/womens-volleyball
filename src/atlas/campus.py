"""Match volleyball programs (ncaa_id) to IPEDS institutions for campus
coordinates.

Signals, strongest first:
  1. the athletics site's registered domain equals the IPEDS web domain
     (e.g. athletics.bellarmine.edu -> bellarmine.edu);
  2. normalized-name similarity, with a bonus for the same state.
Reviewed overrides in data/reference/geo/team_location_overrides.csv win over
both. Anything below the similarity threshold is left unmatched for review.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

import pandas as pd

from .load import GEO, RAW_GEO, REFERENCE, ROOT

ABBREV = {
    "st": "state", "st.": "state", "univ": "university", "u": "university",
    "col": "college", "col.": "college", "so": "southern", "so.": "southern",
    "no": "northern", "no.": "northern", "n": "north", "s": "south",
    "e": "east", "w": "west", "mt": "mount", "mt.": "mount", "ft": "fort",
    "tex": "texas", "tex.": "texas", "ill": "illinois", "ill.": "illinois",
    "wis": "wisconsin", "wis.": "wisconsin", "mich": "michigan",
    "minn": "minnesota", "mass": "massachusetts", "conn": "connecticut",
    "ga": "georgia", "fla": "florida", "ala": "alabama", "ark": "arkansas",
    "ky": "kentucky", "tenn": "tennessee", "miss": "mississippi",
    "mo": "missouri", "neb": "nebraska", "okla": "oklahoma", "pa": "pennsylvania",
    "va": "virginia", "colo": "colorado", "ariz": "arizona", "ind": "indiana",
    "ia": "iowa", "md": "maryland", "nc": "north carolina", "sc": "south carolina",
    "nm": "new mexico", "ny": "new york", "nj": "new jersey", "cal": "california",
    "uc": "university of california", "csu": "california state university",
    "chi": "chicago", "intl": "international", "inst": "institute",
    "tech": "technology", "a&m": "a m", "&": "and",
}
STOP = {"of", "the", "at", "and", "in", "campus", "main", "-"}
# generic words count for less when they are the only difference
GENERIC = {"university", "college", "state"}


def tokens(name: str) -> list[str]:
    s = name.lower().replace("’", "'")
    s = re.sub(r"\(([a-z.\s]+)\)", r" \1 ", s)      # Pacific (OR) -> pacific or
    s = re.sub(r"[-/,]", " ", s)
    out: list[str] = []
    for w in s.split():
        w2 = ABBREV.get(w, ABBREV.get(w.rstrip("."), w.rstrip(".")))
        out.extend(w2.split())
    return [w for w in out if w not in STOP and w]


def similarity(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    sa, sb = set(ta), set(tb)
    # every team token found in the institution name scores well, so
    # "Drake" matches "Drake University" but not "Drake State Community".
    w = lambda ws: sum(0.3 if t in GENERIC else 1.0 for t in ws)
    cover = w(sa & sb) / w(sa)
    extra = w(sb - sa) / max(w(sb), 1e-9)
    seq = SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio()
    return 0.6 * cover + 0.4 * seq - 0.3 * extra


def domain(url: str) -> str:
    import tldextract
    if not url:
        return ""
    ext = tldextract.extract(url)
    return f"{ext.domain}.{ext.suffix}".lower() if ext.suffix else ""


def load_ipeds(all_levels: bool = False) -> pd.DataFrame:
    h = pd.read_csv(RAW_GEO / "HD2023.csv", dtype=str, keep_default_na=False,
                    encoding="utf-8-sig", encoding_errors="replace")
    # four-year degree-granting institutions only (ICLEVEL 1)
    if not all_levels:
        h = h[(h.ICLEVEL == "1") | h.INSTNM.str.contains("College", case=False)]
    h = h.assign(web_domain=h.WEBADDR.map(domain))
    return h[["UNITID", "INSTNM", "IALIAS", "CITY", "STABBR", "LATITUDE",
              "LONGITUD", "web_domain"]]


def match_teams(threshold: float = 0.55) -> pd.DataFrame:
    teams = pd.read_csv(REFERENCE / "teams_canonical.csv", dtype=str,
                        keep_default_na=False)
    urls = pd.read_csv(ROOT / "data" / "teams.csv", dtype=str,
                       keep_default_na=False)[["ncaa_id", "url"]]
    teams = teams.merge(urls, on="ncaa_id", how="left").fillna("")
    teams["ath_domain"] = teams.url.map(domain)
    ip = load_ipeds()
    by_domain = ip[ip.web_domain != ""].groupby("web_domain")

    rows = []
    for t in teams.itertuples():
        cands = []
        if t.ath_domain and t.ath_domain in by_domain.groups:
            for r in by_domain.get_group(t.ath_domain).itertuples():
                cands.append((2.0 + (r.STABBR == t.team_state), "domain", r))
        if not cands:
            for r in ip.itertuples():
                # IPEDS aliases are too noisy ("NC" for an Illinois college)
                s = similarity(t.team_name, r.INSTNM)
                if r.STABBR == t.team_state:
                    s += 0.12
                if s >= threshold:
                    cands.append((s, "name", r))
        cands.sort(key=lambda c: (-c[0], c[2].UNITID))
        if cands:
            score, method, r = cands[0]
            runner = cands[1][0] if len(cands) > 1 else 0.0
            rows.append(dict(ncaa_id=t.ncaa_id, team_name=t.team_name,
                             team_state=t.team_state, unitid=r.UNITID,
                             ipeds_name=r.INSTNM, ipeds_city=r.CITY,
                             ipeds_state=r.STABBR, lat=float(r.LATITUDE),
                             lon=float(r.LONGITUD), match_method=method,
                             match_score=round(score, 3),
                             runner_up=round(runner, 3)))
        else:
            rows.append(dict(ncaa_id=t.ncaa_id, team_name=t.team_name,
                             team_state=t.team_state, match_method="unmatched"))
    out = pd.DataFrame(rows)
    return apply_overrides(out)


def apply_overrides(df: pd.DataFrame) -> pd.DataFrame:
    """Reviewed overrides: an exact IPEDS name + state, or explicit
    coordinates for closed/merged/Canadian schools. Fails loudly when a
    named institution is not found exactly once."""
    path = GEO / "team_location_overrides.csv"
    if not path.exists():
        return df
    ov = pd.read_csv(path, dtype=str, keep_default_na=False)
    ip = load_ipeds(all_levels=True)
    df = df.astype({"lat": float, "lon": float, "match_score": float},
                   errors="ignore").set_index("ncaa_id")
    for c in ("lat", "lon", "match_score"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    cols = ["unitid", "ipeds_name", "ipeds_city", "ipeds_state", "lat", "lon",
            "match_method", "match_score"]
    for o in ov.itertuples():
        if o.ipeds_name:
            hit = ip[(ip.INSTNM == o.ipeds_name) & (ip.STABBR == o.ipeds_state)]
            if len(hit) != 1:
                raise ValueError(f"override {o.ncaa_id}: {o.ipeds_name!r} "
                                 f"({o.ipeds_state}) matched {len(hit)} rows")
            r = hit.iloc[0]
            vals = [r.UNITID, r.INSTNM, r.CITY, r.STABBR, float(r.LATITUDE),
                    float(r.LONGITUD), "reviewed", 9.0]
        else:
            vals = ["", o.note.split(";")[0], "", "", float(o.lat), float(o.lon),
                    "reviewed_coords", 9.0]
        df.loc[o.ncaa_id, cols] = vals
    return df.reset_index()
