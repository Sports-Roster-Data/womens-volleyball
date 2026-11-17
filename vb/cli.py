"""vb command-line interface.

Subcommands: scrape (rosters; wraps src/rosters.py), games (game JSON
downloads; see vb.games), logs (game log CSVs from the volleyball-game-data
archive; see vb.logs), list-teams (no network). Modeled on the wbb CLI:
human status goes to stderr; single-team roster JSON goes to stdout; the
end-of-run summaries always print, even with --quiet.
"""

import argparse
import csv
import json
import logging
import sys
import time
from pathlib import Path

from vb import __version__
from vb import games as vb_games
from vb import logs as vb_logs

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
TEAMS_FILE = REPO_ROOT / "data" / "teams.json"

SUMMARY_WIDTH = 64


def setup_logging(quiet: bool = False, verbose: bool = False):
    """Configure logging: --quiet -> WARNING (wins), --verbose -> DEBUG, else INFO."""
    level = logging.WARNING if quiet else (logging.DEBUG if verbose else logging.INFO)
    logging.basicConfig(level=level, format='%(asctime)s - %(levelname)s - %(message)s')


def _fmt_path(p) -> str:
    """Path relative to cwd when possible, else absolute."""
    path = Path(p)
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _load_rosters():
    """Import src/rosters.py (the existing scraper module) from the repo."""
    src = REPO_ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    import rosters
    return rosters


def _print_summary(title: str, lines, elapsed: float):
    mins, secs = divmod(int(elapsed), 60)
    out = ["-" * SUMMARY_WIDTH, title, *lines,
           f"  elapsed             {mins}m {secs:02d}s"]
    sys.stderr.write("\n".join(out) + "\n")
    sys.stderr.flush()


def cmd_scrape(args) -> int:
    """Rosters: wraps src/rosters.py (single-URL mode and bulk mode).

    Season labels are canonicalized to the full academic year (fall sport,
    end year = start + 1): '-season 2025' scrapes and writes 2025-26.
    """
    rosters = _load_rosters()
    start = time.monotonic()

    # Single team with URL override: fetch + parse, print JSON to stdout.
    if args.url:
        label = rosters.canonical_season_label(args.season)
        team = {'url': args.url, 'ncaa_id': 0, 'team': 'Single Team'}
        html = rosters.fetch_roster(args.url, label)
        if html is None:
            sys.stderr.write(f"Could not fetch roster page: {args.url}\n")
            return 1
        roster = rosters.parse_roster(team, html, label)
        for player in roster:
            print(json.dumps(player.to_dict(), indent=2))
        _print_summary(f"Scrape complete: {label} single team",
                       [f"  players scraped     {len(roster)}"], time.monotonic() - start)
        return 0 if roster else 1

    # Bulk mode. rosters.py uses repo-relative paths (data/teams.json,
    # data/rosters_<label>.csv), so anchor cwd at the repo root.
    import os
    os.chdir(REPO_ROOT)
    try:
        label = rosters.canonical_season_label(args.season)
    except ValueError as e:
        sys.stderr.write(f"{e}\n")
        return 2
    logger.info(f"Using canonical season label: {label}")

    teams_arg = list(args.teams) if args.teams else []
    unparsed, skipped = rosters.get_all_rosters(label, teams_arg, resume=args.resume)
    teams_json = json.loads((REPO_ROOT / "data" / "teams.json").read_text())
    teams_with_urls = [
        t for t in teams_json
        if t.get('url') and t.get('scraper') != 'skip'
        and (args.season not in t.get('skip_seasons', [])
             and label not in t.get('skip_seasons', []))
        and 'roster' not in t
        and (not teams_arg or t['ncaa_id'] in teams_arg)
    ]

    output_file = REPO_ROOT / "data" / f"rosters_{label}.csv"
    rows = len(list(csv.reader(output_file.open(newline='')))) - 1 if output_file.exists() else 0

    lines = [f"Scrape complete: {label}",
             f"  teams attempted     {len(teams_with_urls)}",
             f"  rows in csv         {rows:,}",
             f"  csv                 {_fmt_path(output_file)}"]
    if unparsed:
        lines.append(f"  unparsed teams      {len(unparsed)}")
        lines.append("    " + " ".join(str(i) for i in unparsed))
    if skipped:
        lines.append(f"  skipped teams       {len(skipped)}")
        lines.append("    " + " ".join(str(i) for i in skipped))
    _print_summary(*lines, elapsed=time.monotonic() - start)
    return 0 if not skipped and not unparsed else 1


def cmd_games(args) -> int:
    label = vb_games.canonical_season_label(args.season)
    try:
        vb_games.validate_season(label)
    except ValueError as e:
        sys.stderr.write(f"{e}\n")
        return 2
    teams_file = Path(args.teams_file) if args.teams_file else TEAMS_FILE
    if not teams_file.exists():
        sys.stderr.write(f"Teams file not found: {teams_file}\n")
        return 1
    teams = json.loads(teams_file.read_text())
    team_ids = args.teams
    if team_ids:
        teams = [t for t in teams if int(t['ncaa_id']) in team_ids]
    # Game scraping needs only a url and a name; drop config-only entries.
    teams = [t for t in teams if t.get('url')]
    if args.scraper:
        teams = [t for t in teams if t.get('scraper', 'default') == args.scraper]
    # Sites whose livestats endpoint is gone (newer Sidearm builds) can't
    # serve game JSON at all; every run would just redo their failures unless
    # they're dropped here. An explicit -teams list overrides the flag.
    n_no_livestats = 0
    if not team_ids:
        excluded = [t for t in teams if t.get('no_livestats')]
        n_no_livestats = len(excluded)
        if excluded:
            excluded_ids = {int(t['ncaa_id']) for t in excluded}
            teams = [t for t in teams if int(t['ncaa_id']) not in excluded_ids]
    if not teams:
        sys.stderr.write("No matching teams.\n")
        return 1

    start = time.monotonic()
    enabled = sys.stderr.isatty()
    state = {'count': 0, 'last_len': 0}

    def ephemeral(text):
        pad = ' ' * max(state['last_len'] - len(text), 0)
        state['last_len'] = len(text) + len(pad)
        sys.stderr.write("\r" + text + pad)
        sys.stderr.flush()

    def progress(o):
        if not enabled:
            return
        state['count'] += 1
        prefix = f"[{state['count']:5d}/{len(teams)}]"
        if o.status == 'ok':
            ephemeral(f"{prefix} [{o.saved:3d} games] {o.team} ({o.team_id})")
        else:
            detail = f"ERROR: {o.error}" if o.error else "no games found"
            sys.stderr.write("\n" if state['count'] > 1 else "")
            sys.stderr.write(f"{prefix} {o.team} ({o.team_id}) — {detail}\n")
            state['last_len'] = 0
        sys.stderr.flush()

    def discovery_progress(phase, done, total):
        if not enabled:
            return
        ephemeral(f"[discovery {phase}] {done:5d}/{total}")

    with vb_games.GameScraper(data_dir=args.data_dir, headed=args.headed,
                              skip_existing=args.skip_existing, workers=args.workers,
                              on_team=progress) as scraper:
        scraper.scrape_all(teams, label, on_discovery=discovery_progress)
    if enabled:
        sys.stderr.write("\n")

    results = scraper.results
    saved = sum(o.saved for o in results)
    stale = sum(o.stale for o in results)
    mins, secs = divmod(int(time.monotonic() - start), 60)
    lines = [f"Games complete: {label}",
             f"  teams attempted     {len(results)}",
             f"  teams with games    {sum(1 for o in results if o.found)}",
             f"  games saved         {saved:,}"]
    if stale:
        lines.append(f"  outside season      {stale:,} (wrong-season games, not saved)")
    if args.skip_existing:
        lines.append(f"  games skipped       {sum(o.skipped for o in results):,}")
    failed = sum(o.failed for o in results)
    if failed:
        lines.append(f"  games failed        {failed:,}")
    errors = [o for o in results if o.error]
    if errors:
        lines.append(f"  team errors         {len(errors)}")
    if n_no_livestats:
        lines.append(f"  no-livestats teams  {n_no_livestats} (excluded)")

    # Per-team outcomes: the durable record of what this run did, so retries
    # and post-run diagnosis don't have to reverse-engineer the archive.
    if args.outcomes:
        outcome_file = Path(args.outcomes)
    else:
        stamp = time.strftime('%Y%m%dT%H%M%S')
        outcome_file = (REPO_ROOT / "data" / "outcomes"
                        / f"games_{label}_{stamp}.csv")
    n_rows = vb_games.write_outcome_csv(outcome_file, results, label)
    lines.append(f"  game outcomes       {_fmt_path(outcome_file)} ({n_rows} rows)")
    lines.append(f"  data dir            {_fmt_path(scraper.data_dir)}")
    _print_summary(lines[0], lines[1:], elapsed=time.monotonic() - start)
    return 0 if saved >= 1 else 1


def cmd_logs(args) -> int:
    """Game log CSV from the volleyball-game-data archive, per season+division."""
    label = vb_games.canonical_season_label(args.season)
    try:
        vb_games.validate_season(label)
    except ValueError as e:
        sys.stderr.write(f"{e}\n")
        return 2
    start = time.monotonic()
    try:
        rows, info = vb_logs.build_game_logs(label, args.division,
                                             data_dir=args.data_dir)
    except (ValueError, FileNotFoundError) as e:
        sys.stderr.write(f"{e}\n")
        return 2

    if args.output:
        output_file = Path(args.output)
    else:
        output_file = Path(args.out_dir) / vb_logs.output_name(label, args.division)
    vb_logs.write_game_logs(rows, output_file)

    mins, secs = divmod(int(time.monotonic() - start), 60)
    lines = [f"Game logs complete: {label} {args.division.upper()}",
             f"  teams with games    {info['teams']}",
             f"  files scanned       {info['files_scanned']:,}",
             f"  rows written        {info['rows']:,}"]
    for key, label_name in (
            ('outside_season', 'wrong-season files'),
            ('outside_division', 'files outside division'),
            ('side_unknown', 'rows w/o box score'),
            ('unreadable', 'documents unreadable'),
            ('no_date', 'files without date'),
            ('unparsed', 'files not livestats')):
        value = info.get(key, 0)
        if value:
            lines.append(f"  {label_name}      {value:,}")
    lines.append(f"  elapsed             {mins}m {secs:02d}s")
    lines.append(f"  csv                 {_fmt_path(output_file)}")
    lines.append(f"  data dir            {_fmt_path(info['data_dir'])}")
    _print_summary(lines[0], lines[1:], elapsed=time.monotonic() - start)
    return 0 if info['rows'] >= 1 else 1


def cmd_list_teams(args) -> int:
    """Teams in data/teams.json with their scraper configuration (no network)."""
    teams_file = Path(args.teams_file) if args.teams_file else TEAMS_FILE
    if not teams_file.exists():
        sys.stderr.write(f"Teams file not found: {teams_file}\n")
        return 1
    teams = json.loads(teams_file.read_text())
    rows = [{'ncaa_id': t['ncaa_id'], 'team': t['team'],
             'scraper': t.get('scraper', 'default'),
             'has_url': 'url' in t, 'url': t.get('url', '')} for t in teams]
    if args.no_url:
        rows = [r for r in rows if not r['has_url']]
    if args.type:
        rows = [r for r in rows if r['scraper'] == args.type]
    if args.format == 'json':
        json.dump(rows, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        fieldnames = ['ncaa_id', 'team', 'scraper', 'has_url', 'url']
        writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='vb',
        description=f"NCAA women's volleyball toolkit v{__version__} "
                    "(scrape | games | logs | list-teams)")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--verbose', action='store_true', help='Verbose (DEBUG) logging')
    common.add_argument('--quiet', action='store_true', help='Only warnings and the summary')

    sub = parser.add_subparsers(dest='command', required=True)

    p_scrape = sub.add_parser('scrape', parents=[common],
                              help='Scrape rosters to data/rosters_<season>.csv')
    p_scrape.add_argument('-season', '-s', required=True,
                          help='Season: year-only ("2026") or the full '
                               'academic-year label ("2026-27")')
    p_scrape.add_argument('-teams', nargs='+', type=int,
                          help='Specific team IDs to scrape (space-separated)')
    p_scrape.add_argument('-url', help='Base URL for single-team scraping '
                                       '(roster JSON goes to stdout)')
    p_scrape.add_argument('--resume', action='store_true',
                          help='Reuse cached per-team results from a previous run '
                               'of this season (data/cache/rosters_<label>/) instead '
                               'of re-fetching teams that already succeeded')
    p_scrape.set_defaults(func=cmd_scrape)

    p_games = sub.add_parser('games',
                             help='Download game livestats JSON '
                                  '(default: ../volleyball-game-data)')
    p_games.add_argument('--verbose', action='store_true', help='Verbose (DEBUG) logging')
    p_games.add_argument('--quiet', action='store_true', help='Only warnings and the summary')
    p_games.add_argument('-season', '-s', required=True,
                         help='Season: year-only ("2026") or "YYYY-YY" (e.g. "2026-27")')
    p_games.add_argument('-teams', nargs='*', type=int, help='Specific team IDs')
    p_games.add_argument('--scraper', help='Only teams whose teams.json scraper key '
                                           'equals this value')
    p_games.add_argument('--data-dir', default=None, metavar='DIR',
                         help='Game JSON directory (default: ../volleyball-game-data, '
                              '$VB_GAME_DATA_DIR)')
    p_games.add_argument('--skip-existing', action='store_true',
                         help="Don't re-download games that already have a JSON file")
    p_games.add_argument('--headed', action='store_true',
                         help='Show the Playwright browser window')
    p_games.add_argument('--workers', type=int, default=6,
                         help='Parallel discovery/fetch workers (default: 6)')
    p_games.add_argument('--teams-file', default=None,
                         help='Path to teams.json (default: data/teams.json)')
    p_games.add_argument('--outcomes', default=None, metavar='FILE',
                         help='Per-team outcome CSV path (default: '
                              'data/outcomes/games_<season>_<stamp>.csv)')
    p_games.set_defaults(func=cmd_games)

    p_logs = sub.add_parser('logs',
                            help='Build a game log CSV (vblogs{YY}_{div}.csv) '
                                 'from the volleyball-game-data archive',
                            description='One row per team per game, in the '
                                        'NCAAWomensVolleyballData matchstats '
                                        'schema plus extras (set scores, '
                                        'attendance, opponent ids)')
    p_logs.add_argument('--verbose', action='store_true', help='Verbose (DEBUG) logging')
    p_logs.add_argument('--quiet', action='store_true', help='Only warnings and the summary')
    p_logs.add_argument('-s', '--season', required=True,
                        help='Season: year-only ("2026") or "YYYY-YY" (e.g. "2026-27")')
    p_logs.add_argument('-d', '--division', required=True, choices=['I', 'II', 'III'],
                        help='Division of the teams whose logs to build')
    p_logs.add_argument('-output', '--out', dest='output', default=None,
                        help='Output CSV file path (default: vblogs{YY}_{i|ii|iii}.csv '
                             'in the output dir)')
    p_logs.add_argument('--out-dir', default=str(REPO_ROOT / "data" / "game-logs"),
                        metavar='DIR',
                        help='CSV output directory (default: data/game-logs)')
    p_logs.add_argument('--data-dir', default=None, metavar='DIR',
                        help='Game JSON directory (default: ../volleyball-game-data, '
                             '$VB_GAME_DATA_DIR)')
    p_logs.set_defaults(func=cmd_logs)

    p_list = sub.add_parser('list-teams', parents=[common],
                            help='List teams with their scraper configuration (no network)')
    p_list.add_argument('--no-url', action='store_true',
                        help="List only teams that lack a url key")
    p_list.add_argument('--type', dest='type',
                        help="Only teams whose scraper key equals this value")
    p_list.add_argument('--format', choices=['csv', 'json'], default='csv')
    p_list.add_argument('--teams-file', default=None, help='Path to teams.json')
    p_list.set_defaults(func=cmd_list_teams)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(quiet=args.quiet, verbose=args.verbose)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        sys.stderr.write("\nInterrupted.\n")
        return 130
    except Exception as e:
        logger.error(f"Fatal: {e}")
        if args.verbose:
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())
