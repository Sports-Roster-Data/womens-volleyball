#!/usr/bin/env python3
"""Classify each team's live /api/livestats endpoint for the games pipeline.

Teams that saved no game JSON for a season fall into two very different
buckets (see the 2021-22 run: 957 outside-season games, 447 failures):

- slug-ignoring echo sites: discovery serves the current season under any
  slug, the date guard rejects the fetches; harmless, and the endpoint
  itself is alive (classification: echo/ok);
- sites whose /api/livestats is gone entirely (newer Sidearm builds return
  the site's 404 page): every game fetch fails forever; the games pipeline
  has no source for them at all (classification: no_livestats), so they
  should be flagged in data/teams.json ("no_livestats": true) and excluded
  from runs (cmd_games skips them unless chosen with -teams).
- WAF challenges (403/429/202/tiny bodies) are transient (see
  [[scrape-failures-transient-waf]]); a few retries settle them.

A real game id is needed for a meaningful probe (a bogus id gets a 404 on
healthy sites too). Sources, in order: boxscore ids discovered on the
site's stats/schedule pages (requests, then Playwright), then any id already
saved in the game-data archive for the team, else the endpoint is
'unverifiable' and never flagged.

Usage:
  uv run python scripts/probe_livestats.py -s 2021-22          # report only
  uv run python scripts/probe_livestats.py -s 2021-22 --apply  # flag teams
"""

import argparse
import csv
import json
import requests
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from vb import games as vb_games  # noqa: E402

HEADERS = {'User-agent': 'Mozilla/5.0'}
TIMEOUT = 30
RETRIES = 3
RETRY_SLEEP = 5
TINY_BODY = 300  # below this, a non-JSON body is a challenge stub


def find_team_universe(teams, data_dir, season):
    """Teams with no saved game JSON for the season (the ones that failed)."""
    ids_with_files = set()
    for d in data_dir.iterdir():
        try:
            ncaa_id = int(d.name.split('-')[0])
        except ValueError:
            continue
        season_dir = d / season
        if season_dir.is_dir() and any(season_dir.glob('*.json')):
            ids_with_files.add(ncaa_id)
    return [t for t in teams if t.get('url') and int(t['ncaa_id']) not in ids_with_files]


def archived_game_id(data_dir, ncaa_id):
    """A real game id for this team from any season already in the archive."""
    for d in sorted((data_dir).glob(f"{ncaa_id}-*")):
        if not d.is_dir():
            continue
        jsons = sorted(d.glob('*/*.json'))
        if jsons:
            return jsons[0].stem
    return None


def probe_endpoint(domain, game_id):
    """(classification, detail) for one livestats request, with WAF retries."""
    url = f"https://{domain}/api/livestats?game_id={game_id}&detail=full"
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException as e:
            return 'unreachable', f"{type(e).__name__}: {e}"
        body = r.text or ''
        try:
            json.loads(body)
        except ValueError:
            pass
        else:
            if r.status_code == 200:
                return 'ok', f"{r.status_code} json"
        if r.status_code == 404:
            return 'no_livestats', f"{r.status_code} ({len(body)} bytes)"
        if len(body) < TINY_BODY:
            kind = 'challenge' if r.status_code in (200, 202, 403, 429) \
                else 'unverifiable'
            return kind, f"{r.status_code} {len(body)}b"
        if r.status_code in (403, 429):
            if attempt < RETRIES:
                time.sleep(RETRY_SLEEP)
                continue
        return 'unverifiable', f"{r.status_code} {len(body)}b"
    return 'challenge', 'exhausted retries'


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('-s', '--season', required=True,
                    help='Season whose zero-file teams to probe')
    ap.add_argument('--teams-file', default=None)
    ap.add_argument('--data-dir', default=None, metavar='DIR')
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--apply', action='store_true',
                    help='Set "no_livestats": true in data/teams.json for '
                         'teams classified no_livestats (backup saved first)')
    ap.add_argument('--report', default=None,
                    help='CSV report path (default: data/outcomes/'
                         'livestats_probe_<season>_<stamp>.csv)')
    args = ap.parse_args(argv)

    label = vb_games.canonical_season_label(args.season)
    season = _validated(label)
    teams_file = Path(args.teams_file or REPO_ROOT / 'data' / 'teams.json')
    teams = json.loads(teams_file.read_text())
    data_dir = vb_games.resolve_game_data_dir(args.data_dir)

    universe = find_team_universe(teams, data_dir, season)
    print(f"probing {len(universe)} teams "
          f"(no {season} files in {_short(data_dir)})", file=sys.stderr)

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def classify(team, ids, method):
        """Row for one team: probe the endpoint with the best game id found."""
        ncaa_id = int(team['ncaa_id'])
        domain = urlparse(team['url']).netloc
        gid, source = (ids[0], f'discovery:{method}') if ids else (None, '')
        if gid is None:
            gid = archived_game_id(data_dir, ncaa_id)
            if gid:
                source = 'archive'
        # No id at all: cannot verify, and a probe without a real game id
        # never justifies flagging (bogus ids 404 on healthy sites too).
        if gid is not None:
            klass, detail = probe_endpoint(domain, gid)
        else:
            klass, detail = 'unverifiable', 'no game id (discovery + archive empty)'
        return {'season': season, 'ncaa_id': ncaa_id, 'team': team['team'],
                'domain': domain, 'class': klass, 'detail': detail,
                'probe_gid': gid or '', 'gid_source': source,
                'discovered': len(ids or []), 'discovery_method': method}

    def requests_discovery(team):
        try:
            ids, method = scraper._requests_discovery(team, season)
        except Exception as e:  # _NeedsBrowser or an unreachable site
            ids, method = [], f'needs_browser:{type(e).__name__}'
        return team, ids, method

    rows = []
    pending_browser = []
    with vb_games.GameScraper(data_dir=data_dir) as scraper, \
            ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        # Requests-only discovery parallelizes fine; the shared browser
        # does not (sync Playwright), so that queue runs sequentially after.
        for i, fut in enumerate(as_completed(
                [pool.submit(requests_discovery, t) for t in universe]), 1):
            team, ids, method = fut.result()
            if ids:
                row = classify(team, ids, method)
                rows.append(row)
                print(f"[{i}/{len(universe)}] {row['domain']:40s} "
                      f"{row['class']:13s} {row['detail']}", file=sys.stderr)
            else:
                pending_browser.append(team)
        for i, team in enumerate(pending_browser, 1):
            try:
                ids, method = scraper.discover(team, season)
            except Exception:
                ids, method = [], 'browser:failed'
            row = classify(team, ids, method)
            rows.append(row)
            print(f"[browser {i}/{len(pending_browser)}] {row['domain']:40s} "
                  f"{row['class']:13s} {row['detail']}", file=sys.stderr)

    rows.sort(key=lambda r: (r['team'].casefold(), r['ncaa_id']))

    report = Path(args.report) if args.report else (
        REPO_ROOT / 'data' / 'outcomes'
        / f"livestats_probe_{season}_{datetime.now().strftime('%Y%m%dT%H%M%S')}.csv")
    report.parent.mkdir(parents=True, exist_ok=True)
    with open(report, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    summary = {}
    for r in rows:
        summary[r['class']] = summary.get(r['class'], 0) + 1
    print("classification:", file=sys.stderr)
    for klass, n in sorted(summary.items(), key=lambda kv: -kv[1]):
        print(f"  {klass:13s}{n:5d}", file=sys.stderr)
    print(f"report: {report}", file=sys.stderr)

    if args.apply:
        flagged = {r['ncaa_id'] for r in rows if r['class'] == 'no_livestats'}
        if not flagged:
            print("nothing to apply", file=sys.stderr)
            return 0
        stamp = datetime.now().strftime('%Y%m%dT%H%M%S')
        backup = teams_file.with_suffix(f".backup-{stamp}.json")
        backup.write_text(teams_file.read_text())
        for t in teams:
            if int(t['ncaa_id']) in flagged:
                t['no_livestats'] = True
        teams_file.write_text(json.dumps(teams, indent=2) + "\n")
        print(f"flagged {len(flagged)} teams in {teams_file} "
              f"(backup: {backup.name})", file=sys.stderr)
    return 0


def _validated(label):
    vb_games.validate_season(label)
    return label


def _short(p):
    try:
        return str(Path(p).relative_to(REPO_ROOT))
    except ValueError:
        return str(p)


if __name__ == "__main__":
    sys.exit(main())
