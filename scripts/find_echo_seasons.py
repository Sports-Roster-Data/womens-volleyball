#!/usr/bin/env python3
"""Cross-season sweep for site-echo contamination.

Modern athletics-site JS apps render the current roster for any
/roster/<season> URL, ignoring the season slug. A build that hits one of
those while sweeping historical seasons stores the *current* roster under
every label, producing consecutive seasons whose (name, year) rosters are
byte-for-byte identical — something a real roster never is, because class
years advance every season.

This audits data/clean/vb_rosters_<season>.csv two ways:

Identical-run census
    per team, per season, compare the roster with the previous season's;
    flag every season identical to its predecessor. Identical rosters are
    never legitimate: class years advance every non-redshirt season.

Chronology check (tiered; identity by profile_url)
    The person key is (team, profile_url) — stable per player and exactly
    what echo rows share with the current page. Rows without any URL fall
    back to (team, name), and only rows that ALL lack URLs are compared by
    name, so two different people who merely share a name across a decade
    never collide. Class ranks: Freshman 1, Sophomore 2, Junior 3, Senior 4,
    Graduate Student/Fifth Year 5, Sixth Year 6; blank labels are skipped
    (some sites never publish class years).

    Tier `impossible`:
    * a class drop of two or more ranks between ranked observations of the
      same person — no career ever goes backwards;
    * `Freshman` observed three or more seasons apart (a redshirt explains
      a one-season repeat, never a multi-season one);
    * the same grade-2/3/4 class observed five or more seasons apart.
    Tier `suspect` (review):
    * a one-rank drop spanning four or more seasons where neither endpoint
      carries a redshirt flag — most often a site mislabel or a medical
      reclassification, occasionally an echo.
"""
import csv
import sys
from collections import defaultdict
from pathlib import Path

SEASONS = [f"{y}-{str((y + 1) % 100).zfill(2)}" for y in range(2015, 2027)]
RANK = {"Freshman": 1, "Sophomore": 2, "Junior": 3, "Senior": 4,
        "Graduate Student": 5, "Fifth Year": 5, "Sixth Year": 6}
RANK_N = {v: k for k, v in RANK.items()}
RANK_N[5] = "Grad/Fifth"


def rosters_by_team(season):
    """ncaa_id -> {(name, year_clean, redshirt, profile_url)} for one season."""
    path = Path("data/clean") / f"vb_rosters_{season}.csv"
    if not path.exists():
        return {}
    by_team = defaultdict(set)
    for r in csv.DictReader(path.open()):
        tid = str(r["ncaa_id"]).strip()
        if not tid or not r.get("name"):
            continue
        by_team[tid].add((r["name"].strip(),
                          str(r.get("year_clean") or "").strip(),
                          str(r.get("redshirt") or "").strip().upper(),
                          str(r.get("profile_url") or "").strip()))
    return by_team


def team_name(tid):
    try:
        with open("data/reference/teams_canonical.csv") as f:
            for r in csv.DictReader(f):
                if r["ncaa_id"] == tid:
                    return r["team_name"]
    except FileNotFoundError:
        pass
    return ""


def series_violations(tid, series_key, display, obs):
    """Class-rank impossibility checks over one person's ranked seasons."""
    findings = []

    def add(tier, pair, note):
        findings.append((tid, tier, display, pair, note))

    max_so_far, prev_high = None, None
    for idx, rank, redshirt, season in obs:
        if max_so_far is None:
            max_so_far, prev_high = rank, (idx, rank, redshirt, season)
            continue
        gap = idx - prev_high[0]
        drop = max_so_far - rank
        pair = f"{prev_high[3]}={RANK_N[max_so_far]} -> {season}={RANK_N[rank]}"
        if drop >= 2:
            add("impossible", pair, f"class drops {drop} ranks")
        elif drop == 1 and gap >= 4 and redshirt != "TRUE" and prev_high[2] != "TRUE":
            add("suspect", pair, f"one-rank drop after {gap} seasons, no redshirt")
        if rank >= max_so_far:
            max_so_far, prev_high = rank, (idx, rank, redshirt, season)
    fr = [o for o in obs if o[1] == 1]
    if len(fr) >= 2 and fr[-1][0] - fr[0][0] >= 3:
        add("impossible", f"{fr[0][3]}=Freshman -> {fr[-1][3]}=Freshman",
            f"two freshman seasons {fr[-1][0] - fr[0][0]} seasons apart")
    for rank in (2, 3, 4):
        same = [o for o in obs if o[1] == rank]
        if len(same) >= 2 and same[-1][0] - same[0][0] >= 5:
            add("suspect", f"{same[0][3]}={RANK_N[rank]} -> {same[-1][3]}={RANK_N[rank]}",
                "same class five or more seasons apart")
    return findings


def chronology_violations(seasons_data):
    """Findings keyed by (team, profile_url), name-fallback for URL-less rows."""
    by_identity = defaultdict(list)
    for idx, season in enumerate(SEASONS):
        for tid, players in seasons_data[season].items():
            for name, year_clean, redshirt, url in players:
                rank = RANK.get(year_clean)
                if rank:
                    key = (tid, url) if url else (tid, "name:" + name)
                    by_identity[key].append((idx, rank, redshirt, season))

    findings = []
    for (tid, identity), obs in by_identity.items():
        obs.sort()
        if identity.startswith("name:"):
            display = identity[len("name:"):]
        else:
            display = identity.rstrip('/').split('/')[-1] or identity
        findings.extend(series_violations(tid, identity, display, obs))
    return findings


def main():
    runs = defaultdict(list)  # ncaa_id -> list of (start_season, size)
    teams = {}
    prev = None
    seasons_data = {}
    for season in SEASONS:
        cur = rosters_by_team(season)
        seasons_data[season] = cur
        teams.update(cur)
        if prev is not None:
            prev_sets = {tid: {(n, y) for n, y, _, _ in rows} for tid, rows in prev.items()}
            cur_sets = {tid: {(n, y) for n, y, _, _ in rows} for tid, rows in cur.items()}
            for tid, roster in cur_sets.items():
                if tid in prev_sets and prev_sets[tid] and prev_sets[tid] == roster:
                    runs[tid].append((season, len(roster)))
        prev = cur

    print(f"{'ncaa':>5}  identical-consecutive seasons (start season shown)")
    flagged = 0
    for tid in sorted(teams, key=lambda t: (-len(runs.get(t, [])), int(t))):
        tid_runs = runs.get(tid, [])
        if not tid_runs:
            continue
        flagged += 1
        print(f"{tid:>5}  {team_name(tid):28} " + ", ".join(f"{s} ({n}p)" for s, n in tid_runs))
    print()
    print(f"teams flagged: {flagged} of {len(teams)} with data")

    findings = chronology_violations(seasons_data)
    out = Path("data/derived/audits/echo_sweep.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    by_tier = defaultdict(int)
    teams_hit = set()
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ncaa_id", "team", "tier", "player", "class_pair", "note"])
        for tid, tier, display, pair, note in sorted(findings, key=lambda x: (int(x[0]), x[1], x[2])):
            by_tier[tier] += 1
            teams_hit.add(tid)
            w.writerow([tid, team_name(tid), tier, display, pair, note])
    print(f"chronology findings: {dict(by_tier)} across {len(teams_hit)} teams "
          f"(rows written to {out})")


if __name__ == "__main__":
    sys.exit(main())
