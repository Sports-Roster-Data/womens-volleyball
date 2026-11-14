"""Link roster rows into people.

Stage 1 (within a team): rows on the same team in different seasons join
when they share a nonblank player_id, a normalized profile URL, or an exact
name_key. Name joins are skipped whenever either season has two rows with
that name on that team (the build's reviewed keep_distinct pairs), so a
same-name pair is never collapsed by name alone. The result is a "stint".

Stage 2 (across teams): stints with the same name_key on different teams,
where the later stint starts after the earlier one ends with at most
MAX_GAP unobserved seasons between them, are scored:

  confirmed  previous_school_ncaa_id on the later stint names the earlier
             team, or high school AND hometown both match
  probable   hometown matches, class year does not go backward, and height
             is within 2 inches (or missing on one side)
  possible   anything weaker (e.g. same state only) -> review file only

A stint with more than one candidate at its best tier is demoted to
possible; a previous_school_ncaa_id match ranks above every other kind of
evidence when breaking such ties. Confirmed and probable links are unioned into one person_id.
"""
from __future__ import annotations

import re
from collections import defaultdict

import pandas as pd

from .load import seasons as all_seasons

MAX_GAP = 2  # seasons allowed between the end of one stint and the next
MAX_TENURE = 6  # 4 + redshirt + COVID year
YEAR_RANK = {"Freshman": 1, "Sophomore": 2, "Junior": 3, "Senior": 4,
             "Graduate Student": 5, "Fifth Year": 5, "Sixth Year": 6}
TIER_RANK = {"confirmed": 3, "probable": 2, "possible": 1}


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # deterministic: the smaller id becomes the root
            if rb < ra:
                ra, rb = rb, ra
            self.parent[rb] = ra


def norm_url(u: str) -> str:
    u = u.strip().lower()
    u = re.sub(r"^(https?:)?//", "", u)
    u = re.sub(r"^www\.", "", u)
    u = re.sub(r"[?#].*$", "", u).rstrip("/")
    # a bare path without a numeric bio id is just the player's name slug,
    # which adds nothing over name_key
    return u if re.search(r"/\d+$", u) else ""


def norm_text(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\b(high school|h\.?s\.?|school|academy)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def norm_hs(s: str) -> str:
    """High school as space-separated words, without generic words or the
    previous-college notes some rosters append: 'Westfield (Loyola
    Maryland)' and 'Reagan HS / Lehigh' -> 'westfield', 'reagan'."""
    s = re.split(r"\s*/\s*|\s*\(", s, maxsplit=1)[0].lower()
    s = re.sub(r"\b(high school|h\.?s\.?|school|academy)\b", " ", s)
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", s).split())


def norm_home(city_state: str, state: str, country: str) -> str:
    """'Fullerton, CA' and 'Fullerton, Calif' -> 'fullerton|CA'."""
    city = city_state.split(",")[0]
    where = state or (country if country not in ("", "USA") else "")
    c = norm_text(city)
    return f"{c}|{where}" if c else ""


def hs_match(a: str, b: str) -> bool:
    """Equal, or one is a whole-word prefix of the other ('westfield' vs
    'westfield senior'), never a partial word ('allen' vs 'allendale')."""
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = sorted((a.split(), b.split()), key=len)
    return len(" ".join(short)) >= 5 and long_[:len(short)] == short


def season_index() -> dict[str, int]:
    return {s: i for i, s in enumerate(all_seasons())}


def build_stints(r: pd.DataFrame) -> pd.Series:
    """Return stint id (a source_row_id) for each row index."""
    uf = UnionFind()
    for rid in r.source_row_id:
        uf.find(rid)
    r = r.assign(_url=r.profile_url.map(norm_url))
    for col in ("player_id", "_url"):
        sub = r[r[col] != ""]
        for _, g in sub.groupby(["ncaa_id", col], sort=False):
            if g.season.nunique() == len(g):      # never two rows in one season
                ids = sorted(g.source_row_id)
                for x in ids[1:]:
                    uf.union(ids[0], x)
    # Name edges only when the name is unique on that team in each season,
    # and only between consecutive appearances: no more than MAX_GAP
    # observed team seasons apart and class year not going backward
    # (Senior -> Freshman is a different person with the same name).
    sidx = season_index()
    team_obs: dict[str, list[int]] = defaultdict(list)
    for t, s in r[["ncaa_id", "season"]].drop_duplicates().itertuples(index=False):
        team_obs[t].append(sidx[s])
    counts = r.groupby(["ncaa_id", "season", "name_key"]).size()
    dup = set(counts[counts > 1].index)
    named = r[r.name_key != ""].assign(si=lambda d: d.season.map(sidx),
                                       yr=lambda d: d.year_clean.map(YEAR_RANK))
    for (team, key), g in named.groupby(["ncaa_id", "name_key"], sort=False):
        g = g[[(team, s, key) not in dup for s in g.season]].sort_values("si")
        rows = list(g.itertuples())
        obs = team_obs[team]
        for a, b in zip(rows, rows[1:]):
            gap = sum(1 for si in obs if a.si < si < b.si)
            back = (pd.notna(a.yr) and pd.notna(b.yr) and b.yr < a.yr)
            if gap <= MAX_GAP and not back:
                uf.union(a.source_row_id, b.source_row_id)
    stint = r.source_row_id.map(uf.find)
    # safety: a stint must not hold two rows from one season; split any that
    # do back to single rows (can only arise from conflicting id/url edges)
    bad = (r.assign(_s=stint).groupby("_s").season
           .agg(lambda s: s.duplicated().any()))
    bad = set(bad[bad].index)
    return stint.where(~stint.isin(bad), r.source_row_id)


def stint_table(r: pd.DataFrame, sidx: dict[str, int]) -> pd.DataFrame:
    r = r.assign(_si=r.season.map(sidx),
                 _yr=r.year_clean.map(YEAR_RANK),
                 _ht=pd.to_numeric(r.total_inches, errors="coerce"),
                 _hs=r.high_school_clean.map(norm_hs),
                 _home=[norm_home(h, st, co) for h, st, co in
                        zip(r.hometown_clean, r.state, r.country)])
    r = r.sort_values(["stint", "_si"])

    def first_nonblank(s: pd.Series) -> str:
        s = s[s != ""]
        return s.iloc[0] if len(s) else ""

    g = r.groupby("stint", sort=True)
    st = pd.DataFrame({
        "ncaa_id": g.ncaa_id.first(),
        "team": g.team.first(),
        "division": g.division.first(),
        "name_key": g.name_key.first(),
        "first_si": g._si.min(),
        "last_si": g._si.max(),
        "n_seasons": g._si.nunique(),
        "first_year": g._yr.first(),
        "last_year": g._yr.last(),
        "height": g._ht.median(),
        "hs": g._hs.agg(first_nonblank),
        "home": g._home.agg(first_nonblank),
        "home_state": g.state.agg(first_nonblank),
        "prev_ncaa": g.previous_school_ncaa_id.agg(
            lambda s: "|".join(sorted(set(s) - {""}))),
    })
    return st


def score_pair(a: pd.Series, b: pd.Series, team_seasons: dict) -> tuple[str, str]:
    """a ends before b starts. Returns (tier, evidence) or ('', '')."""
    gap = sum(1 for si in range(a.last_si + 1, b.first_si)
              if si in team_seasons.get(b.ncaa_id, set()))
    if b.first_si <= a.last_si or gap > MAX_GAP:
        return "", ""
    if a.ncaa_id in b.prev_ncaa.split("|"):
        return "confirmed", "previous_school_ncaa_id"
    hs_same = hs_match(a.hs, b.hs)
    home_match = a.home != "" and a.home == b.home
    if hs_same and home_match:
        return "confirmed", "high_school+hometown"
    class_ok = (pd.isna(a.last_year) or pd.isna(b.first_year)
                or b.first_year >= a.last_year)
    height_ok = (pd.isna(a.height) or pd.isna(b.height)
                 or abs(a.height - b.height) <= 2)
    if home_match and class_ok and height_ok:
        return "probable", "hometown"
    if hs_same and class_ok and height_ok:
        return "probable", "high_school"
    if a.home_state != "" and a.home_state == b.home_state and class_ok:
        return "possible", "state"
    return "possible", "name_only"


def link_players(r: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    sidx = season_index()
    r = r.copy()
    r["stint"] = build_stints(r)
    st = stint_table(r, sidx)
    # seasons each team was observed (a gap a team was absent for is free)
    team_seasons: dict[str, set] = defaultdict(set)
    for t, s in r[["ncaa_id", "season"]].drop_duplicates().itertuples(index=False):
        team_seasons[t].add(sidx[s])

    cands = []
    for key, g in st[st.name_key != ""].groupby("name_key", sort=False):
        if g.ncaa_id.nunique() < 2:
            continue
        g = g.sort_values("first_si")
        rows = list(g.itertuples())
        for i, a in enumerate(rows):
            for b in rows:
                if b.ncaa_id == a.ncaa_id or b.first_si <= a.last_si:
                    continue
                tier, ev = score_pair(a, b, team_seasons)
                if tier:
                    cands.append((a.Index, b.Index, tier, ev))
    c = pd.DataFrame(cands, columns=["from_stint", "to_stint", "tier", "evidence"])
    if len(c):
        # the roster's own previous-school statement outranks inferred ties
        c["rank"] = c.tier.map(TIER_RANK) + (c.evidence == "previous_school_ncaa_id")
        # keep each stint's best-tier candidates; >1 at that tier = ambiguous
        best_out = c.groupby("from_stint")["rank"].transform("max")
        best_in = c.groupby("to_stint")["rank"].transform("max")
        c = c[(c["rank"] == best_out) & (c["rank"] == best_in)].copy()
        n_out = c.groupby("from_stint").to_stint.transform("count")
        n_in = c.groupby("to_stint").from_stint.transform("count")
        ambiguous = (n_out > 1) | (n_in > 1)
        c.loc[ambiguous & (c["rank"] >= 2), "evidence"] += "+ambiguous"
        c.loc[ambiguous, "tier"] = "possible"
    accepted = c[c.tier.isin(["confirmed", "probable"])] if len(c) else c

    uf = UnionFind()
    for s in st.index:
        uf.find(s)
    for a, b in zip(accepted.from_stint, accepted.to_stint):
        uf.union(a, b)
    tier_of: dict[str, str] = {}
    for a, b, t in zip(accepted.from_stint, accepted.to_stint, accepted.tier):
        root = uf.find(a)
        prev = tier_of.get(root)
        # a person's tier is their weakest accepted link
        if prev is None or TIER_RANK[t] < TIER_RANK[prev]:
            tier_of[root] = t

    person = r.stint.map(uf.find)
    method = pd.Series("single", index=r.index)
    multi = r.groupby("stint").source_row_id.transform("count") > 1
    method[multi] = "within_team"
    links = pd.DataFrame({
        "source_row_id": r.source_row_id,
        "season": r.season,
        "ncaa_id": r.ncaa_id,
        "stint_id": r.stint,
        "person_id": "P" + person,
        "stint_method": method,
        "link_tier": person.map(lambda p: tier_of.get(p, "single_team")),
    })
    # More than MAX_TENURE seasons on one team is not a playing career
    # (staff listed on the roster page, or a recurring name); flag it so
    # transfers and cohorts can leave it out.
    span = r.groupby("stint").season.transform("nunique")
    links["suspect_tenure"] = (span > MAX_TENURE).values
    links = links.sort_values("source_row_id", kind="stable").reset_index(drop=True)

    review = c[c.tier == "possible"].copy() if len(c) else c
    if len(review):
        review = review.merge(st[["ncaa_id", "team", "name_key", "first_si",
                                  "last_si", "home", "hs"]],
                              left_on="from_stint", right_index=True)
        review = review.merge(st[["ncaa_id", "team", "first_si", "last_si",
                                  "home", "hs"]], left_on="to_stint",
                              right_index=True, suffixes=("_from", "_to"))
        review = review.drop(columns="rank").sort_values(["from_stint", "to_stint"])
    pairs = accepted[["from_stint", "to_stint", "tier", "evidence"]].sort_values(
        ["from_stint", "to_stint"]).reset_index(drop=True)
    return links, pairs, review.reset_index(drop=True)


def stints_for(r: pd.DataFrame, links: pd.DataFrame) -> pd.DataFrame:
    """Stint summary with person ids attached (used by transfers)."""
    sidx = season_index()
    m = r.merge(links[["source_row_id", "stint_id", "person_id", "link_tier"]],
                on="source_row_id")
    m = m.rename(columns={"stint_id": "stint"})
    st = stint_table(m, sidx)
    p = m.drop_duplicates("stint").set_index("stint")[["person_id", "link_tier"]]
    return st.join(p)
