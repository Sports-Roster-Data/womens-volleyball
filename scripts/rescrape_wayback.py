#!/usr/bin/env python3
"""Rescrape one team's season file from Wayback Machine captures.

For sites that echo the current roster on every season URL and 404 on the
real per-season pages, the Wayback Machine's period captures of the roster
page are the only live-side recovery source. This script:

1. queries the Wayback CDX API for captures of the roster page,
2. picks the best in-season capture per requested season (preferred months
   first, e.g. November of the fall year),
3. parses it with the repo's own layout parsers,
4. sanity-checks the result (at least `--min-players`, and NOT identical to
   the current-echo roster), then
5. writes it through `src.rosters.write_rosters_csv` with replace semantics,
   so the previous raw vintage is archived verbatim.

This must be followed by register_snapshot.py (-season), remap_corrections.py,
and a rebuild, like any rescrape.
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import rosters  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402


CDX = ("http://web.archive.org/cdx/search/cdx?url={host}{path}"
       "&output=text&fl=timestamp,statuscode&filter=statuscode:200")


def cdx_captures(roster_url: str):
    host = roster_url.split("://", 1)[1].split("/", 1)[0]
    path = "/" + roster_url.split("://", 1)[1].split("/", 1)[1].lstrip("/")
    url = CDX.format(host=host, path=path.rstrip("/"))
    with urllib.request.urlopen(url, timeout=60) as resp:
        out = []
        for line in resp.read().decode().splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "200" and not parts[0].startswith("2026"):
                out.append(parts[0])
    return sorted(set(out))


def pick_capture(timestamps, season_label, preferred_months):
    """Best capture timestamp for a season label ('2015-16' -> fall 2015)."""
    fall_year = int(season_label.split("-")[0])
    season_ts = [t for t in timestamps if t.startswith(str(fall_year))]
    # Late-month snapshots can already reflect the NEXT season's page when the
    # site pre-published in, say, June; stay in the academic window.
    window = season_ts or [t for t in timestamps if t.startswith(str(fall_year - 1))]
    for pref in preferred_months:
        matches = [t for t in window if len(t) >= 6 and t[4:6] in pref]
        if matches:
            return matches[-1]
    return window[-1] if window else None


def fetch_capture(ts: str, roster_url: str) -> BeautifulSoup:
    url = f"http://web.archive.org/web/{ts}id_/{roster_url}"
    with urllib.request.urlopen(url, timeout=90) as resp:
        return BeautifulSoup(resp.read().decode("utf-8", "replace"), "html.parser")


def roster_key(rows):
    return tuple(sorted((r[3], r[4]) for r in rows))  # (name, year)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--team-id", required=True)
    ap.add_argument("--team-name", required=True)
    ap.add_argument("--roster-url", required=True)
    ap.add_argument("--seasons", nargs="+", required=True)
    ap.add_argument("--min-players", type=int, default=10)
    ap.add_argument("--preferred-months", nargs="*",
                    default=["10", "11", "12", "09", "01", "02"],
                    help="Preferred 2-digit snapshot months, in order")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # Current-echo roster: the live season file's rows for this team
    season_files = {}
    team_cfg = {"ncaa_id": args.team_id, "team": args.team_name, "url": args.roster_url}
    print(f"Querying CDX for {args.roster_url} ...")
    captures = cdx_captures(args.roster_url)
    print(f"  {len(captures)} captures")

    for season_label in args.seasons:
        raw_path = Path("data") / f"rosters_{season_label}.csv"
        echo = []
        import csv
        if raw_path.exists():
            with raw_path.open(newline="") as f:
                echo = [r for r in csv.reader(f) if r and r[0] == args.team_id]
        echo_key = roster_key([e for e in echo]) if echo else None

        ts = pick_capture(captures, season_label, args.preferred_months)
        if ts is None:
            print(f"{season_label}: NO capture found — skipping")
            continue
        soup = fetch_capture(ts, args.roster_url)
        roster = rosters._best_parse(soup, team_cfg, season_label)
        n = len(roster) if roster else 0
        status = []
        if n < args.min_players:
            status.append(f"too few players ({n})")
        rows = [rosters.player_to_row(p, season_label) for p in (roster or [])]
        if echo_key is not None and roster_key(rows) == echo_key:
            status.append("IDENTICAL to current-echo roster")
        tag = "OK" if not status else "; ".join(status)
        print(f"{season_label}: capture {ts} -> {n} players [{tag}]")
        if status or args.dry_run:
            continue
        if not echo:  # team not in file at all
            print(f"{season_label}: team missing from {raw_path}; refusing to append")
            continue
        rosters.write_rosters_csv(str(raw_path), rows, {args.team_id})
        print(f"{season_label}: wrote {len(rows)} rows to {raw_path}")


if __name__ == "__main__":
    main()
