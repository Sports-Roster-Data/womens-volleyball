"""Game (box score + play-by-play) JSON scraping, volleyball edition.

Ported from the wbb package's games module (wbb/games.py), which itself
porteds ncaa/games/game_utils.py. Volleyball-specific changes:

- The WMT (wmt.games) branch is dropped: the wmt.games schools index covers
  ~44 schools and its stats pages do not carry volleyball.
- Discovery is Sidearm-only: boxscore ids from the site's stats page
  (`/sports/<sport>/stats/<season>`), then the season schedule page, falling
  back to a Playwright-rendered stats page for JS-driven sites.
- Every fetched JSON is date-checked against the requested season. The roster
  side of this repo has already established that many Sidearm sites ignore
  the season slug in /stats and /schedule URLs and serve the current season's
  games whatever slug you request (the "site echo" problem — see
  data/docs and the echo-audit work in tests/test_echo_guard.py). Without the
  date check those runs would silently mix the wrong season's games into
  volleyball-game-data.

Layout is identical to wbb-game-data:
<game_data_dir>/<slug>/<season>/<game_id>.json
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
GAME_DATA_DIR = REPO_ROOT.parent / "volleyball-game-data"

HEADERS = {'User-agent': 'Mozilla/5.0'}

# ?path=... sport-path guesses for the schedule page; sites differ (wvolley,
# womens-volleyball, volleyball, ...). The bare base + '/schedule/<season>'
# form is tried first and often works without the hint.
SCHEDULE_PATH_HINTS = ['', 'wvolley', 'womens-volleyball', 'volleyball',
                       'w-volleyball', 'wvball']


def resolve_game_data_dir(data_dir=None) -> Path:
    """--data-dir flag, $VB_GAME_DATA_DIR, or <repo parent>/volleyball-game-data."""
    return Path(data_dir or os.environ.get("VB_GAME_DATA_DIR") or GAME_DATA_DIR)


def validate_season(season):
    """Raise ValueError unless season is 'YYYY-YY' with consecutive years."""
    if not isinstance(season, str) or not re.fullmatch(r'\d{4}-\d{2}', season) \
            or int(season[5:7]) != (int(season[:4]) + 1) % 100:
        raise ValueError(f"Invalid season: '{season}' - expected format is "
                         f"'YYYY-YY' (e.g., '2026-27'; a year-only '2026' is "
                         f"canonicalized by the CLI)")


def canonical_season_label(season: str) -> str:
    """Year-only '2026' -> '2026-27'; pass through full labels unvalidated."""
    season = str(season).strip()
    if re.fullmatch(r'\d{4}', season):
        return f"{season}-{(int(season) + 1) % 100:02d}"
    return season


def slugify(team) -> str:
    """Directory slug, identical to wbb's layout (e.g. '8-alabama')."""
    return str(team['ncaa_id']) + '-' + team['team'].lower().replace(" ", "-").replace(
        '.', '').replace(',', '').replace("'", "").replace(')', '').replace('(', '')


def _dedupe(ids):
    return list(dict.fromkeys(ids))


def game_ids_from_links(links) -> List[str]:
    """Game ids from boxscore hrefs, both Sidearm layouts.

    Old ASP.NET style:  /boxscore.aspx?id=21789[&path=wvolley]
    Modern segment style: /sports/womens-volleyball/stats/2025/florida/boxscore/16437
    """
    ids = []
    for href in links:
        if 'id=' in href:
            tail = href.split('id=', 1)[1].split('&')[0]
            if tail.isdigit():
                ids.append(tail)
                continue
        tail = href.rstrip('/').split('/')[-1]
        if tail.isdigit():
            ids.append(tail)
    return _dedupe(ids)


def schedule_urls(base_url, season) -> List[str]:
    """Schedule pages to try for a season, most likely first."""
    urls = [f"{base_url}/schedule/season/{season}/", f"{base_url}/schedule/{season}"]
    for hint in SCHEDULE_PATH_HINTS[1:]:
        urls.append(f"{base_url}/schedule/{season}?path={hint}")
    return urls


def season_date_bounds(season: str) -> Tuple[date, date]:
    """Acceptable game-date window for a season label.

    NCAA volleyball runs late August through December, with the 2020-21
    exception extending into spring: the window is Aug 1 of the start year
    through Jun 30 of the end year, which rejects both the next season's
    fall games (the site-echo failure mode) and the previous season's.
    """
    start = int(season[:4])
    return date(start, 8, 1), date(start + 1, 6, 30)


def game_date_from_json(game_json) -> Optional[date]:
    """The livestats JSON's game date, or None when absent/unparseable."""
    if not isinstance(game_json, dict):
        return None
    game = game_json.get('Game') or {}
    for raw in (game.get('DateUTC'), game.get('Date')):
        if not raw:
            continue
        text = str(raw).strip().split('.')[0].replace('T', ' ')
        for fmt in ('%m/%d/%Y', '%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%m/%d/%Y %H:%M:%S'):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    return None


def game_in_season(game_json, season: str):
    """True/False/None(None = no parseable date; caller decides)."""
    d = game_date_from_json(game_json)
    if d is None:
        return None
    lo, hi = season_date_bounds(season)
    return lo <= d <= hi


def fetch_game_ids_static(base_url, season):
    """Game ids from the season stats page's boxscore links (requests-only).

    NOTE: on slug-ignoring sites this serves the current season whatever
    slug is requested; harmless, because every fetch is date-checked later.
    """
    r = requests.get(f"{base_url}/stats/{season}", headers=HEADERS, timeout=30)
    if r.status_code != 200:
        return []
    links = [a['href'] for a in BeautifulSoup(r.text, "html.parser").find_all('a', href=True)
             if 'boxscore' in a['href']]
    return game_ids_from_links(links)


def fetch_game_ids_schedule(base_url, season):
    """Game ids from any season schedule page's boxscore links (requests-only)."""
    for url in schedule_urls(base_url, season):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
        except requests.RequestException:
            continue
        if r.status_code != 200:
            continue
        links = [a['href'] for a in
                 BeautifulSoup(r.text, "html.parser").find_all('a', href=True)
                 if 'boxscore' in a['href']]
        ids = game_ids_from_links(links)
        if ids:
            return ids
    return []


def fetch_game_json(domain, game_id):
    """One game's livestats JSON, or None.

    Same endpoint wbb uses; Sidearm's livestats API is sport-agnostic and
    serves volleyball ('Type': 'VolleyballGame') for both the ASP.NET
    boxscore ids and the modern segment ids.
    """
    url = f"https://{domain}/api/livestats?game_id={game_id}&detail=full"
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
    except requests.RequestException as e:
        logger.debug(f"livestats request failed for game {game_id}: {e}")
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def fetch_game_ids_playwright(browser, url):
    """Game ids from a rendered stats page (JS-driven sites), or []."""
    page = browser.new_page()
    try:
        page.goto(url)
        try:
            page.wait_for_selector("a[href*='boxscore']", timeout=10000, state='attached')
        except Exception:
            page.wait_for_timeout(3000)
        ids = []
        for link in page.locator("a[href*='boxscore']").all():
            href = link.get_attribute("href")
            if not href:
                continue
            ids.extend(game_ids_from_links([href]))
        return _dedupe(ids)
    finally:
        page.close()


@dataclass
class GameOutcome:
    team_id: int
    team: str
    found: int = 0
    saved: int = 0
    skipped: int = 0
    stale: int = 0      # fetched but dated outside the requested season
    failed: int = 0
    method: str = ''
    error: Optional[str] = None
    files: List[Path] = field(default_factory=list)

    @property
    def status(self):
        if self.error:
            return 'error'
        return 'ok' if self.found else 'zero'


class _NeedsBrowser(Exception):
    """Internal: the static pages had nothing; Playwright fallback needed."""


class GameScraper:
    """Discovers and downloads volleyball game JSON for teams/seasons.

    scrape_all() runs three phases so the shared browser stays
    single-threaded (sync Playwright is not): (1) requests-only discovery in
    a thread pool, (2) sequential Playwright discovery for teams whose static
    pages had nothing — queueing each team's fetches into the same pool as
    its discovery finishes, (3) a blocking drain. Workers share nothing
    mutable; outcomes are finalized in the main thread.
    """

    def __init__(self, data_dir=None, headed=False, skip_existing=False, workers=6,
                 on_team: Optional[Callable[[GameOutcome], None]] = None):
        self.data_dir = resolve_game_data_dir(data_dir)
        self.headed = headed
        self.skip_existing = skip_existing
        self.workers = workers
        self.on_team = on_team
        self.results: List[GameOutcome] = []
        self._pw = None
        self._browser = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self._browser is not None:
            self._browser.close()
        if self._pw is not None:
            self._pw.stop()

    def _get_browser(self):
        if self._browser is None:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=not self.headed)
        return self._browser

    def discover(self, team, season):
        """(game_ids, method) for one team: static stats page, then Playwright."""
        base_url = team['url']
        try:
            ids = fetch_game_ids_static(base_url, season)
            if ids:
                return _dedupe(ids), 'static'
        except requests.RequestException as e:
            logger.debug(f"static stats page failed for {team['team']}: {e}")
        ids = fetch_game_ids_playwright(self._get_browser(),
                                        f"{base_url}/stats/{season}")
        return _dedupe(ids), 'playwright'

    def _requests_discovery(self, team, season):
        """Phase 1 discovery: requests only. Raises _NeedsBrowser elsewhere."""
        try:
            ids = fetch_game_ids_static(team['url'], season)
        except requests.RequestException as e:
            logger.debug(f"request discovery for {team['team']}: {e} -> trying browser")
            raise _NeedsBrowser(team['team']) from e
        if ids:
            return _dedupe(ids), 'static'
        ids = fetch_game_ids_schedule(team['url'], season)
        if ids:
            return _dedupe(ids), 'static'
        raise _NeedsBrowser(team['team'])

    def _fetch_one(self, team, season, game_id, out_dir) -> Tuple[str, Optional[Path]]:
        """('saved'|'skipped'|'stale'|'failed', path) for one game fetch."""
        domain = urlparse(team['url']).netloc
        try:
            game_json = fetch_game_json(domain, game_id)
        except (requests.RequestException, ValueError) as e:
            logger.debug(f"game {game_id} fetch failed: {e}")
            return 'failed', None
        if game_json is None:
            return 'failed', None
        verdict = game_in_season(game_json, season)
        if verdict is False:
            logger.debug(f"game {game_id} ({game_date_from_json(game_json)}) outside "
                         f"{season}; not saved")
            return 'stale', None
        path = out_dir / f"{game_id}.json"
        if self.skip_existing and path.exists():
            return 'skipped', None
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(game_json, f, ensure_ascii=False, indent=4)
        return 'saved', path

    def scrape_all(self, teams, season, on_discovery=None) -> List[GameOutcome]:
        """Pipelined run over all teams; returns outcomes in completion order.

        on_discovery(phase, done, total) fires per completed discovery for CLI
        progress; phase is 'requests' or 'playwright'.
        """
        outcomes = [GameOutcome(t['ncaa_id'], t['team']) for t in teams]
        discovered: List[Optional[Tuple[List[str], str]]] = [None] * len(teams)
        n = len(teams)

        def discover_job(i):
            try:
                return i, self._requests_discovery(teams[i], season), None
            except _NeedsBrowser as e:
                return i, None, ('browser', e)
            except Exception as e:
                return i, None, ('error', e)

        def fetch_job(i):
            refs = discovered[i][0] if discovered[i] else []
            if not refs:
                return i, []
            out_dir = self.data_dir / slugify(teams[i]) / season
            return i, [(self._fetch_one(teams[i], season, game_id, out_dir), game_id)
                       for game_id in refs]

        def finalize(i, per):
            outcome = outcomes[i]
            if discovered[i]:
                outcome.method = discovered[i][1]
                outcome.found = len(per)
            for (status, path), _gid in per:
                if status == 'saved':
                    outcome.saved += 1
                    outcome.files.append(path)
                elif status == 'stale':
                    outcome.stale += 1
                elif status == 'skipped':
                    outcome.skipped += 1
                else:
                    outcome.failed += 1
            self.results.append(outcome)
            if self.on_team is not None:
                self.on_team(outcome)

        from concurrent.futures import (ThreadPoolExecutor, as_completed, wait,
                                        FIRST_COMPLETED)
        with ThreadPoolExecutor(max_workers=max(1, self.workers)) as pool:
            logger.info(f"discovering (static requests): {n} teams, {self.workers} workers")
            pending: dict = {}
            need_browser: List[int] = []
            done1 = 0
            for fut in as_completed([pool.submit(discover_job, i) for i in range(n)]):
                i, refs, err = fut.result()
                if err is None:
                    game_ids, method = refs
                    discovered[i] = (game_ids, method)
                elif err[0] == 'browser':
                    need_browser.append(i)
                else:
                    e = err[1]
                    outcomes[i].error = str(e).splitlines()[0] if str(e) else type(e).__name__
                done1 += 1
                if on_discovery is not None:
                    on_discovery('requests', done1, n)

            for i, di in enumerate(discovered):
                if di is not None and di[0]:
                    pending[pool.submit(fetch_job, i)] = i

            # Phase 2: Playwright discovery, sequential (one shared browser).
            if need_browser:
                logger.info(f"discovering (browser): {len(need_browser)} teams, sequential")
            for j, i in enumerate(need_browser, 1):
                try:
                    refs, method = self.discover(teams[i], season)
                    discovered[i] = (refs, method)
                except Exception as e:
                    # A missing game list (no games posted yet) surfaces as a timeout.
                    if type(e).__name__ == 'TimeoutError':
                        logger.debug(f"no game list for {teams[i]['team']} {season}: {e}")
                        discovered[i] = ([], 'playwright')
                    else:
                        outcomes[i].error = str(e).splitlines()[0] if str(e) else type(e).__name__
                if discovered[i]:
                    pending[pool.submit(fetch_job, i)] = i
                    if pending:
                        done, _ = wait(set(pending), timeout=0, return_when=FIRST_COMPLETED)
                        for f in done:
                            (i_done, per) = f.result()
                            finalize(i_done, per)
                            del pending[f]
                if on_discovery is not None:
                    on_discovery('playwright', done1 - len(need_browser) + j, n)

            # Phase 3: block until all queued fetches finish.
            if pending:
                done, _ = wait(set(pending))
                for f in done:
                    i, per = f.result()
                    finalize(i, per)
            for i in range(n):
                if discovered[i] is None:
                    finalize(i, [])
        return self.results

    def scrape_team(self, team, season) -> GameOutcome:
        """Sequential single-team convenience path."""
        return self.scrape_all([team], season)[0]
