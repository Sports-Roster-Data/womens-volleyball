"""Game log CSVs built from the volleyball-game-data archive.

Modeled on the wbb package's logs module and on the reference game log in
NCAAWomensVolleyballData (data/ncaa_womens_volleyball_matchstats_<season>.csv):
every game recorded for the requested division and season, one row per team
per game, with the team's box-score block and the opponent's under
defensive_ prefixes. Scores are sets won (team_score 3, s 5 = a five-set
match), matching the reference. Percentage-style columns elsewhere in this
repo are computed from raw totals; hit_pct here is (kills - errors) / attacks.

Unlike the wbb builder, which reads a pre-built index of games, this scans
the raw archive directly: volleyball-game-data stores each scraped team's
copies under its own directory (<ncaa_id>-<slug>/<season>/<game_id>.json),
and each file carries both sides' totals, so one row comes from each
division team's file.

Guards, in the spirit of this repo's roster work:

- Each file is date-checked against the requested season's window
  (Aug 1 - Jun 30; vb.games.season_date_bounds) before use, so a
  slug-ignoring site's wrong-season games (the "site echo" problem) can
  never leak into a season's log.
- The row team's side is resolved by site hostname (Game.ClientHostname or
  the team-logo URL, which appear only on the host side), then by the
  archive slug / manifest name; an unresolved side emits a row without box
  stats rather than a guessed one.
- The opponent's ncaa_id is matched by name (exact normalized match, then
  an unambiguous prefix match); ambiguous names stay blank.

Beyond the reference columns the log adds: per-set scores (set1..set5,
row team first), total blocks both sides, opponent_ncaa_id, attendance,
location, officials, game_id, the archive URL, and a per-team game number.
"""

import csv
import json
import logging
import re
from collections import Counter
from collections import Counter
from pathlib import Path

from vb.games import (REPO_ROOT, game_date_from_json, resolve_game_data_dir,
                      season_date_bounds)

logger = logging.getLogger(__name__)

RAW_URL = "https://raw.githubusercontent.com/dwillis/volleyball-game-data/main/"
TEAMS_PATH = REPO_ROOT / "data" / "teams.json"
DIVISIONS_PATH = REPO_ROOT / "data" / "vb_teams.csv"
DIVISION_KEYS = {'I': 'i', 'II': 'ii', 'III': 'iii'}

# The reference matchstats schema: one stat block per team side, the
# opponent's under defensive_ prefixes. retatt (reception attempts) has no
# livestats counterpart and stays blank, kept for schema parity.
_STAT_BLOCK = ['kills', 'errors', 'total_attacks', 'hit_pct', 'assists', 'aces',
               'serr', 'digs', 'retatt', 'rerr', 'block_solos', 'block_assists',
               'berr', 'pts', 'bhe']
_SET_BLOCK = ['set1', 'set2', 'set3', 'set4', 'set5']
FIELDS = (['ncaa_id', 'date', 'team', 'opponent', 'home_away', 'result',
           'team_score', 'opponent_score', 's']
          + _STAT_BLOCK
          + ['defensive_' + c for c in _STAT_BLOCK]
          + _SET_BLOCK
          + ['opponent_ncaa_id', 'tb', 'defensive_tb',
             'attendance', 'location', 'officials', 'game_id', 'url', 'game_no'])

# livestats Totals.Values keys, aligned with _STAT_BLOCK (None = no
# counterpart, e.g. reception attempts).
_LIVESTATS_KEYS = ['Kills', 'AttackErrors', 'TotalAttackAttempts', 'HittingPercentage',
                   'Assists', 'ServiceAces', 'ServiceErrors', 'Digs', None,
                   'ReceptionErrors', 'BlockSolos', 'BlockAssists', 'BlockErrors',
                   'Points', 'BallHandlingErrors']


def season_year(season):
    """Short year for the output filename: '2025-26' -> '26'."""
    return season[-2:]


def output_name(season, division):
    """Default output filename: vblogs{year}_{i|ii|iii}.csv."""
    return f"vblogs{season_year(season)}_{DIVISION_KEYS[division]}.csv"


def load_manifest():
    """Team manifest keyed by ncaa_id, with division and conference from
    data/vb_teams.csv merged in."""
    teams = json.loads(TEAMS_PATH.read_text())
    manifest = {t['ncaa_id']: t for t in teams if t.get('ncaa_id') is not None}
    with open(DIVISIONS_PATH, newline='') as f:
        for row in csv.DictReader(f):
            try:
                team = manifest.get(int(row['ncaa_id']))
            except (KeyError, ValueError):
                continue
            if team is not None:
                team['division'] = row.get('division') or ''
                team['conference'] = row.get('conference') or ''
    return manifest


# ------------------------------------------------------------ stat parsing

def _num(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _num_float(value):
    """Float from '.179' / '0.214' / '4.0'; None when unparseable."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def side_team_stats(side):
    """One Sidearm Stats.<Team>.Totals block -> {stat: value}.

    hit_pct is recomputed from (kills - errors) / attempts when those totals
    exist (the feed's rounded value is the fallback); pts is the feed's
    Points (kills + 0.5 * assisted blocks + aces, the NCAA box convention);
    tb (total blocks) is solos + 0.5 * assists, falling back to TotalBlocks.
    """
    values = ((side or {}).get('Totals') or {}).get('Values') or {}
    stats = dict(zip(_STAT_BLOCK, [_num(values.get(k)) for k in _LIVESTATS_KEYS]))
    k, e, ta = stats['kills'], stats['errors'], stats['total_attacks']
    if k is not None and e is not None and ta:
        stats['hit_pct'] = round((k - e) / ta, 3)
    else:
        pct = _num_float(values.get('HittingPercentage'))
        stats['hit_pct'] = round(pct, 3) if pct is not None else None
    solos, assists = _num(values.get('BlockSolos')), _num(values.get('BlockAssists'))
    if solos is not None:
        stats['tb'] = round(solos + 0.5 * (assists or 0), 1)
    else:
        stats['tb'] = _num_float(values.get('TotalBlocks'))
    return stats


def _empty_side_stats():
    stats = {key: None for key in _STAT_BLOCK}
    stats['tb'] = None
    return stats


def parse_game_json(data):
    """Both sides' names, set scores and box stat totals for one file.

    Returns {'home': ..., 'visitor': ...} (each with name, sets_won,
    period_scores, stats), or None when the payload is not a Sidearm
    livestats volleyball game. Note that team-logo URLs and Game.URL carry
    the serving site's domain for both sides (a site renders its own logo
    for the visiting team too), so hostnames cannot identify a side.
    """
    if not (isinstance(data, dict) and isinstance(data.get('Game'), dict)
            and isinstance(data.get('Stats'), dict)):
        return None
    game = data['Game']
    if game.get('Type') and game.get('Type') != 'VolleyballGame':
        return None
    sides = {}
    for key, side_name in (('HomeTeam', 'home'), ('VisitingTeam', 'visitor')):
        info = game.get(key) or {}
        sides[side_name] = {
            'name': info.get('Name') or '',
            'sets_won': _num(info.get('Score')),
            'period_scores': [s for s in (info.get('PeriodScores') or [])
                              if _num(s) is not None],
            'stats': side_team_stats(data['Stats'].get(key) or {}),
        }
    return sides


def _normalize_name(name):
    return re.sub(r'[^a-z0-9]', '', (name or '').lower())


def match_team_id(name, manifest, exclude_id=None):
    """ncaa_id whose teams.json name matches, or None.

    Exact normalized match first; then a substring match on normalized names
    of at least eight characters ("Fresno St." vs "Fresno State"), kept only
    when unambiguous. "Texas A&M" wins by exact match even though
    "Texas A&M-Corpus Christi" also exists; a shorter ambiguous key ("Tex")
    matches nothing.
    """
    key = _normalize_name(name)
    if not key:
        return None
    by_norm = {}
    for tid, t in manifest.items():
        if tid == exclude_id:
            continue
        norm = _normalize_name(t.get('team'))
        if norm and norm not in by_norm:
            by_norm[norm] = tid
    exact = by_norm.get(key)
    if exact is not None:
        return exact
    if len(key) < 6 or len(set(by_norm)) < 2:
        return None
    # Abbreviations run both ways: a shorter feed name inside a fuller
    # manifest name ("Fresno St." in "Fresno State") and a fuller feed name
    # containing the manifest name ("Belmont Bruins" over "Belmont").
    candidates = [tid for norm, tid in by_norm.items()
                  if len(norm) >= 6 and (key in norm or norm in key)]
    if len(candidates) == 1:
        return candidates[0]
    return None


def resolve_sides(slug_tail, manifest_name, parsed):
    """Which parsed side ('home'/'visitor') is the archive team's, or None.

    Side identity rests on names: the archive slug's team name and the
    manifest name, exact-normalized match first, then containment in either
    direction — ambiguous when both names (or neither) match.
    """
    if parsed is None:
        return None
    names = {s: _normalize_name(parsed[s].get('name')) for s in ('home', 'visitor')}
    keys = {_normalize_name(k) for k in (slug_tail, manifest_name) if k}
    for key in ('exact', 'contained'):
        matches = []
        if key == 'exact':
            matches = [s for s in ('home', 'visitor') if names[s] in keys]
        elif names['home'] and names['visitor']:
            matches = [s for s in ('home', 'visitor')
                       if any(s_ok(k, names[s]) for k in keys)
                       and not any(s_ok(k, names['visitor' if s == 'home' else 'home'])
                                   for k in keys)]
        matches = list(dict.fromkeys(matches))
        if len(matches) == 1:
            return matches[0]
    return None


def s_ok(slug, name):
    """Name containment in either direction, too short to be promiscuous."""
    return slug in name or (len(slug) >= 8 and name in slug)


# -------------------------------------------------------------- log builder

def build_game_logs(season, division, data_dir=None):
    """Build the game log rows for one division and season.

    Returns (rows, info); rows are dicts keyed by FIELDS, ordered by team
    name then date, and info carries summary counters for the CLI.
    """
    data_dir = Path(resolve_game_data_dir(data_dir))
    manifest = load_manifest()
    division_ids = {tid for tid, team in manifest.items()
                    if team.get('division') == division}
    if not division_ids:
        raise ValueError(f"No teams with division '{division}' in {DIVISIONS_PATH}")
    bounds = season_date_bounds(season)

    rows = []
    counters = Counter()
    files_scanned = 0
    teams_with_games = set()
    season_dir_glob = sorted(data_dir.glob(f'*/{season}'))
    for season_dir in season_dir_glob:
        dir_name = season_dir.parent.name
        try:
            slug_id, slug_tail = dir_name.split('-', 1)
            ncaa_id = int(slug_id)
        except ValueError:
            counters['unknown_dir'] += 1
            continue
        scrape_team = manifest.get(ncaa_id)
        if scrape_team is None or ncaa_id not in division_ids:
            counters['outside_division'] += 1
            continue
        scrape_name = scrape_team.get('team') or ''
        for raw_path in sorted(season_dir.glob('*.json'), key=lambda p: p.name):
            files_scanned += 1
            try:
                data = json.loads(raw_path.read_text())
            except (OSError, json.JSONDecodeError) as e:
                logger.debug(f"unreadable: {raw_path.name}: {e}")
                counters['unreadable'] += 1
                continue
            d = game_date_from_json(data)
            if d is None:
                counters['no_date'] += 1
                continue
            if not (bounds[0] <= d <= bounds[1]):
                counters['outside_season'] += 1
                continue
            parsed = parse_game_json(data)
            side_name = resolve_sides(slug_tail, scrape_name, parsed) if parsed else None
            if parsed is None:
                counters['unparsed'] += 1
            elif side_name is None:
                counters['side_unknown'] += 1
            team_side = (parsed.get(side_name) or {}) if side_name else {}
            opp_side_name = 'visitor' if side_name == 'home' else 'home'
            opp_side = (parsed.get(opp_side_name) or {}) if side_name else {}
            my_periods = team_side.get('period_scores') or []
            opp_periods = opp_side.get('period_scores') or []
            sets_total = max(len(my_periods), len(opp_periods))
            if team_side.get('sets_won') is not None:
                team_score = team_side['sets_won']
            elif opp_periods:
                team_score = sum(1 for a, b in zip(my_periods, opp_periods) if a > b)
            else:
                team_score = None
            if opp_side.get('sets_won') is not None:
                opp_score = opp_side['sets_won']
            elif my_periods:
                opp_score = sum(1 for a, b in zip(opp_periods, my_periods) if a > b)
            else:
                opp_score = None
            got_scores = team_score is not None and opp_score is not None
            is_home = side_name == 'home'
            game = (data or {}).get('Game') or {}
            opp_name = opp_side.get('name') or ''
            opp_id = match_team_id(opp_name, manifest, exclude_id=ncaa_id) if opp_name else None
            my_stats = team_side.get('stats') or _empty_side_stats()
            opp_stats = opp_side.get('stats') or _empty_side_stats()

            row = {
                'ncaa_id': ncaa_id,
                'date': d.isoformat(),
                'team': scrape_name,
                'opponent': opp_name,
                'home_away': 'Home' if is_home else ('Away' if side_name else ''),
                'result': ('W' if (got_scores and team_score > opp_score)
                           else ('L' if got_scores else '')),
                'team_score': team_score,
                'opponent_score': opp_score,
                's': sets_total or None,
                'tb': my_stats.get('tb'),
                'attendance': _num(game.get('Attendance')),
                'location': game.get('Location') or None,
                'officials': game.get('Officials') or None,
                'game_id': raw_path.stem,
                'url': RAW_URL + str(raw_path.relative_to(data_dir)),
                'opponent_ncaa_id': opp_id,
            }
            for label in _STAT_BLOCK:
                row[label] = my_stats[label]
                row['defensive_' + label] = opp_stats[label]
            row['defensive_tb'] = opp_stats.get('tb')
            for i, label in enumerate(_SET_BLOCK):
                mine = my_periods[i] if i < len(my_periods) else None
                theirs = opp_periods[i] if i < len(opp_periods) else None
                row[label] = (f"{mine}-{theirs}"
                              if mine is not None and theirs is not None else None)
            rows.append(row)
            teams_with_games.add(ncaa_id)

    rows.sort(key=lambda r: (r['team'].lower(), r['date'], r['game_id']))
    game_no = Counter()
    for row in rows:
        game_no[row['ncaa_id']] += 1
        row['game_no'] = game_no[row['ncaa_id']]

    info = {
        'season': season,
        'division': division,
        'teams': len(teams_with_games),
        'files_scanned': files_scanned,
        'rows': len(rows),
        'unreadable': counters['unreadable'],
        'no_date': counters['no_date'],
        'unparsed': counters['unparsed'],
        'outside_season': counters['outside_season'],
        'outside_division': counters['outside_division'],
        'side_unknown': counters['side_unknown'],
        'data_dir': data_dir,
    }
    return rows, info


def write_game_logs(rows, path):
    """Write the game log rows as CSV; returns the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction='raise')
        writer.writeheader()
        writer.writerows(rows)
    return path
