#!/usr/bin/env python3
"""Cross-season sweep for site-echo contamination.

Modern athletics-site JS apps render the current roster for any
/roster/<season> URL, ignoring the season slug. A build that hits one of
those while sweeping historical seasons stores the *current* roster under
every label, producing consecutive seasons whose (name, year) rosters are
byte-for-byte identical — something a real roster never is, because class
years advance every season.

This audits data/clean/vb_rosters_<season>.csv for exactly that signature:
per team, per season, compare the roster with the previous season's; flag
every season identical to its predecessor and report maximal identical
runs. One-season runs are reported too so borderline teams can be eyeballed.
"""
import csv
import sys
from collections import defaultdict
from pathlib import Path

SEASONS = [f"{y}-{str((y + 1) % 100).zfill(2)}" for y in range(2015, 2027)]


def rosters_by_team(season):
    """ncaa_id -> frozenset of (name year_clean) for one season's clean file."""
    path = Path("data/clean") / f"vb_rosters_{season}.csv"
    if not path.exists():
        return {}
    by_team = defaultdict(set)
    for r in csv.DictReader(path.open()):
        tid = str(r["ncaa_id"]).strip()
        if not tid or not r.get("name"):
            continue
        by_team[tid].add((r["name"].strip(), str(r.get("year_clean") or "").strip()))
    return {k: frozenset(v) for k, v in by_team.items()}


def main():
    runs = defaultdict(list)  # ncaa_id -> list of (start_season, end_season, size)
    teams = {}
    prev = None
    seasons_data = {}
    for season in SEASONS:
        cur = rosters_by_team(season)
        seasons_data[season] = cur
        teams.update(cur)
        if prev is not None:
            for tid, roster in cur.items():
                if tid in prev and prev[tid] and prev[tid] == roster:
                    runs[tid].append((season, len(roster)))
        prev = cur

    # Chronology signature: a concrete (name, year) that appears both in the
    # latest season and in a season at least three years earlier. Class years
    # advance every season (redshirts aside), so this cannot happen for real
    # and catches echo rows that no longer form exact-identical runs.
    echo_rows = defaultdict(set)
    LAST = SEASONS[-1]
    early = SEASONS[:-3]
    for tid in teams:
        latest = seasons_data[LAST].get(tid, set())
        for season in early:
            for key in seasons_data[season].get(tid, set()) & latest:
                echo_rows[tid].add((season, key))
        for season in ('2024-25', '2025-26'):
            for key in seasons_data[season].get(tid, set()) & latest:
                echo_rows[tid].add((season, key))

    print(f"{'ncaa':>5}  identical-consecutive seasons (start season shown)")
    flagged = []
    for tid in sorted(teams, key=lambda t: (len(runs.get(t, [])), int(t) if t.isdigit() else 0),
                      reverse=True):
        tid_runs = runs.get(tid, [])
        if not tid_runs:
            continue
        flagged.append((tid, tid_runs))
    for tid, tid_runs in flagged:
        name = ""
        try:
            with open("data/reference/teams_canonical.csv") as f:
                for r in csv.DictReader(f):
                    if r["ncaa_id"] == tid:
                        name = r["team_name"]
                        break
        except FileNotFoundError:
            pass
        print(f"{tid:>5}  {name:28} " + ", ".join(f"{s} ({n}p)" for s, n in tid_runs))
    print()
    print(f"teams flagged: {len(flagged)} of {len(teams)} with data")

    print()
    print("chronology violations: (name, year) in 2026-27 also in an earlier season")
    out = Path("data/derived/audits/echo_sweep.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    rows_out = []
    for tid, detail in sorted(echo_rows.items(), key=lambda t: int(t[0])):
        if not detail:
            continue
        name = ""
        try:
            with open("data/reference/teams_canonical.csv") as f:
                for r in csv.DictReader(f):
                    if r["ncaa_id"] == tid:
                        name = r["team_name"]
                        break
        except FileNotFoundError:
            pass
        rows_out.append({"ncaa_id": tid, "team": name,
                         "seasons": ", ".join(s for s, _ in sorted(detail)),
                         "players": "; ".join(k[0] for _, k in sorted(detail))})
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ncaa_id", "team", "seasons", "players"])
        w.writeheader()
        w.writerows(rows_out)
    print(f"chronology-suspect teams: {len(rows_out)} (rows written to {out})")


if __name__ == "__main__":
    sys.exit(main())
