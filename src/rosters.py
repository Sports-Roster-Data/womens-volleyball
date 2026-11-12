#!/usr/bin/env python3
"""
NCAA Women's Volleyball Roster Scraper - Unified Architecture
Adapted from Soccer scraper architecture for volleyball-specific needs

Usage:
    python rosters.py -season 2023-24
    python rosters.py -season 2023-24 -teams 255 326
    python rosters.py -url https://example.com/sports/w-volley -season 2023-24
    python rosters.py -season 2025        # year-only accepted; written as 2025-26

Seasons are written with the full academic-year label (fall sport: end year
is start + 1), so `-season 2025` writes data/rosters_2025-26.csv and uses
data/cache/rosters_2025-26/ for its resume cache.
"""

import os
import re
import csv
import time
import json
import shutil
import argparse
import logging
import subprocess
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import tldextract

# Configure tldextract to not fetch updates (to avoid 403 errors)
tldextract.extract = tldextract.TLDExtract(suffix_list_urls=None)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Network settings shared by all requests-based fetchers.
REQUEST_TIMEOUT = 15  # seconds
RATE_LIMIT_SECONDS = 0.5  # polite delay between teams in a bulk run


def _build_session() -> requests.Session:
    """A requests session with retry/backoff on transient HTTP failures."""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(['GET']),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session


SESSION = _build_session()


# ============================================================================
# CONSTANTS
# ============================================================================

# Standard header mappings for table-based rosters
HEADERS = {
    'No.': 'jersey', 'Name': 'name', 'NAME': 'name', 'Cl.': 'academic_year',
    'Pos.': 'position', 'Ht.': 'height', 'Hometown/High School': 'town',
    'Hometown/Last School': 'town', 'Num': 'jersey', 'Yr': 'academic_year',
    'Ht': 'height', 'Hometown': 'town', 'High School/Previous School': 'high_school',
    'Pos': 'position', 'Hometown/Previous School': 'town', 'Exp.': 'academic_year',
    'Number': 'jersey', 'Position': 'position', 'HT.': 'height', 'YEAR': 'academic_year',
    'HOMETOWN': 'town', 'LAST SCHOOL': 'high_school', 'Yr.': 'academic_year',
    'Hometown/High School/Last School': 'town', 'Class': 'academic_year',
    'High school': 'high_school', 'Previous College': 'previous_school',
    'Cl.-Exp.': 'academic_year', '#': 'jersey', 'High School': 'high_school',
    'Hometown / Previous School': 'town', 'No': "jersey",
    'Hometown/High School/Previous School': 'town',
    'Hometown / High School / Last College': 'town', 'Year': 'academic_year',
    'Height': 'height', 'Previous School': 'high_school', 'Cl': 'academic_year',
    'Prev. Coll.': 'previous_school', 'Hgt.': 'height', 'Hometown/ High School': 'town',
    'Hometown/High School (Last School)': 'town',
    'Hometown/High School (Former School)': 'town', 'Hometown / High School': 'town',
    'YR': 'academic_year', 'POS': 'position', 'HT': 'height', 'Player': 'name',
    'Hometown/High School/Previous College': 'town', 'Last School/Hometown': 'town',
    'Academic Year': 'academic_year',
    'Number Jersey Number': 'jersey',
    'NO.': 'jersey', 'YR.': 'academic_year', 'POS.': 'position',
    'HIGH SCHOOL': 'high_school', 'NO': 'jersey', 'HOMETOWN/HIGH SCHOOL': 'town',
    'Academic Yr.': 'academic_year', 'Full Name': 'name', 'POSITION': 'position',
    'Hometown / Previous School / High School': 'town',
    'High School / Previous School': 'high_school',
    'Hometown/High School (Previous School)': 'town',
    'Hometown/Previous School/Club Team': 'town'
}


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class Player:
    """Player data structure for NCAA women's volleyball rosters"""
    team_id: int
    team: str
    season: str
    player_id: Optional[str] = None
    name: str = ""
    jersey: str = ""
    position: str = ""
    height: str = ""
    year: str = ""  # Academic year (class)
    hometown: str = ""
    high_school: str = ""
    previous_school: str = ""
    url: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for CSV output"""
        d = asdict(self)

        # Clean string fields
        for k, v in list(d.items()):
            if isinstance(v, str):
                d[k] = FieldExtractors.clean_text(v)

        # Move high-school-like values out of previous_school, then drop a
        # previous_school that merely repeats high_school.
        reclassify_high_school_from_previous(d)
        dedupe_school_fields(d)

        return d


# ============================================================================
# FIELD EXTRACTORS
# ============================================================================

class FieldExtractors:
    """Text-cleaning utilities used when building Player output."""

    @staticmethod
    def clean_text(text: str) -> str:
        """Clean and normalize text"""
        if not text:
            return ""

        # Remove extra whitespace and normalize
        cleaned = re.sub(r'\s+', ' ', text.strip())

        # Remove common unwanted elements
        cleaned = re.sub(r'\s*(Full Bio|Instagram|Twitter|Opens in a new window).*$', '', cleaned)

        # Strip common labelled prefixes
        cleaned = FieldExtractors.clean_field_labels(cleaned)

        return cleaned

    @staticmethod
    def clean_field_labels(text: str) -> str:
        """Remove label prefixes like 'Class:', 'Hometown:', etc."""
        if not text:
            return text

        patterns = [
            r'\bClass:\s*', r'\bHometown:\s*', r'\bHigh school:\s*',
            r'\bPrevious College:\s*', r'\bPrevious School:\s*',
            r'\bHt\.?:\s*', r'\bPos\.?:\s*', r'\bMajor:\s*',
            r'^High school:\s*', r'^Hometown:\s*', r'^No\.?:\s*',
            # Generic leading "Label:" prefix, e.g. 'Pos.: DS', 'Yr.: Fr.',
            # 'Hometown/High School: Huber Heights, Ohio' on rendered tables.
            r'^[A-Za-z][A-Za-z /.]{0,22}:\s*',
        ]

        for p in patterns:
            text = re.sub(p, '', text, flags=re.IGNORECASE).strip()

        return text


# ============================================================================
# SCRAPER UTILITIES
# ============================================================================

def fetch_url_with_javascript(url: str, timeout: int = 45, wait_ms: int = 5000) -> Optional[BeautifulSoup]:
    """
    Fetch URL with JavaScript rendering using shot-scraper

    This uses 'uv run shot-scraper html' to render JavaScript-heavy pages
    and return the rendered HTML for parsing.

    Args:
        url: URL to fetch
        timeout: Timeout in seconds (default 45)
        wait_ms: Milliseconds to wait after page load (default 5000)

    Returns:
        BeautifulSoup object or None if failed
    """
    try:
        # Use shot-scraper via uv to render JavaScript
        result = subprocess.run(
            ['uv', 'run', 'shot-scraper', 'html', url, '--wait', str(wait_ms)],
            capture_output=True,
            text=True,
            timeout=timeout
        )

        if result.returncode == 0:
            return BeautifulSoup(result.stdout, 'html.parser')
        else:
            logger.warning(f"shot-scraper returned code {result.returncode}: {result.stderr[:200]}")
            return None

    except subprocess.TimeoutExpired:
        logger.warning(f"shot-scraper timeout after {timeout}s for {url}")
        return None
    except FileNotFoundError:
        logger.error("shot-scraper or uv not found. Install with: uv sync")
        return None
    except Exception as e:
        logger.error(f"shot-scraper error: {e}")
        return None


def fetch_url_with_curl(url: str) -> str:
    """Fetch URL using curl as a fallback when requests fails"""
    try:
        result = subprocess.check_output([
            'curl', '-s', '-L', url,
            '-H', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            '-H', 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            '--compressed'
        ], timeout=30)
        return result.decode('utf-8', errors='ignore')
    except Exception as e:
        logger.error(f"curl fetch error for {url}: {e}")
        return ""


def fetch_url(url: str, headers: Optional[Dict] = None) -> requests.Response:
    """Fetch URL with standard headers, with curl fallback for 403 errors"""
    if headers is None:
        headers = {
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/95.0.4638.69 Safari/537.36"
        }

    r = SESSION.get(url, headers=headers, timeout=REQUEST_TIMEOUT)

    # If we get 403, try curl as fallback
    if r.status_code == 403:
        logger.warning(f"Got 403 for {url}, trying curl fallback")
        content = fetch_url_with_curl(url)
        if content:
            # Create a mock response object
            class MockResponse:
                def __init__(self, text, status_code=200):
                    self.text = text
                    self.status_code = status_code
            return MockResponse(content, 200)

    return r


def fetch_roster(base_url: str, season: str) -> Optional[BeautifulSoup]:
    """Fetch standard roster page"""
    url = f"{base_url}/roster/{season}"
    r = fetch_url(url)
    return BeautifulSoup(r.text, features="html.parser")


def fetch_wbkb_roster(base_url: str, season: str) -> Optional[BeautifulSoup]:
    """Fetch women's basketball style roster"""
    url = base_url.replace('index', season + '/roster?view=list')
    headers = {
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/95.0.4638.69 Safari/537.36"
    }
    r = SESSION.get(url, headers=headers, timeout=REQUEST_TIMEOUT)

    # Try curl fallback on 403
    if r.status_code == 403:
        logger.warning(f"Got 403 for {url}, trying curl fallback")
        content = fetch_url_with_curl(url)
        if content:
            return BeautifulSoup(content, features="html.parser")

    if r.status_code == 404:
        return None
    return BeautifulSoup(r.text, features="html.parser")


def fetch_baskbl_roster(base_url: str, season: str) -> BeautifulSoup:
    """Fetch basketball style roster"""
    if 'index' in base_url:
        url = base_url.replace('index', 'roster/?season=' + season)
    elif base_url.endswith('w-baskbl'):
        url = f"{base_url}/{season}/roster"
    else:
        url = base_url + 'roster/?season=' + season

    headers = {
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/95.0.4638.69 Safari/537.36"
    }
    r = SESSION.get(url, headers=headers, timeout=REQUEST_TIMEOUT)

    # Try curl fallback on 403
    if r.status_code == 403:
        logger.warning(f"Got 403 for {url}, trying curl fallback")
        content = fetch_url_with_curl(url)
        if content:
            return BeautifulSoup(content, features="html.parser")

    if r.status_code == 404:
        url = base_url.replace('index', f"/{season}/roster")
        r = SESSION.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        # Try curl fallback on 403 for the retry URL too
        if r.status_code == 403:
            logger.warning(f"Got 403 for {url}, trying curl fallback")
            content = fetch_url_with_curl(url)
            if content:
                return BeautifulSoup(content, features="html.parser")

    return BeautifulSoup(r.text, features="html.parser")


def shotscraper_caller(team: Dict, season: str, url: str, javascript_code: str) -> List[Dict]:
    """Call shot-scraper with JavaScript code to extract roster data"""
    roster = []
    try:
        result = subprocess.check_output([
            'shot-scraper', 'javascript', url, javascript_code,
            "--user-agent", "Firefox"
        ], timeout=60)
        parsed_data = json.loads(result)

        seen_urls: set = set()
        deduped = []
        for player in parsed_data:
            player['team_id'] = team['ncaa_id']
            player['team'] = team['team']
            player['season'] = season
            url_key = player.get('url', '')
            if url_key and url_key in seen_urls:
                continue
            if url_key:
                seen_urls.add(url_key)
            deduped.append(player)

        return deduped
    except subprocess.TimeoutExpired:
        logger.error(f"shot-scraper timeout for {team.get('team', 'unknown')}")
        return []
    except FileNotFoundError:
        logger.error(f"shot-scraper not found in PATH for {team.get('team', 'unknown')}")
        return []
    except Exception as e:
        logger.error(f"shot-scraper error for {team.get('team', 'unknown')}: {e}")
        return []


# ============================================================================
# SEASON VERIFICATION
# ============================================================================

class SeasonVerifier:
    """Classify whether a fetched roster page identifies the requested season.

    Ported from the field-hockey scraper. Sites that only serve a current-roster
    page (bare ``/roster`` on the Nuxt layouts) would otherwise be scraped and
    silently labeled with the requested season even when the page still shows
    last season's team. This checks the page title and h1/h2/h3 headings for
    year evidence.

    A season string such as "2025-26" is identified by its starting year, so
    "2025 Volleyball Roster" and the ranges "2025-26" / "2025-2026" all match;
    the trailing year ("26" or "2026") alone never counts as evidence.
    """

    @staticmethod
    def classify(html, season: str) -> str:
        candidates = []
        if html.title and html.title.get_text():
            candidates.append(('title', html.title.get_text()))
        for tag in html.find_all(['h1', 'h2', 'h3']):
            candidates.append(('heading', tag.get_text()))
        if not any(text.strip() for _kind, text in candidates):
            return 'unknown'
        try:
            expected = int(season[0:4])
        except (ValueError, TypeError):
            return 'unknown'

        def year_evidence(raw_text):
            text = re.sub(r'[–—]', '-', FieldExtractors.clean_text(raw_text))
            ranges = list(re.finditer(r'\b(20\d{2})\s*-\s*(20\d{2}|\d{2})\b', text))
            if any(int(match.group(1)) == expected for match in ranges):
                return 'matched'
            # Remove academic-year ranges before looking for a standalone year,
            # so the "26" in "2025-26" cannot satisfy a 2026-27 request.
            without_ranges = re.sub(r'\b20\d{2}\s*-\s*(?:20\d{2}|\d{2})\b', ' ', text)
            if re.search(rf'(?<!\d){expected}(?!\d)', without_ranges):
                return 'matched'
            if ranges or re.search(r'\b20\d{2}\b', without_ranges):
                return 'mismatch'
            return 'unknown'

        # An explicitly dated page title outweighs unrelated schedule/nav
        # headings carrying another year.
        title_evidence = year_evidence(candidates[0][1]) if candidates[0][0] == 'title' else 'unknown'
        if title_evidence in ('matched', 'mismatch'):
            return title_evidence

        if any(year_evidence(text) == 'matched' for _kind, text in candidates):
            return 'matched'
        if any(year_evidence(text) == 'mismatch' for _kind, text in candidates):
            return 'mismatch'
        return 'unknown'


def verify_roster_season(html, season: str, team: Dict) -> bool:
    """Reject a fetched roster page only when it positively shows another season.

    A 'mismatch' page (e.g. a current-roster page still stamped "2025 Volleyball
    Roster" while scraping "2026-27") returns False, so the caller moves on —
    or reports the team unparsed — instead of scraping last season's players
    under this season's label. Pages with 'unknown' verification are accepted:
    unlike the field-hockey scraper, positive season evidence is not required,
    because many volleyball roster pages carry no year in their headings and
    the fetched URL is usually season-qualified already.
    """
    verdict = SeasonVerifier.classify(html, season)
    if verdict != 'mismatch':
        return True
    logger.warning(f"Season mismatch for {team['team']}: page does not identify {season}")
    return False


def canonical_season_label(season: str) -> str:
    """Normalize a requested season to the full academic-year label used for
    all writes (CSV path, per-team cache dir, and the `season` column value).

    Volleyball is a fall sport spanning an academic year, so the canonical
    label is the starting year plus the two-digit end year (start + 1):

    >>> canonical_season_label('2025')
    '2025-26'
    >>> canonical_season_label('2025-26')
    '2025-26'
    >>> canonical_season_label('2023-24')
    '2023-24'

    A year-only four-digit season (existing muscle memory) is accepted and
    expanded; anything else raises ValueError.
    """
    if not isinstance(season, str):
        raise ValueError(f"Season must be a string: {season!r}")
    value = season.strip()
    if re.fullmatch(r'\d{4}', value):
        return f"{value}-{'%02d' % ((int(value) + 1) % 100)}"
    if re.fullmatch(r'\d{4}-\d{2}', value):
        start = int(value[:4])
        end = int(value[5:7])
        if end != (start + 1) % 100:
            logger.warning(f"Season label {value} does not extend its start year "
                           f"(expected {start}-{'%02d' % ((start + 1) % 100)}); "
                           f"using it as given")
        return value
    raise ValueError(f"Season must be a four-digit year ('2025') or a full "
                     f"academic-year label ('2025-26'); got {season!r}")


def season_url_slugs(season: str) -> List[str]:
    """Return the season slugs to embed in roster URL paths, best first.

    Volleyball seasons span an academic year, and many sites index rosters
    under the full slug ('2025-26') even when the requested season is
    year-only ('2025', a form canonical_season_label accepts).
    For a year-only season, derive the academic slug (fall sport: end year is
    start + 1) and try it before the bare year. A season that already carries
    its end year also yields its bare-start-year form, which the Nuxt
    /roster/season/<year> sites require.

    >>> season_url_slugs('2025')
    ['2025-26', '2025']
    >>> season_url_slugs('2026-27')
    ['2026-27', '2026']
    """
    year = season.split('-')[0]
    if re.fullmatch(r'\d{4}', season):
        slugs = [f"{season}-{(int(season) + 1) % 100:02d}"]
    else:
        slugs = [season]
    if year and year not in slugs:
        slugs.append(year)
    return slugs


# ============================================================================
# SCHOOL-FIELD HYGIENE
# ============================================================================

# A school-name marker that (unambiguously enough) identifies a high school.
# Ambiguous bare names and generic colleges are deliberately left alone.
_HS_MARKER = (
    r'(?:\bH\.?S\.?\b|High School|Secondary School|Grammar School|'
    r'Preparatory School|Prep School|Academy|Gymnasium|Lyceum|'
    r'\bSchool(?:\s+of\b|\b))'
)


def _school_value_key(value: str) -> str:
    """Normalize a school value for duplicate comparison."""
    return re.sub(r'\W+', '', FieldExtractors.clean_text(value).casefold(), flags=re.UNICODE)


def reclassify_high_school_from_previous(row: Dict) -> bool:
    """Move an explicitly high-school-like previous_school value to high_school.

    Only fires when high_school is empty. Combined values keep the transfer
    institution in previous_school, e.g. "Twin Valley HS / Liberty" via a slash
    and "Deer Park HS (University of Houston)" via a transfer-suffix paren.
    Ported from the field-hockey scraper.
    """
    if row.get('high_school') or not row.get('previous_school'):
        return False
    value = FieldExtractors.clean_text(row['previous_school'])
    if not re.search(_HS_MARKER, value, re.IGNORECASE):
        return False

    slash = re.match(rf'^(.+?{_HS_MARKER})\s*/\s*(.+)$', value, re.IGNORECASE)
    if slash:
        row['high_school'] = FieldExtractors.clean_text(slash.group(1))
        row['previous_school'] = FieldExtractors.clean_text(slash.group(2))
        return True

    # Transfer suffix: "<high school> (<transfer school>)", where the paren
    # part is a UC campus ('UC Houston') or a school carrying 'University'
    # ('University of Houston'). E.g. "Deer Park HS (University of Houston)".
    transfer = re.match(
        rf'^(.+?{_HS_MARKER})\s+\(((?:UC\s+.+)|(?:.*University.*))\)$',
        value, re.IGNORECASE)
    if transfer:
        row['high_school'] = FieldExtractors.clean_text(transfer.group(1))
        row['previous_school'] = FieldExtractors.clean_text(transfer.group(2))
        return True

    row['high_school'] = value
    row['previous_school'] = ''
    return True


def dedupe_school_fields(row: Dict) -> bool:
    """Clear previous_school when it duplicates high_school."""
    if not row.get('high_school') or not row.get('previous_school'):
        return False
    if _school_value_key(row['high_school']) != _school_value_key(row['previous_school']):
        return False
    row['previous_school'] = ''
    return True


# ============================================================================
# PARSER FUNCTIONS
# ============================================================================

def _collapse_duplicated_name(name: str) -> str:
    """Collapse a name cell that repeats itself (hidden + visible copies).

    Some Sidearm/Presto tables render 'First Last First Last' in one cell:
    a visually-hidden copy followed by the visible one.
    """
    tokens = ' '.join((name or '').split()).split(' ')
    n = len(tokens)
    if n >= 4 and tokens[:n // 2] == tokens[n // 2:]:
        return ' '.join(tokens[:n // 2])
    return ' '.join(tokens)


def split_town_high_school(town: Optional[str]) -> tuple:
    """Split a combined 'Hometown / High School' cell.

    Tolerates a missing '/' (falls back to a trailing parenthesized school,
    e.g. "La Jolla, Calif. (The Bishop's School)", then the whole string as
    the town with None for the high school) and a missing/None cell, so a
    malformed row never raises.
    """
    if not town:
        return '', None
    if '/' in town:
        parts = [x.strip() for x in town.split('/', maxsplit=1)]
        if len(parts) == 2:
            return parts[0], parts[1]
        return parts[0], None
    paren = re.match(r'(.+?)\s*\(([^)]+)\)\s*$', town)
    if paren:
        return paren.group(1).strip(), paren.group(2).strip()
    return town.strip(), None


def parse_roster_baskbl(team: Dict, html: BeautifulSoup, season: str) -> List[Player]:
    """Parse basketball-style roster"""
    roster = []
    er = tldextract.extract(team['url'])

    thead = html.find('thead')
    if not thead:
        logger.warning(f"No thead found for {team['team']}")
        return roster

    if team['ncaa_id'] == 255:
        cols = [x.text.strip() for x in thead.find_all('th') if x.text.strip() != 'Social']
    else:
        cols = [x.text.strip() for x in thead.find_all('th') if x.text.strip() if x.text.strip() != '']

    # Filter out unmapped columns and log warning
    mapped_cols = []
    unmapped_cols = []
    for c in cols:
        if c in HEADERS:
            mapped_cols.append(c)
        else:
            unmapped_cols.append(c)

    if unmapped_cols:
        logger.warning(f"Unmapped columns for {team['team']}: {unmapped_cols}")

    if not mapped_cols:
        logger.warning(f"No valid columns found for {team['team']}")
        return roster

    new_cols = [HEADERS[c] for c in mapped_cols]
    cols = mapped_cols  # Update cols to only include mapped ones
    # Index-based alignment: when a row carries one cell per header (including
    # unmapped ones, e.g. "Prev. College"), dropping an unmapped header from
    # new_cols while keeping its cell shifts every later field left by one.
    # Track which header indexes are mapped instead.
    header_map = [i for i, c in enumerate([x.text.strip() for x in thead.find_all('th')
                                           if x.text.strip()]) if c in HEADERS]

    tbody = html.find('tbody')
    if not tbody:
        logger.warning(f"No tbody found for {team['team']}")
        return roster
    raw_players = [x for x in tbody.find_all('tr')]

    for raw_player in raw_players:
        try:
            # Keep spans that wrap the cell's entire text (player names are
            # often <a><span>Name</span></a>); decompose only decorative spans.
            for td in raw_player.find_all('td'):
                span_texts = ' '.join(s.get_text(' ', strip=True) for s in td.find_all('span'))
                td_text = td.get_text(' ', strip=True)
                if td.find('span') is not None and span_texts and span_texts != td_text:
                    for span in td.find_all('span'):
                        span.decompose()
            row_cells = raw_player.find_all(['td', 'th'])
            if len(row_cells) == len(header_map):
                raw_player_list = [row_cells[i].text.strip() for i in header_map]
            else:
                raw_player_list = [x.text.strip() for x in raw_player.find_all('td')]
            if len(raw_player_list) <= 4:
                logger.info(f"Skipping short row {raw_player_list}")
                continue
            raw_player_list[4] = " ".join([x for x in raw_player_list[4].split()])
            if len(raw_player_list) < len(new_cols):
                name = " ".join([x.strip() for x in raw_player.find('a').text.replace("  ", "").strip().split()])
                raw_player_list.insert(1, name)
            player_dict = dict(zip(new_cols, raw_player_list))
            if 'high_school' not in player_dict:
                player_dict['town'], player_dict['high_school'] = split_town_high_school(player_dict.get('town'))
            if 'previous_school' not in player_dict:
                player_dict['previous_school'] = None

            roster.append(Player(
                team_id=team['ncaa_id'],
                team=team['team'],
                player_id=None,
                name=player_dict['name'],
                year=player_dict['academic_year'],
                hometown=player_dict['town'],
                high_school=player_dict['high_school'],
                previous_school=player_dict['previous_school'],
                height=player_dict['height'],
                position=player_dict['position'],
                jersey=player_dict['jersey'],
                url=f"https://www.{er.domain}.{er.suffix}{raw_player.find('a')['href']}",
                season=season
            ))
        except Exception as e:
            # One malformed row should skip itself, not abort the whole team.
            logger.warning(f"Error parsing baskbl-style row for {team['team']}: {e}")
            continue
    return roster


def parse_roster_wbkb(team: Dict, html: BeautifulSoup, season: str) -> List[Player]:
    """Parse women's basketball style roster with extensive filtering logic"""
    roster = []
    er = tldextract.extract(team['url'])

    # Find headers
    if team['ncaa_id'] == 30164:
        tables = html.find_all('table')
        if len(tables) < 2:
            logger.warning(f"No table found for {team['team']} (expected at least 2 tables)")
            return roster
        headers = tables[1].find_all('tr')[0]
    elif team['ncaa_id'] == 326:
        tables = html.find_all('table')
        if len(tables) < 13:
            logger.warning(f"No table found for {team['team']} (expected at least 13 tables)")
            return roster
        headers = tables[12].find_all('tr')[0]
    else:
        table = html.find('table')
        if not table:
            logger.warning(f"No table found for {team['team']}")
            return roster
        table_rows = table.find_all('tr')
        if not table_rows:
            logger.warning(f"No table rows found for {team['team']}")
            return roster
        headers = table_rows[0]

    cols = [x.text.strip() for x in headers if x.text.strip() != '']
    cols_with_major = list(cols)

    # Remove unwanted columns
    unwanted_cols = ['Pronounciation', 'Club Team', 'Major', 'MAJOR', 'Major/Minor',
                     'College', 'Wt.', 'Ltrs.', 'Pronouns', 'Instagram', 'Twitter',
                     'TikTok', 'X (Twitter)', 'Facebook']
    for col in unwanted_cols:
        if col in cols:
            cols.remove(col)

    # Filter out unmapped columns and log warning
    mapped_cols = []
    unmapped_cols = []
    for c in cols:
        if c in HEADERS:
            mapped_cols.append(c)
        else:
            unmapped_cols.append(c)

    if unmapped_cols:
        logger.warning(f"Unmapped columns for {team['team']}: {unmapped_cols}")

    if not mapped_cols:
        logger.warning(f"No valid columns found for {team['team']}")
        return roster

    new_cols = [HEADERS[c] for c in mapped_cols]
    # Index-based alignment: when a row carries one cell per header (including
    # unmapped ones, e.g. "Prev. College"), dropping an unmapped or unwanted
    # header from new_cols while keeping its cell shifts every later field
    # left. Track which ORIGINAL header positions are mapped and kept.
    header_map = [i for i, c in enumerate(cols_with_major)
                  if c in HEADERS and c not in unwanted_cols]
    cols = mapped_cols  # Update cols to only include mapped ones
    tbody = html.find('tbody')
    if not tbody:
        logger.warning(f"No tbody found for {team['team']}")
        return roster
    raw_players = [x for x in tbody.find_all('tr')]

    # Heuristic used to detect and drop a stray "Major" column when a table
    # includes one: if any cell matches a known major, that column is removed.
    # WARNING: this list is deliberately partial, so majors not listed here are
    # not detected and can leak into an adjacent field. The proper fix is to
    # drop the Major column by header (see `unwanted_cols` above) rather than by
    # value; this list is a fallback for tables whose Major header is unlabeled.
    major_list = [
        'Nursing', 'Biology', 'Public Health', 'Exercise Science', 'Pre-Nursing', 'Economics',
        'Physical Therapy', 'Psychology', 'Business Administration', 'Criminal Justice/Psychology',
        'Forensic Science', 'Undecided', 'Management', 'Psychology / Management',
        'Political Science', 'Psychology / Pre-Medicine', 'Undeclared', 'Biomedical Engineering',
        'Business Marketing', 'Chemistry', 'Business', 'Computer Science', 'Business Management',
    ]

    for raw_player in raw_players:
        try:
            # Remove decorative spans (icons, social links), but keep a span
            # that wraps the cell's entire text: some sites render the player
            # name inside <a><span>Name</span></a>, and decomposing that left
            # an empty cell whose removal shifted every later field.
            for td in raw_player.find_all('td'):
                span_texts = ' '.join(s.get_text(' ', strip=True) for s in td.find_all('span'))
                td_text = td.get_text(' ', strip=True)
                if td.find('span') is not None and span_texts and span_texts != td_text:
                    for span in td.find_all('span'):
                        span.decompose()
            # Sidearm responsive templates repeat one column in a mobile-only
            # cell (class d-md-none) beside a desktop-only twin (d-none
            # d-md-table-cell). Counting both breaks header alignment and
            # shifted the jersey number into the name column (Simpson
            # 2026-27); drop the mobile-only copy and keep the desktop twin.
            if raw_player.find('td', class_='d-md-none') is not None and \
                    raw_player.find('td', class_='d-md-table-cell') is not None:
                for cell in raw_player.find_all('td', class_='d-md-none'):
                    cell.decompose()
            raw_player_list = [x.text.strip() for x in raw_player.find_all('td')
                              if x.text.replace('*', '').replace('(she/her/hers)', '').replace('she/her/hers', '').strip() != '']
            row_cells_td_th = raw_player.find_all(['td', 'th'])
            aligned_row = len(row_cells_td_th) == len(cols_with_major)
            if aligned_row:
                # One cell per header (unmapped/unwanted columns included):
                # select only the mapped-and-kept cells by header index. This
                # also restores empty trailing cells that the nonempty filter
                # would have dropped, so fields never shift.
                cells = [row_cells_td_th[i].text.strip() for i in header_map]
                name_idx = next((k for k, c in enumerate(new_cols) if c == 'name'), None)
                if name_idx is not None:
                    cells[name_idx] = _collapse_duplicated_name(cells[name_idx])
                raw_player_list = cells
            else:
                all_cells = [x.text.strip() for x in raw_player.find_all('td')]
                if len(raw_player_list) < len(all_cells) and len(all_cells) == len(new_cols) \
                        and all_cells[0].isdigit():
                    # A genuinely empty cell was dropped by the filter; restore
                    # it so the positional zip stays aligned. Player rows only:
                    # staff rows (no leading jersey) stay on the filtered path,
                    # which drops them below.
                    raw_player_list = all_cells

            # Team-specific name insertion logic
            if not aligned_row and team['ncaa_id'] in [1340, 760, 510, 227]:
                raw_name = raw_player.find('a').text.strip().replace("  ", " ").replace("\t", "").replace("\r", "").replace("\n", "").split(' ')
                raw_player_list.insert(1, " ".join([x.strip() for x in raw_name if x != '']))

            # Skip specific problematic entries
            if not aligned_row and team['ncaa_id'] == 73 and len(raw_player_list) > 0 and raw_player_list[0] == '43':
                continue

            # Special handling for specific teams
            if not aligned_row and team['ncaa_id'] in [114, 1050, 1059, 1199, 22626, 24317, 30037, 341, 1315, 46, 641, 730, 75, 806, 817, 89, 145, 217, 247, 2713, 2798, 28594, 30002, 30225, 467, 567, 569, 137, 715, 779, 808, 8688, 379, 1461]:
                raw_player_list = [x.text.strip() for x in raw_player.find_all('td')]

            if len(raw_player_list) == 4:
                raw_player_list = [None] + raw_player_list
            elif len(raw_player_list) < 4:
                logger.info(f"Skipping {raw_player_list}")
                continue

            raw_player_list[4] = " ".join([x for x in raw_player_list[4].split()])

            # Team-specific adjustments (legacy layout quirks; the header-index
            # aligned path above needs none of these)
            if not aligned_row and team['ncaa_id'] == 1036 and len(raw_player_list) == len(new_cols):
                raw_player_list.pop()
            if not aligned_row and team['ncaa_id'] == 1096 and len(raw_player_list) >= len(new_cols):
                raw_player_list.pop()
            if not aligned_row and team['ncaa_id'] == 142:
                del raw_player_list[2]

            # Filter out major column if present
            if not aligned_row and any(major in raw_player_list for major in major_list):
                if team['ncaa_id'] == 186:
                    del raw_player_list[4]
                else:
                    raw_player_list.pop()

            if len(raw_player_list) < len(new_cols):
                name = " ".join([x.strip() for x in raw_player.find('a').text.replace("  ", "").strip().split()])
                raw_player_list.insert(1, name)

            player_dict = dict(zip(new_cols, raw_player_list))

            if 'high_school' not in player_dict:
                if team['ncaa_id'] == 2713:
                    player_dict['high_school'] = None
                else:
                    player_dict['town'], player_dict['high_school'] = split_town_high_school(player_dict.get('town'))

            if 'previous_school' not in player_dict:
                player_dict['previous_school'] = None

            roster.append(Player(
                team_id=team['ncaa_id'],
                team=team['team'],
                player_id=None,
                name=player_dict['name'],
                year=player_dict['academic_year'],
                hometown=player_dict['town'],
                high_school=player_dict['high_school'],
                previous_school=player_dict['previous_school'],
                height=player_dict['height'],
                position=player_dict['position'],
                jersey=player_dict['jersey'],
                url=f"https://www.{er.domain}.{er.suffix}{raw_player.find('a')['href']}",
                season=season
            ))
        except Exception as e:
            # One malformed row should skip itself, not abort the whole team.
            logger.warning(f"Error parsing wbkb-style row for {team['team']}: {e}")
            continue
    return roster


def parse_roster(team: Dict, html: BeautifulSoup, season: str) -> List[Player]:
    """Parse standard Sidearm roster (li.sidearm-roster-player format)"""
    roster = []
    er = tldextract.extract(team['url'])

    try:
        players = html.find_all('li', {'class': 'sidearm-roster-player'})
    except (AttributeError, TypeError):
        return []

    for player in players:
        position = None

        # Skip specific known bad entries. Addison Jeansonne appears as a
        # duplicate/placeholder card on one team's page.
        anchor = player.find('a')
        aria_label = anchor.get('aria-label', '') if anchor else ''
        if aria_label.split(' - ')[0].strip() == 'Addison Jeansonne':
            continue

        # Extract previous school
        previous_school = None
        if player.find('span', {'class': 'sidearm-roster-player-previous-school'}):
            previous_school = player.find('span', {'class': 'sidearm-roster-player-previous-school'}).text

        # Extract high school
        high_school = None
        if player.find('span', {'class': 'sidearm-roster-player-highschool'}):
            high_school_text = player.find('span', {'class': 'sidearm-roster-player-highschool'}).text.strip()
            high_school = " ".join([x.strip() for x in high_school_text.split(' ') if x != ''])

        # Extract height
        height = None
        if player.find('span', {'class': 'sidearm-roster-player-height'}):
            height = player.find('span', {'class': 'sidearm-roster-player-height'}).text

        # Extract hometown
        try:
            hometown = player.find('span', {'class': 'sidearm-roster-player-hometown'}).text.strip()
        except AttributeError:
            hometown = None

        # Extract position (with multiple fallback strategies)
        position_div = player.find('div', {'class': 'sidearm-roster-player-position'})
        if position_div is not None:
            position_text = position_div.text.strip()
            if position_text == '':
                position = 'N/A'
            if not position and '"' in position_text:
                position = position_text.split()[0]
            text_bold = position_div.find('span', {'class': 'text-bold'})
            if not position and text_bold is not None:
                long_short = text_bold.find(
                    'span', {'class': 'sidearm-roster-player-position-long-short hide-on-small-down'})
                if long_short is not None:
                    position = long_short.text.strip()
                else:
                    position = text_bold.text.strip()

        # Extract jersey
        try:
            jersey = player.find('span', {'class': 'sidearm-roster-player-jersey-number'}).text.strip()
        except AttributeError:
            jersey = None

        # Extract academic year
        try:
            academic_year = player.find_all('span', {'class': 'sidearm-roster-player-academic-year'})[1].text
        except (IndexError, AttributeError):
            academic_year = None

        # Extract name: prefer the anchor's aria-label, then <h3>, then the
        # newer .sidearm-roster-player-name div (which prefixes the jersey
        # number, e.g. "1 Jane Smith"). A card with none is skipped, not fatal.
        h3 = player.find('h3')
        h3_name = h3.text.strip() if h3 else ''
        name = aria_label.split(' - ')[0].strip() if aria_label else ''
        if not name or 'Instagram' in name:
            name = h3_name
        if not name:
            name_div = player.find(class_='sidearm-roster-player-name')
            if name_div:
                name = re.sub(r'^\s*\d+\s+', '', name_div.get_text(' ', strip=True))
        if not name:
            logger.info(f"Skipping player card with no name for {team['team']}")
            continue

        roster.append(Player(
            team_id=team['ncaa_id'],
            team=team['team'],
            player_id=player.get('data-player-id'),
            name=name,
            year=academic_year,
            hometown=hometown,
            high_school=high_school,
            previous_school=previous_school,
            height=height,
            position=position,
            jersey=jersey,
            url=f"https://www.{er.domain}.{er.suffix}{anchor.get('href', '') if anchor else ''}",
            season=season
        ))
    return roster


# ============================================================================
# JAVASCRIPT-RENDERED SCRAPERS
# ============================================================================

# These functions use shot-scraper to handle JavaScript-rendered content
# Each function targets specific team websites with custom selectors

_YEAR_WORDS = {
    'freshman', 'sophomore', 'junior', 'senior', 'graduate', 'redshirt',
    'first year', 'fifth year', 'grad student', 'r-freshman', 'r-sophomore',
    'r-junior', 'r-senior',
}


def parse_clemson_cards(html: BeautifulSoup, team: Dict, season: str) -> List[Player]:
    """Parse Clemson's rendered roster-card layout.

    Only player cards carry a jersey number, so staff cards are skipped. The
    bio fields (height, hometown, high school, class) share one repeated
    `info-item` class, so each is classified by its content rather than its
    position, which tolerates a missing field. Split from fetching so it is
    testable offline against a saved rendered-HTML fixture.
    """
    er = tldextract.extract(team['url'])
    roster, seen = [], set()
    for card in html.select('.roster-card'):
        jersey_el = card.select_one('.roster-card__jersey-number')
        if jersey_el is None:
            continue
        anchor = card.find('a')
        href = anchor.get('href', '') if anchor else ''
        if href and href in seen:
            continue
        seen.add(href)

        height = hometown = high_school = year = ''
        for item in card.select('.roster-players-cards-item__info-item'):
            text = item.get_text(' ', strip=True)
            if not text:
                continue
            if re.search(r"\d\s*['′\"″]", text):        # 6'3" / 6′3″
                height = text
            elif text.lower() in _YEAR_WORDS:
                year = text
            elif ',' in text and not hometown:          # City, ST
                hometown = text
            elif not high_school:
                high_school = text

        # Purdue / newer Nuxt variant: labeled profile fields and/or unlabeled
        # "basic" spans (first=height, second=year) when info-items are absent.
        if not height and not year:
            for field in card.select('.roster-player-card-profile-field'):
                label_el = field.select_one('.roster-player-card-profile-field__label')
                value_el = field.select_one('.roster-player-card-profile-field__value')
                if label_el and value_el:
                    key = label_el.get_text(strip=True)
                    val = value_el.get_text(strip=True)
                    if key == 'Height':
                        height = val
                    elif key == 'Class':
                        year = val
                    elif key == 'Hometown':
                        hometown = val
                    elif key == 'High School':
                        high_school = val
            if not height:
                basics = card.select('.roster-player-card-profile-field__value--basic')
                if basics:
                    height = basics[0].get_text(strip=True)
                if not year and len(basics) > 1:
                    year = basics[1].get_text(strip=True)

        name_el = card.select_one('.roster-card__title-link')
        pos_el = card.select_one('.roster-card__position')
        url = href if href.startswith('http') else f"https://www.{er.domain}.{er.suffix}{href}"
        roster.append(Player(
            team_id=team['ncaa_id'],
            team=team['team'],
            player_id=None,
            name=name_el.get_text(strip=True) if name_el else '',
            year=year,
            hometown=hometown,
            high_school=high_school or None,
            previous_school=None,
            height=height,
            position=pos_el.get_text(' ', strip=True) if pos_el else '',
            jersey=jersey_el.get_text(strip=True).lstrip('#'),
            url=url,
            season=season,
        ))
    return roster


def fetch_and_parse_clemson(team: Dict, season: str) -> List[Player]:
    """Clemson - modern Nuxt site (roster-card layout).

    Like Iowa State, the roster is client-rendered and only served at `/roster`
    (a `/roster/{season}` path 404s), so it is fetched through shot-scraper and
    parsed from the rendered HTML.
    """
    url = team['url'].rstrip('/') + '/roster'
    html = fetch_url_with_javascript(url)
    if html is None:
        return []
    if not verify_roster_season(html, season, team):
        return []
    return parse_clemson_cards(html, team, season)


def fetch_and_parse_vandy(team: Dict, season: str) -> List[Dict]:
    """Vanderbilt - JavaScript rendered"""
    javascript_code = """
    Array.from(document.querySelectorAll('#players-table tbody tr'), el => {
     const id = '';
     const name = el.querySelectorAll('td')[1].innerText;
     const year = el.querySelectorAll('td')[4].innerText;
     const height = el.querySelectorAll('td')[3].innerText;
     const position = el.querySelectorAll('td')[2].innerText;
     const hometown = el.querySelectorAll('td')[6].innerText;
     hs_el = el.querySelectorAll('td')[5];
     const high_school = hs_el ? hs_el.innerText : '';
     const previous_school = '';
     const jersey = el.querySelectorAll('td')[0].innerText;
     const url = el.querySelectorAll('td')[1].querySelector('a')['href']
     return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """
    for slug in season_url_slugs(season):
        result = shotscraper_caller(
            team, season, f"{team['url']}/roster/season/{slug}", javascript_code)
        if result:
            return result
    return []


def fetch_and_parse_miami(team: Dict, season: str) -> List[Dict]:
    """Miami - JavaScript rendered"""
    javascript_code = """
    Array.from(document.querySelectorAll('#players-table tbody tr'), el => {
     const id = '';
     const name = el.querySelectorAll('td')[1].innerText;
     const year = el.querySelectorAll('td')[4].innerText;
     const height = el.querySelectorAll('td')[3].innerText;
     const position = el.querySelectorAll('td')[2].innerText;
     const hometown = el.querySelectorAll('td')[5].innerText;
     hs_el = el.querySelectorAll('td')[6];
     const high_school = hs_el ? hs_el.innerText : '';
     ps_el = el.querySelectorAll('td')[7];
     const previous_school = ps_el ? ps_el.innerText : '';
     const jersey = el.querySelectorAll('td')[0].innerText;
     const url = el.querySelectorAll('td')[1].querySelector('a')['href']
     return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """
    for slug in season_url_slugs(season):
        result = shotscraper_caller(
            team, season, f"{team['url']}/roster/season/{slug}", javascript_code)
        if result:
            return result
    return []


def fetch_and_parse_byu(team: Dict, season: str) -> List[Dict]:
    """BYU - JavaScript rendered"""
    javascript_code = """
    Array.from(document.querySelectorAll('div.roster-players__group table tbody tr'), el => {
     const id = '';
     const name = el.querySelector('a').innerText;
     const year = el.querySelectorAll('td')[4].innerText;
     const height = el.querySelectorAll('td')[3].innerText;
     const position = el.querySelectorAll('td')[2].innerText;
     const hometown = el.querySelectorAll('td')[5].innerText;
     const high_school = el.querySelectorAll('td')[6].innerText;
     const previous_school = '';
     const jersey = el.querySelectorAll('td')[0].innerText;
     const url =el.querySelector("a")['href'];
     return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """
    for slug in season_url_slugs(season):
        result = shotscraper_caller(
            team, season, f"{team['url']}/roster/season/{slug}", javascript_code)
        if result:
            return result
    return []


def fetch_and_parse_sanjose(team: Dict, season: str) -> List[Dict]:
    """San Jose State - JavaScript rendered"""
    javascript_code = """
    Array.from(document.querySelectorAll('.roster__players .roster-card-item'), el => {
     const id = '';
     const name = el.querySelector('.roster-card-item__title-link').innerText;
     const year = el.querySelectorAll('.roster-player-card-profile-field__value')[1].innerText;
     const height = el.querySelectorAll('.roster-player-card-profile-field__value')[0].innerText;
     const position = el.querySelector('.roster-card-item__position').innerText;
     const hometown = el.querySelector(".roster-player-card-profile-field__value--hometown").innerText;
     hs_el = el.querySelector(".roster-player-card-profile-field__value--school");
     const high_school = hs_el ? hs_el.innerText : '';
     ps_el = el.querySelector(".roster-player-card-profile-field__value--previous_school");
     const previous_school = ps_el ? ps_el.innerText : '';
     const jersey = el.querySelector(".roster-card-item__jersey-number").innerText;
     const url =el.querySelector("a")['href'];
     return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """
    for slug in season_url_slugs(season):
        result = shotscraper_caller(
            team, season, f"{team['url']}/roster/season/{slug}", javascript_code)
        if result:
            return result
    return []


def parse_iowa_state_cards(html: BeautifulSoup, team: Dict, season: str) -> List[Player]:
    """Parse Iowa State's rendered s-person-card roster.

    Only player cards carry a jersey stamp, so coach/staff cards are skipped.
    The page renders each player twice (card + list view), so players are
    de-duplicated by URL. Split out from fetching so it is testable offline
    against a saved rendered-HTML fixture.
    """
    def strip_label(node, label):
        if node is None:
            return ''
        text = node.get_text(' ', strip=True)
        return text[len(label):].strip() if text.startswith(label) else text

    roster, seen = [], set()
    for card in html.select('.s-person-card__content'):
        jersey_el = card.select_one('.s-stamp__text')
        if jersey_el is None:
            continue
        anchor = card.find('a')
        url = anchor.get('href', '') if anchor else ''
        if url and url in seen:
            continue
        seen.add(url)

        bio = {}
        for item in card.select('.s-person-details__bio-stats-item'):
            text = item.get_text(' ', strip=True)
            for label in ('Position', 'Academic Year', 'Height'):
                if text.startswith(label):
                    bio[label] = text[len(label):].strip()

        name_el = card.select_one('.s-person-details__personal-single-line')
        roster.append(Player(
            team_id=team['ncaa_id'],
            team=team['team'],
            player_id=None,
            name=name_el.get_text(strip=True) if name_el else '',
            year=bio.get('Academic Year', ''),
            hometown=strip_label(card.select_one('.s-person-card__content__person__location-item'), 'Hometown'),
            high_school=strip_label(card.select_one('.s-person-card__content__person__high-school-item'), 'Last School'),
            previous_school=None,
            height=bio.get('Height', ''),
            position=bio.get('Position', ''),
            jersey=strip_label(jersey_el, 'Jersey Number'),
            url=url,
            season=season,
        ))
    return roster


def parse_prestosport_cards(html: BeautifulSoup, team: Dict, season: str) -> List[Player]:
    """PrestoSport player-card grid (e.g. Goucher 2025-26).

    Each ``.player-card`` has a front face (``.bio-card-name-and-year`` with
    firstname/lastname spans and a ``.number`` badge) and a flipped back face
    (``.bio-data``) holding a labelled field list: Position, Class, Height,
    Hometown, Highschool. Split from fetching so it is testable offline.
    """
    roster = []
    for card in html.select('.player-cards .player-card'):
        front = card.select_one('.bio-card-name-and-year')
        if front is None:
            continue
        anchor = card.find('a')
        url = anchor.get('href', '') if anchor else ''

        name = ''
        name_el = front.select_one('.name')
        if name_el is not None:
            first = name_el.select_one('.firstname')
            last = name_el.select_one('.lastname')
            name = ' '.join(x for x in (
                first.get_text(strip=True) if first else '',
                last.get_text(strip=True) if last else '') if x)

        jersey = None
        jersey_el = card.select_one('.card-front .number')
        if jersey_el is not None:
            jersey = jersey_el.get_text(strip=True).lstrip('#')

        year = height = position = hometown = high_school = None
        # The flipped card carries the full field list; the front's
        # .bio-attr-short spans (position, class, height) are the fallback.
        back = card.select_one('.bio-data')
        if back is not None:
            for li in back.find_all('li'):
                label_el = li.find('span', class_='fw-bold')
                if label_el is None:
                    continue
                label = label_el.get_text(strip=True).rstrip(':').lower()
                rest = li.get_text(' ', strip=True)
                prefix = label_el.get_text(strip=True)
                if rest.lower().startswith(prefix.lower()):
                    rest = rest[len(prefix):].lstrip(':').strip()
                rest = FieldExtractors.clean_text(rest)
                if label == 'position':
                    position = rest
                elif label in ('class', 'year'):
                    year = rest
                elif label == 'height':
                    height = rest
                elif label == 'hometown':
                    hometown = rest
                elif label in ('highschool', 'high school'):
                    high_school = rest
                elif label in ('previous school', 'last school'):
                    high_school = rest
        else:
            short = card.select_one('.bio-attr-short')
            if short is not None:
                spans = [s.get_text(strip=True) for s in short.find_all('span')]
                spans = [s for s in spans if s]
                if len(spans) == 3:
                    position, year, height = spans

        roster.append(Player(
            team_id=team['ncaa_id'],
            team=team['team'],
            player_id=None,
            name=name,
            year=year,
            hometown=hometown,
            high_school=high_school,
            previous_school=None,
            height=height,
            position=position,
            jersey=jersey,
            url=url,
            season=season,
        ))
    return roster


def parse_prestosport_table(html: BeautifulSoup, team: Dict, season: str) -> List[Player]:
    """PrestoSport data-table roster (e.g. Sage 2025-26).

    The table's data rows carry per-cell ``data-label``/``data-field``
    attributes and repeat each field label inside the cell text
    (``Yr.:Fr.``); player names sit in ``a[aria-label]`` (``Name: jersey
    number N: full bio``). The header row uses ``th[data-field-label]`` only
    and is skipped (no ``td[data-field]``), which also keeps staff tables
    without data attributes out of the result. There is often no height
    column; such fields stay None.
    """
    roster = []
    for tr in html.select('tr'):
        if tr.find('td', attrs={'data-field': True}) is None:
            continue
        cells = tr.find_all(['td', 'th'])
        fields: Dict[str, str] = {}
        for cell in cells:
            label = cell.get('data-label') or ''
            key = HEADERS.get(label)
            if not key:
                continue
            text = ' '.join(cell.get_text(' ', strip=True).split())
            # Cells repeat their label ('Yr.:Fr.'); strip it.
            if label:
                lc = label.lower()
                if text.lower().startswith(lc + ':'):
                    text = text[len(label) + 1:].strip()
                elif text.lower().startswith(lc):
                    text = text[len(label):].strip(' :')
            text = FieldExtractors.clean_text(text)
            fields[key] = text.strip()

        name = fields.get('name', '')
        # Prefer the bio anchor's aria-label ('Name: jersey number 1: full
        # bio'); the visible name spans repeat the name on both faces.
        anchor = None
        for cell in cells:
            if HEADERS.get(cell.get('data-label', '')) == 'name':
                anchor = cell.find('a')
                break
        if anchor is not None and anchor.get('aria-label'):
            name = anchor['aria-label'].split(':')[0].strip()
        name = _collapse_duplicated_name(name) if name else ''
        if not name:
            continue

        town = fields.get('town') or None
        high_school = fields.get('high_school') or None
        # PrestoSport folds town and high school into one 'Hometown/High
        # School' cell ('Massena, NY / X').
        if town and '/' in town and high_school is None:
            town, high_school = split_town_high_school(town)
        jersey = fields.get('jersey') or None
        roster.append(Player(
            team_id=team['ncaa_id'],
            team=team['team'],
            player_id=None,
            name=name,
            year=fields.get('academic_year') or None,
            hometown=town,
            high_school=high_school,
            previous_school=None,
            height=fields.get('height') or None,
            position=fields.get('position') or None,
            jersey=jersey,
            url=anchor.get('href') if anchor else None,
            season=season,
        ))
    return roster


def parse_nuxt_payload(html: BeautifulSoup, team: Dict, season: str) -> List[Player]:
    """Extract roster rows from a Sidearm/Nuxt page that did not hydrate.

    The browser DOM on such pages stays empty (the roster never renders), but
    the full player records live in ``<script id="__NUXT_DATA__">`` — a
    devalue-flattened JSON payload where most player fields resolve to plain
    strings at the first level. Records carry ``full_name`` plus either
    ``player_position`` or ``jersey_number``/``jersey_number_label``; staff
    records (athletic trainers, etc.) carry an email/title and neither of
    those, so they are skipped.
    """
    script = html.find('script', id='__NUXT_DATA__')
    if script is None or not (script.string or '').lstrip().startswith('['):
        return []
    try:
        payload = json.loads(script.string)
    except ValueError:
        return []
    n = len(payload)

    def first_level(v):
        """One devalue hop: an int is an index into the flat payload."""
        if isinstance(v, int) and -n <= v < n:
            return payload[v]
        return v

    roster, seen = [], set()
    for item in payload:
        if not isinstance(item, dict) or 'full_name' not in item:
            continue
        rec = {}
        for k, v in item.items():
            out = first_level(v)
            if isinstance(out, dict):
                out = {kk: first_level(vv) for kk, vv in out.items()}
            rec[k] = out
        name = rec.get('full_name')
        if not isinstance(name, str) or not name.strip():
            continue
        keys = rec.keys()
        has_position = isinstance(rec.get('player_position'), dict) and (
            rec['player_position'].get('name') or rec['player_position'].get('abbreviation'))
        has_jersey = isinstance(rec.get('jersey_number'), str) or \
            isinstance(rec.get('jersey_number_label'), str)
        if 'email' in keys or not (has_position or has_jersey):
            continue

        position = None
        if has_position:
            pp = rec['player_position']
            position = pp.get('name') or pp.get('abbreviation')

        height = None
        feet, inches = rec.get('height_feet'), rec.get('height_inches')
        if isinstance(feet, str) and feet.isdigit() and \
                isinstance(inches, str) and inches.isdigit():
            height = f"{feet}' {inches}''"

        year = rec.get('class_level')
        if not isinstance(year, str) or not year.strip() or 'None' in year:
            year = None

        slug = rec.get('slug')
        url = f"/roster/player/{slug}" if isinstance(slug, str) and slug else None

        key = name.strip()
        if key in seen:
            continue
        seen.add(key)
        roster.append(Player(
            team_id=team['ncaa_id'],
            team=team['team'],
            player_id=rec.get('id') if isinstance(rec.get('id'), str) else None,
            name=name,
            year=year,
            hometown=rec.get('hometown') if isinstance(rec.get('hometown'), str) else None,
            high_school=rec.get('high_school') if isinstance(rec.get('high_school'), str) else None,
            previous_school=None,
            height=height,
            position=position,
            jersey=rec.get('jersey_number') if isinstance(rec.get('jersey_number'), str)
                   else (rec.get('jersey_number_label') if isinstance(rec.get('jersey_number_label'), str) else None),
            url=url,
            season=season,
        ))
    return roster


def parse_nebraska_table(html: BeautifulSoup, team: Dict, season: str) -> List[Player]:
    """Parse Nebraska's rendered roster-table layout.

    Player rows are identified by a jersey-number cell (staff rows have none),
    and each field has its own dedicated `roster-table-cell--*` class. Split
    from fetching so it is testable offline against a saved rendered fixture.
    """
    def cell(row, cls):
        el = row.select_one('.' + cls)
        return el.get_text(' ', strip=True) if el else ''

    er = tldextract.extract(team['url'])
    roster = []
    for row in html.select('table tbody tr'):
        jersey = cell(row, 'roster-table-cell--jersey-number-label')
        if not jersey:
            continue
        anchor = row.select_one('a[href*="/roster/"]')
        href = anchor.get('href', '') if anchor else ''
        url = href if href.startswith('http') else f"https://www.{er.domain}.{er.suffix}{href}"
        roster.append(Player(
            team_id=team['ncaa_id'],
            team=team['team'],
            player_id=None,
            name=cell(row, 'roster-table-cell--full-name') or cell(row, 'table__roster-name'),
            year=cell(row, 'roster-table-cell--class-level-name'),
            hometown=cell(row, 'roster-table-cell--hometown'),
            high_school=cell(row, 'roster-table-cell--high-school') or None,
            previous_school=None,
            height=cell(row, 'roster-table-cell--height-text'),
            position=cell(row, 'roster-table-cell--player-position-name'),
            jersey=jersey,
            url=url,
            season=season,
        ))
    return roster


def fetch_and_parse_nebraska(team: Dict, season: str) -> List[Player]:
    """Nebraska - modern Nuxt site (roster-table layout).

    Client-rendered and only served at `/roster` (a `/roster/{season}` path
    404s), so it is fetched through shot-scraper and parsed from the render.
    """
    url = team['url'].rstrip('/') + '/roster'
    html = fetch_url_with_javascript(url)
    if html is None:
        return []
    if not verify_roster_season(html, season, team):
        return []
    return parse_nebraska_table(html, team, season)


def fetch_and_parse_iowa_state(team: Dict, season: str) -> List[Player]:
    """Iowa State - modern Nuxt site (s-person-card layout).

    The site only serves the current roster at `/roster`; a `/roster/{season}`
    path renders an unresolved template and 404s. The page is client-rendered,
    so it is fetched through shot-scraper and parsed from the rendered HTML.
    """
    url = f"{team['url']}/roster"
    html = fetch_url_with_javascript(url)
    if html is None:
        return []
    if not verify_roster_season(html, season, team):
        return []
    return parse_iowa_state_cards(html, team, season)


def shotscraper_airforce(team: Dict, season: str) -> List[Dict]:
    """Air Force - JavaScript rendered with s-person-card format"""
    javascript_code = """
    Array.from(document.querySelectorAll('.s-person-card'), el => {
        // Skip coaches - they don't have jersey numbers
        const jerseyEl = el.querySelector('span.s-stamp__text');
        if (!jerseyEl) return null;
        
        const id = '';
        const name = el.querySelector('.s-person-details__personal-single-line')?.innerText ?? '';
        
        const bioStats = el.querySelectorAll('.s-person-details__bio-stats span');
        const year = bioStats[2]?.innerText?.split('\\n')?.[1]?.trim() ?? '';
        const height = bioStats[4]?.innerText?.split('\\n')?.[1]?.trim() ?? '';
        const position = bioStats[0]?.innerText?.split('\\n')?.[1]?.trim() ?? '';
        
        const locationItems = el.querySelectorAll('span.s-person-card__content__person__location-item');
        const hometown = locationItems[0]?.innerText?.replace("Hometown\\n", "") ?? '';
        const high_school = locationItems[1]?.innerText?.split('\\n')?.[1] ?? '';
        
        const previous_school = '';
        const stampText = jerseyEl?.innerText ?? '';
        const jersey = stampText.split('\\n').pop()?.trim() ?? stampText.trim();
        if (!jersey || !/^\\d+$/.test(jersey)) return null;
        const url = el.querySelector('a')?.href ?? '';
        
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    }).filter(item => item !== null)
    """
    result = shotscraper_caller(team, season, f"{team['url']}/roster/{season}", javascript_code)
    if not result:
        result = shotscraper_caller(team, season, f"{team['url']}/roster", javascript_code)
    return result


def parse_roster_list_item(html: BeautifulSoup, team: Dict, season: str) -> List[Player]:
    """Parse Nuxt roster-list-item layout (Iowa, Notre Dame, Missouri, Northwestern, …).

    Rendered DOM: <li class="roster-list-item"> with nested profile-field spans.
    """
    er = tldextract.extract(team['url'])
    players = []
    seen_urls: set = set()
    for li in html.find_all('li', class_='roster-list-item'):
        # Iowa-style pages use an anchor classed roster-list-item__title; the
        # UTSA variant uses roster-list-item__title-link with a separate
        # span.roster-list-item__number for the jersey.
        a_title = (li.find('a', class_='roster-list-item__title') or
                   li.find('a', class_='roster-list-item__title-link'))
        if not a_title:
            continue
        name = a_title.get_text(strip=True)
        if not name:
            continue
        href = a_title.get('href', '')
        url = f"https://www.{er.domain}.{er.suffix}{href}"
        if url in seen_urls:
            continue
        seen_urls.add(url)
        player_id = href.rstrip('/').split('/')[-1] if href else None

        jersey_el = li.find('strong', class_='roster-list-item__jersey-number')
        jersey = jersey_el.get_text(strip=True) if jersey_el else ''

        year_el = li.find('strong', class_='roster-player-list-profile-field--class-level')
        year = year_el.get_text(strip=True) if year_el else ''

        height_el = li.find('span', class_='roster-player-list-profile-field--height')
        if height_el:
            inner = height_el.find('span')
            height = inner.get_text(strip=True) if inner else height_el.get_text(strip=True)
        else:
            height = ''

        pos_el = li.find('strong', class_='roster-player-list-profile-field--position')
        position = pos_el.get_text(strip=True) if pos_el else 'N/A'

        ht_el = li.find('span', class_='roster-player-list-profile-field--hometown')
        hometown = ht_el.get_text(strip=True) if ht_el else ''

        hs_el = li.find('span', class_='roster-player-list-profile-field--high-school')
        high_school = hs_el.get_text(strip=True) if hs_el else None

        ps_el = li.find('span', class_='roster-player-list-profile-field--previous-school')
        previous_school = ps_el.get_text(strip=True) if ps_el else ''

        # UTSA-style variant: strong.roster-players-list-item__position carries
        # the position and two .roster-players-list-item__profile-fields groups
        # hold unlabeled spans in a fixed order — height, year, then hometown,
        # high school and (optionally) previous school.
        if not year and not height and pos_el is None:
            position_el = li.find('strong', class_='roster-players-list-item__position')
            if position_el is not None:
                groups = li.select('.roster-players-list-item__profile-fields')
                # Group 1: unlabeled spans are height and academic year;
                # later groups: hometown, high school, previous school.
                g1_spans = [s.get_text(strip=True)
                            for s in groups[0].find_all('span', class_='roster-players-list-item__profile-field')] if groups else []
                height = next((s for s in g1_spans if re.fullmatch(r"\d\s*[′″'\"\d]+", s)), '')
                year = next((s for s in g1_spans
                             if s != height and re.search(r'(?i)fresh|soph|junior|senior|grad|redshirt', s)), '')
                rest: List[str] = [s.get_text(strip=True)
                                   for grp in groups[1:]
                                   for s in grp.find_all('span', class_='roster-players-list-item__profile-field')]
                position = position_el.get_text(strip=True) or 'N/A'
                hometown = rest[0] if rest else ''
                high_school = rest[1] if len(rest) > 1 else None
                previous_school = rest[2] if len(rest) > 2 else ''

        jersey_num_el = li.find('span', class_='roster-list-item__number')
        if jersey_num_el is not None:
            jersey = jersey_num_el.get_text(strip=True)

        if not (position != 'N/A' or year or height or hometown):
            continue  # staff/coach cards carry no roster fields

        players.append(Player(
            team_id=team['ncaa_id'],
            team=team['team'],
            player_id=player_id,
            name=name,
            year=year,
            hometown=hometown,
            high_school=high_school,
            previous_school=previous_school,
            height=height,
            position=position,
            jersey=jersey,
            url=url,
            season=season,
        ))
    return players


def parse_s_table_row(html: BeautifulSoup, team: Dict, season: str) -> List[Player]:
    """Parse Nuxt s-table-body__row layout (Arizona, Mississippi State, Rutgers, Toledo, …).

    Rendered DOM: <tr class="s-table-body__row"> with fixed column order:
    photo | jersey | name | position | height | year | hometown | hs | prev_school
    """
    er = tldextract.extract(team['url'])
    players = []
    for row in html.find_all('tr', class_='s-table-body__row'):
        cells = row.find_all('td')
        if len(cells) < 5:
            continue
        jersey = cells[1].get_text(strip=True) if len(cells) > 1 else ''
        if not jersey.isdigit():
            continue  # skip header / staff rows
        name_cell = cells[2] if len(cells) > 2 else None
        if not name_cell:
            continue
        a = name_cell.find('a')
        name = a.get_text(strip=True) if a else name_cell.get_text(strip=True)
        if not name:
            continue
        href = a.get('href', '') if a else ''
        url = f"https://www.{er.domain}.{er.suffix}{href}" if href else ''
        player_id = href.rstrip('/').split('/')[-1] if href else None

        position = cells[3].get_text(strip=True) if len(cells) > 3 else 'N/A'
        height = cells[4].get_text(strip=True) if len(cells) > 4 else ''
        year = cells[5].get_text(strip=True) if len(cells) > 5 else ''
        hometown = cells[6].get_text(strip=True) if len(cells) > 6 else ''
        hs_text = cells[7].get_text(strip=True) if len(cells) > 7 else ''
        high_school = hs_text if hs_text else None
        previous_school = cells[8].get_text(strip=True) if len(cells) > 8 else ''

        players.append(Player(
            team_id=team['ncaa_id'],
            team=team['team'],
            player_id=player_id,
            name=name,
            year=year,
            hometown=hometown,
            high_school=high_school,
            previous_school=previous_school,
            height=height,
            position=position,
            jersey=jersey,
            url=url,
            season=season,
        ))
    return players


def parse_roster_table_body_row(html: BeautifulSoup, team: Dict, season: str) -> List[Player]:
    """Parse the Nuxt roster-table layout used by Notre Dame (2026-27).

    Rendered DOM: visible <tr class="roster-table__body-row"> per player with a
    fixed cell order — jersey | name (th) | position | height | class |
    hometown | high school | social | toggle. Each player is followed by a
    hidden expandable bio row (<tr class="roster-table__body-row--hidden">),
    which is skipped. Split from fetching so it is testable offline against a
    saved rendered fixture.
    """
    er = tldextract.extract(team['url'])
    players = []
    for row in html.select('tr.roster-table__body-row'):
        if 'roster-table__body-row--hidden' in (row.get('class') or []):
            continue
        cells = row.find_all('td', class_='roster-table__body-cell', recursive=False)
        if len(cells) < 5:
            continue
        jersey = cells[0].get_text(strip=True)
        if not jersey.isdigit():
            continue  # staff / header rows carry no jersey number
        th = row.find('th')
        name_a = th.find('a') if th else None
        name = name_a.get_text(strip=True) if name_a else th.get_text(strip=True) if th else ''
        if not name:
            continue

        def cell_text(td):
            return td.get_text(' ', strip=True)

        href = name_a.get('href', '') if name_a else ''
        url = f"https://www.{er.domain}.{er.suffix}{href}" if href else ''
        player_id = href.rstrip('/').split('/')[-1] if href else None
        players.append(Player(
            team_id=team['ncaa_id'],
            team=team['team'],
            player_id=player_id,
            name=name,
            year=cell_text(cells[3]),
            hometown=cell_text(cells[4])
                       if len(cells) > 4 else '',
            high_school=cell_text(cells[5])
                        if len(cells) > 5 else None,
            previous_school='',
            height=cell_text(cells[2]) if len(cells) > 2 else '',
            position=cell_text(cells[1]) if len(cells) > 1 else 'N/A',
            jersey=jersey,
            url=url,
            season=season,
        ))
    return players


def parse_sidearm_list_item(html: BeautifulSoup, team: Dict, season: str) -> List[Player]:
    """Parse Sidearm's .sidearm-roster-list-item layout (Bradley, …).

    Static twin of shotscraper_list_item: the same markup is often served
    server-side, so it is parsed without the shot-scraper round-trip.
    """
    er = tldextract.extract(team['url'])
    players = []
    seen_urls: set = set()
    for li in html.find_all('li', class_='sidearm-roster-list-item'):
        name_el = li.find('span', class_='sidearm-roster-player-name') or \
                  li.find('div', class_='sidearm-roster-player-name')
        if name_el is None:
            name_el = li.find(class_='sidearm-roster-player-name')
        if name_el is None:
            continue
        name = name_el.get_text(strip=True)
        if not name:
            continue

        def field(cls):
            el = li.find(class_=cls)
            return el.get_text(strip=True) if el else ''

        anchor = li.find('a', href=True)
        href = anchor.get('href', '') if anchor else ''
        url = f"https://www.{er.domain}.{er.suffix}{href}" if href else ''
        player_id = (href.rstrip('/').split('/')[-1] or None) if href else None

        key = url or name
        if key in seen_urls:
            continue
        seen_urls.add(key)

        players.append(Player(
            team_id=team['ncaa_id'],
            team=team['team'],
            player_id=player_id,
            name=name,
            year=field('sidearm-roster-list-item-year'),
            hometown=field('sidearm-roster-list-item-hometown'),
            high_school=field('sidearm-roster-list-item-highschool') or None,
            previous_school=field('sidearm-roster-list-item-previous-school'),
            height=field('sidearm-roster-list-item-height'),
            position=field('sidearm-roster-list-item-position'),
            jersey=field('sidearm-roster-list-item-photo-number'),
            url=url,
            season=season,
        ))
    return players


def parse_rosters_list_item(html: BeautifulSoup, team: Dict, season: str) -> List[Player]:
    """Parse the Nuxt .rosters-list-item layout (Washington State, …).

    Newer variant of roster-list-item: each player is an <li class=
    "rosters-list-item">. Fields come in two .roster-list-item__fields-item
    groups — the first holds <strong> position + <span> height, the second
    holds <strong> academic year + spans for hometown, high school and
    previous school. The jersey is a # prefixed strong.rosters-list-item__number.
    """
    er = tldextract.extract(team['url'])
    players = []
    seen_urls: set = set()
    for li in html.find_all('li', class_='rosters-list-item'):
        a_title = li.find('a', class_='rosters-list-item__title-link') or \
                  li.find('a', class_='roster-list-item__title')
        if not a_title:
            continue
        name = a_title.get_text(strip=True)
        if not name:
            continue
        href = a_title.get('href', '')
        url = f"https://www.{er.domain}.{er.suffix}{href}" if href else ''
        player_id = href.rstrip('/').split('/')[-1] if href else None
        if url and url in seen_urls:
            continue
        seen_urls.add(url)

        jersey_el = li.find('strong', class_='rosters-list-item__number')
        jersey = jersey_el.get_text(strip=True).lstrip('#') if jersey_el else ''

        height = year = position = ''
        rest: List[str] = []
        fielditems = li.select('.roster-list-item__fields-item')
        if not fielditems:
            continue  # coach/staff cards carry no field groups
        first = fielditems[0]
        strongs = first.find_all('strong')
        spans = [s.get_text(strip=True) for s in first.find_all('span')]
        position = strongs[0].get_text(strip=True) if strongs else ''
        height = next((s for s in spans if s), '')
        for field_el in fielditems[1:]:
            strongs = field_el.find_all('strong')
            spans = [s.get_text(strip=True) for s in field_el.find_all('span') if s.get_text(strip=True)]
            year = strongs[0].get_text(strip=True) if strongs else ''
            rest.extend(spans)

        hometown = rest[0] if rest else ''
        high_school = rest[1] if len(rest) > 1 else None
        previous_school = rest[2] if len(rest) > 2 else ''

        players.append(Player(
            team_id=team['ncaa_id'],
            team=team['team'],
            player_id=player_id,
            name=name,
            year=year,
            hometown=hometown,
            high_school=high_school,
            previous_school=previous_school,
            height=height,
            position=position,
            jersey=jersey,
            url=url,
            season=season,
        ))
    return players


def parse_roster_card_item(html: BeautifulSoup, team: Dict, season: str) -> List[Player]:
    """Parse Nuxt roster-card-item layout (Penn State, Stanford, Virginia Tech, …).

    Two variants share the div.roster-card-item container:
    - Penn State: roster-card-jersey-number (with '#'), roster-card-item__title-link,
      div.roster-card-item__position, profile-field-content spans
    - Stanford/VT: roster-card-item__jersey-number (no '#'), roster-card-item__title,
      strong.roster-card-item__position, roster-player-card-profile-field divs
    """
    er = tldextract.extract(team['url'])
    players = []
    for card in html.find_all('div', class_='roster-card-item'):
        # Name: Penn State uses __title-link, Stanford uses __title (also matches __title--link)
        a = (card.find('a', class_='roster-card-item__title-link') or
             card.find('a', class_='roster-card-item__title'))
        if not a:
            img = card.find('img')
            name = img.get('alt', '').strip() if img else ''
        else:
            name = a.get_text(strip=True)
        if not name:
            continue

        href_el = card.find('a', class_='roster-card-item__image-wrapper') or card.find('a', href=True)
        href = href_el.get('href', '') if href_el else ''
        url = f"https://www.{er.domain}.{er.suffix}{href}" if href else ''
        player_id = href.rstrip('/').split('/')[-1] if href else None

        # Jersey: Penn State has roster-card-jersey-number (with '#'); Stanford has roster-card-item__jersey-number
        jersey_el = (card.find('strong', class_='roster-card-jersey-number') or
                     card.find('strong', class_='roster-card-item__jersey-number'))
        jersey = jersey_el.get_text(strip=True).lstrip('#') if jersey_el else ''

        # Position: Penn State uses div, Stanford/VT use strong
        pos_el = (card.find('div', class_='roster-card-item__position') or
                  card.find('strong', class_='roster-card-item__position'))
        position = pos_el.get_text(strip=True) if pos_el else 'N/A'

        fields: Dict[str, str] = {}
        # Penn State: span.profile-field-content with strong title + span value
        for field in card.find_all('span', class_='profile-field-content'):
            title_el = field.find('strong', class_='profile-field-content__title')
            value_el = field.find('span', class_='profile-field-content__value')
            if title_el and value_el:
                fields[title_el.get_text(strip=True)] = value_el.get_text(strip=True)
        # Stanford/VT: div.roster-player-card-profile-field with strong label + span value
        for field in card.find_all('div', class_='roster-player-card-profile-field'):
            label_el = field.find('strong', class_='roster-player-card-profile-field__label')
            value_el = field.find('span', class_='roster-player-card-profile-field__value')
            if label_el and value_el:
                fields[label_el.get_text(strip=True)] = value_el.get_text(strip=True)
        # Stanford: unlabeled height in the "basic" profile fields section
        if 'Height' not in fields:
            basic = card.find('span', class_='roster-player-card-profile-field__value--basic')
            if basic:
                fields['Height'] = basic.get_text(strip=True)

        players.append(Player(
            team_id=team['ncaa_id'],
            team=team['team'],
            player_id=player_id,
            name=name,
            year=fields.get('Class', ''),
            hometown=fields.get('Hometown', ''),
            high_school=fields.get('High School') or None,
            previous_school=fields.get('Previous School', ''),
            height=fields.get('Height', ''),
            position=position,
            jersey=jersey,
            url=url,
            season=season,
        ))
    return players


def parse_roster_card_div(html: BeautifulSoup, team: Dict, season: str) -> List[Player]:
    """Parse Nuxt div.roster-card layout (Purdue, …).

    Distinct from parse_clemson_cards which targets li.roster-card. Here the
    container is a <div> with basic unlabeled fields: first=height, second=year.
    """
    er = tldextract.extract(team['url'])
    players = []
    for card in html.find_all('div', class_='roster-card'):
        a = card.find('a', class_='roster-card__title-link')
        if not a:
            img = card.find('img')
            name = img.get('alt', '').strip() if img else ''
        else:
            name = a.get_text(strip=True)
        if not name:
            continue

        href_el = card.find('a', class_='roster-card__image-wrapper') or card.find('a', href=True)
        href = href_el.get('href', '') if href_el else ''
        url = f"https://www.{er.domain}.{er.suffix}{href}" if href else ''
        player_id = href.rstrip('/').split('/')[-1] if href else None

        jersey_el = card.find('strong', class_='roster-card__jersey-number')
        jersey = jersey_el.get_text(strip=True).lstrip('#') if jersey_el else ''
        if jersey and not jersey.isdigit():
            continue  # skip staff rows

        pos_el = card.find('span', class_='roster-card__position')
        position = pos_el.get_text(strip=True) if pos_el else 'N/A'

        basics = card.find_all('span', class_='roster-player-card-profile-field__value--basic')
        height = basics[0].get_text(strip=True) if len(basics) > 0 else ''
        year = basics[1].get_text(strip=True) if len(basics) > 1 else ''

        fields: Dict[str, str] = {}
        for field in card.find_all('div', class_='roster-player-card-profile-field'):
            label_el = field.find('strong', class_='roster-player-card-profile-field__label')
            value_el = field.find('span', class_='roster-player-card-profile-field__value')
            if label_el and value_el:
                fields[label_el.get_text(strip=True)] = value_el.get_text(strip=True)

        players.append(Player(
            team_id=team['ncaa_id'],
            team=team['team'],
            player_id=player_id,
            name=name,
            year=fields.get('Class', year),
            hometown=fields.get('Hometown', ''),
            high_school=fields.get('High School') or None,
            previous_school=fields.get('Previous School', ''),
            height=fields.get('Height', height),
            position=position,
            jersey=jersey,
            url=url,
            season=season,
        ))
    return players


# ============================================================================
# SHOT-SCRAPER HELPERS
# ============================================================================

def shotscraper_list_item(team: Dict, season: str) -> List[Dict]:
    """Scraper for .sidearm-roster-list-item format"""
    javascript_code = """
    Array.from(document.querySelectorAll('.sidearm-roster-list-item'), el => {
     const id = '';
     const name = el.querySelector('.sidearm-roster-player-name')?.innerText ?? '';
     const year = el.querySelector('.sidearm-roster-list-item-year')?.innerText ?? '';
     const height = el.querySelector('.sidearm-roster-list-item-height')?.innerText ?? '';
     const position = el.querySelector('.sidearm-roster-list-item-position')?.innerText ?? '';
     const hometown = el.querySelector('.sidearm-roster-list-item-hometown')?.innerText ?? '';
     const high_school = el.querySelector('.sidearm-roster-list-item-highschool')?.innerText ?? '';
     const previous_school = el.querySelector('.sidearm-roster-list-item-previous-school')?.innerText ?? '';
     const jersey = el.querySelector('.sidearm-roster-list-item-photo-number')?.innerText ?? '';
     const url = el.querySelector('.sidearm-roster-player-name a')?.href ?? '';
     return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """
    result = shotscraper_caller(team, season, f"{team['url']}/roster/{season}", javascript_code)
    if not result:
        result = shotscraper_caller(team, season, f"{team['url']}/roster", javascript_code)
    return result


def shotscraper_data_tables(team: Dict, season: str) -> List[Dict]:
    """Scraper for DataTables format"""
    javascript_code = """
    Array.from(document.querySelectorAll('#DataTables_Table_0 tbody tr'), el => {
     const id = '';
     const name = el.querySelector(".sidearm-table-player-name")?.innerText ?? '';
     const year = el.querySelector(".roster_class")?.innerText ?? '';
     const height = el.querySelector(".height")?.innerText ?? '';
     const position = el.querySelector(".rp_position_short")?.innerText ?? '';
     const hometownhs = el.querySelector(".hometownhighschool")?.innerText ?? '';
     const hometown = hometownhs.split('/')[0].trim();
     const hs = hometownhs.split('/');
     const high_school = hs[1] ? hs[1].trim() : '';
     const previous_school = el.querySelector(".player_previous_school")?.innerText ?? '';
     const jersey = el.querySelector(".roster_jerseynum")?.innerText ?? '';
     const url = el.querySelector(".sidearm-table-player-name a")?.href ?? '';
     return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """
    result = shotscraper_caller(team, season, f"{team['url']}/roster/{season}", javascript_code)
    if not result:
        result = shotscraper_caller(team, season, f"{team['url']}/roster", javascript_code)
    return result


def shotscraper_card(team: Dict, season: str) -> List[Dict]:
    """Scraper for .s-person-card__content format"""
    javascript_code = """
    Array.from(document.querySelectorAll('.s-person-card__content'), el => {
        const id = '';
        const bioStats = el.querySelectorAll('.s-person-details__bio-stats-item');
        const locationItems = el.querySelectorAll('.s-person-card__content__person__location-item');
        const name = el.querySelector('.s-person-details__personal-single-line')?.innerText ?? '';
        const year = bioStats[1]?.innerText?.replace('Academic Year\\n ','') ?? '';
        const height = bioStats[2]?.innerText?.replace('Height\\n ','') ?? '';
        const position = bioStats[0]?.innerText?.replace('Position\\n','') ?? '';
        const hometown = locationItems[0]?.innerText?.replace('Hometown\\n','') ?? '';
        const high_school = locationItems[1]?.innerText?.replace('Last School\\n','') ?? '';
        const previous_school = '';
        const stampText = el.querySelector('.s-stamp__text')?.innerText ?? '';
        const jersey = stampText.split('\\n').pop()?.trim() ?? stampText.trim();
        const url = el.querySelector('a[href*="/roster/"]')?.href ?? el.querySelector('a')?.href ?? '';
        if (!jersey || !/^\\d+$/.test(jersey)) return null;
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    }).filter(Boolean)
    """
    result = shotscraper_caller(team, season, f"{team['url']}/roster/{season}", javascript_code)
    if not result:
        result = shotscraper_caller(team, season, f"{team['url']}/roster", javascript_code)
    return result


def shotscraper_table(team: Dict, season: str) -> List[Dict]:
    """Scraper for .s-table-body__row format"""
    javascript_code = """
    Array.from(document.querySelectorAll('.s-table-body__row'), el => {
        const id = '';
        const cells = el.querySelectorAll('td');
        const name = cells[1]?.innerText ?? '';
        const year = cells[2]?.innerText ?? '';
        const height = cells[3]?.innerText ?? '';
        const position = cells[4]?.innerText ?? '';
        const hometown = cells[5]?.innerText ?? '';
        const high_school = cells[6]?.innerText ?? '';
        const previous_school = cells[7]?.innerText ?? '';
        const jersey = cells[0]?.innerText ?? '';
        const url = cells[1]?.querySelector('a')?.href ?? '';
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """
    result = shotscraper_caller(team, season, f"{team['url']}/roster/{season}", javascript_code)
    if not result:
        result = shotscraper_caller(team, season, f"{team['url']}/roster", javascript_code)
    return result


def shotscraper_roster_player(team: Dict, season: str) -> List[Dict]:
    """Scraper for .sidearm-roster-player-container format"""
    javascript_code = """
    Array.from(document.querySelectorAll('.sidearm-roster-player-container'), el => {
        const id = '';
        const name = el.querySelector('h3')?.innerText ?? '';
        const year = el.querySelector('.sidearm-roster-player-academic-year')?.innerText ?? '';
        const height = el.querySelector('.sidearm-roster-player-height')?.innerText ?? '';
        const position = el.querySelector('.sidearm-roster-player-position-long-short')?.innerText?.trim() ?? '';
        const hometown = el.querySelector('.sidearm-roster-player-hometown')?.innerText ?? '';
        const high_school = el.querySelector('.sidearm-roster-player-highschool')?.innerText ?? '';
        const previous_school = el.querySelector('.sidearm-roster-player-previous-school')?.innerText ?? '';
        const jersey = el.querySelector('.sidearm-roster-player-jersey-number')?.innerText ?? '';
        const url = el.querySelector('a')?.href ?? '';
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """
    result = shotscraper_caller(team, season, f"{team['url']}/roster/{season}", javascript_code)
    if not result:
        result = shotscraper_caller(team, season, f"{team['url']}/roster", javascript_code)
    return result


# ============================================================================
# MAIN SCRAPING LOGIC
# ============================================================================

CSV_HEADER = ['ncaa_id', 'team', 'player_id', 'name', 'year', 'hometown',
              'high_school', 'previous_school', 'height', 'position', 'jersey',
              'url', 'season']


def standard_fallback(team: Dict, season: str) -> List:
    """Standard fetch + parse, used when a shot-scraper strategy returns nothing."""
    logger.info(f"Shotscraper failed for {team['team']}, trying standard fetch")
    html = fetch_roster(team['url'], season)
    if html is None or not verify_roster_season(html, season, team):
        return []
    return parse_roster(team, html, season)


def _with_fallback(scraper):
    """Wrap a shot-scraper so an empty result falls back to a standard fetch."""
    def run(team, season):
        return scraper(team, season) or standard_fallback(team, season)
    return run


def _scrape_byu(team: Dict, season: str) -> List:
    # BYU uses a four-digit end year (e.g. "2025-26" -> "2025-2026"); derive
    # it from the canonical academic-year label when it arrives year-only.
    if re.fullmatch(r'\d{4}', season):
        byu_season = f"{season}-{int(season) + 1}"
    else:
        byu_season = f"{season[0:5]}20{season[5:7]}"
    return fetch_and_parse_byu(team, byu_season)


# Candidate parsers tried, in order, against rendered HTML. Each looks for a
# distinct marker (Sidearm <li>, s-person-card, roster-card, roster-table, then
# the table parsers), so a page matches at most one. Ordered most- to
# least-specific to avoid a generic table parser claiming a card page. All are
# normalized to (html, team, season) -> List[Player].
RENDERED_PARSERS = [
    ('nuxt-data', parse_nuxt_payload),
    ('sidearm', lambda html, team, season: parse_roster(team, html, season)),
    ('s-person-card', parse_iowa_state_cards),
    ('roster-card', parse_clemson_cards),
    ('prestosport-cards', parse_prestosport_cards),
    ('prestosport-table', parse_prestosport_table),
    ('roster-table', parse_nebraska_table),
    ('roster-table-body-row', parse_roster_table_body_row),
    ('roster-list-item', parse_roster_list_item),
    ('rosters-list-item', parse_rosters_list_item),
    ('sidearm-list-item', parse_sidearm_list_item),
    ('s-table-row', parse_s_table_row),
    ('roster-card-item', parse_roster_card_item),
    ('sidearm-table', lambda html, team, season: parse_roster_wbkb(team, html, season)),
    ('baskbl-table', lambda html, team, season: parse_roster_baskbl(team, html, season)),
]


def _best_parse(html: BeautifulSoup, team: Dict, season: str) -> List:
    """Return the first non-empty roster any candidate parser produces."""
    for name, parser in RENDERED_PARSERS:
        try:
            roster = parser(html, team, season)
        except Exception as e:
            logger.debug(f"{team['team']}: parser {name} raised {e}")
            continue
        if roster:
            logger.info(f"{team['team']}: matched layout '{name}' ({len(roster)} players)")
            return roster
    return []


def _scrape_js_render(team: Dict, season: str) -> List:
    """Render the page and self-heal across roster layouts.

    Sidearm sites have migrated to several client-rendered layouts, some of
    which only serve the current roster at `/roster` (a `/roster/{season}` path
    404s). So try both URL forms, and against each render try every known
    layout parser, using the first that finds players.
    """
    base = team['url'].rstrip('/')
    # Legacy homepages end in "/index"; for those, also sweep the trimmed base
    # (Sidearm sites want /roster without the suffix, PrestoSport /{season}/roster).
    bases = [base]
    if base.endswith('/index'):
        bases.append(base[:-len('/index')])
    # Sites that reorganize their sport slugs leave stale team URLs redirecting
    # to the new path. Season-qualified archive URLs only exist under the new
    # slug (the bare /roster redirect is what current-season runs used to get
    # by on), so resolve the redirect and sweep there too.
    resolved = _resolved_roster_base(base)
    if resolved:
        bases.append(resolved)
    bases = list(dict.fromkeys(bases))
    # Some Nuxt sites use /roster/season/{year} or /roster/{year} (e.g. Penn State, Nebraska, Wyoming).
    # PrestoSport sites (roster behind a /index homepage) use /{season}/roster.
    # Both also exist under the full academic-year slug (2025-26), which the
    # year-only "2025" season forms miss entirely on archival pages.
    wait_ms = team.get('wait_ms', 5000)
    paths: List[str] = []
    for slug in season_url_slugs(season):
        paths.append(f"/roster/{slug}")
    for slug in season_url_slugs(season):
        paths.append(f"/roster/season/{slug}")
    for slug in season_url_slugs(season):
        paths.append(f"/{slug}/roster")
    paths.append("/roster")
    for base in bases:
        for path in dict.fromkeys(paths):
            html = fetch_url_with_javascript(base + path, wait_ms=wait_ms)
            if html is None:
                continue
            if not verify_roster_season(html, season, team):
                continue
            roster = _best_parse(html, team, season)
            if roster:
                return roster

    # If JS rendering yielded nothing, fall back to a plain fetch + parse.
    logger.info(f"JS rendering found no players for {team['team']}, trying standard fetch")
    if 'wvball' in team['url']:
        html = fetch_wbkb_roster(team['url'], season)
        if html is None or not verify_roster_season(html, season, team):
            return []
        return parse_roster_wbkb(team, html, season)
    elif 'w-baskbl' in team['url']:
        html = fetch_baskbl_roster(team['url'], season)
        if html is None or not verify_roster_season(html, season, team):
            return []
        return parse_roster_baskbl(team, html, season)
    # Plain fetches over both base forms and every season slug — the browser
    # render can come back empty (a site that wipes its server HTML on load),
    # and a year-only fetch here would miss academic-slug archives entirely.
    roster = _scrape_plain_urls(
        team, season,
        [base.rstrip('/') + f"/roster/{slug}"
         for base in bases for slug in season_url_slugs(season)])
    if roster:
        return roster
    # Last resort: the original single-URL behavior.
    html = fetch_roster(team['url'], season)
    if html is None or not verify_roster_season(html, season, team):
        return []
    return parse_roster(team, html, season)


def _is_nuxt_page(html: BeautifulSoup) -> bool:
    """Return True when the static HTML is a client-rendered Nuxt app skeleton."""
    raw = str(html)
    return '__NUXT_DATA__' in raw or 'window.__NUXT__' in raw


def _resolved_roster_base(base: str) -> Optional[str]:
    """Follow the ``/roster`` redirect to catch reorganized sport slugs.

    Returns the base the site actually serves roster pages under, or None
    when the original URL does not redirect somewhere new. A plain request is
    enough for this — redirects are resolved by the server.
    """
    try:
        r = requests.head(f"{base}/roster", allow_redirects=True,
                          timeout=REQUEST_TIMEOUT)
        resolved = r.url.rstrip('/')
    except requests.RequestException:
        return None
    if not resolved.endswith('/roster'):
        return None
    resolved_base = resolved[:-len('/roster')]
    if resolved_base == base:
        return None
    return resolved_base


def _scrape_plain_urls(team: Dict, season: str, urls: List[str]) -> List:
    """Try a list of plain-fetched roster URLs with every layout parser."""
    for url in urls:
        html = BeautifulSoup(fetch_url(url).text, features="html.parser")
        if not verify_roster_season(html, season, team):
            continue
        roster = _best_parse(html, team, season)
        if roster:
            return roster
    return []


def _scrape_legacy_index(team: Dict, season: str) -> List:
    """Sites whose homepage URL ends in "/index" (old Sidearm or PrestoSport).

    The default `{base}/roster/{season}` form builds a nonexistent
    "/index/roster/..." URL for these. Sidearm sites want the suffix dropped;
    PrestoSport sites use /{season}/roster. All are server-rendered, so plain
    fetches work, and the JS-render pass catches client-rendered holdouts.
    """
    base = team['url'].rstrip('/')[:-len('/index')]
    slugs = season_url_slugs(season)
    roster = _scrape_plain_urls(team, season,
                                 [f"{base}/{slug}/roster" for slug in slugs] +
                                 [f"{base}/roster/{slug}" for slug in slugs] +
                                 [f"{base}/roster"])
    if roster:
        return roster
    return _scrape_js_render(team, season)


def _prefer_named_roster(roster: List, html: BeautifulSoup,
                         team: Dict, season: str) -> List:
    """Replace a fully blank-named roster when another parser names every row.

    Some wvball pages (e.g. Ohio 2025) hold the roster in one table of a
    multi-table page (coaching/support staff follow). The wbkb table parser
    reads <th> page-wide, so the staff tables' Name columns pollute the
    header list; the row-name compensation then grabs the first anchor — an
    icon link — producing empty names. The layout parsers in RENDERED_PARSERS
    handle that markup correctly, so prefer their output over rows that all
    have blank names.
    """
    if not roster or any(p.name for p in roster):
        return roster
    better = _best_parse(html, team, season)
    if better and all(p.name for p in better):
        logger.info(f"{team['team']}: replacing {len(roster)} blank-name rows "
                    f"with {len(better)} named rows from the layout sweep")
        return better
    return roster


def _scrape_default(team: Dict, season: str) -> List:
    """URL-pattern routing for teams without an explicit `scraper` strategy."""
    if team['url'].rstrip('/').endswith('/index'):
        return _scrape_legacy_index(team, season)
    if 'wvball' in team['url']:
        # wvball teams use either standard Sidearm or a table format.
        html = fetch_roster(team['url'], season)
        if html and verify_roster_season(html, season, team):
            roster = parse_roster(team, html, season)
            if roster:
                return roster
            roster = parse_roster_wbkb(team, html, season)
            if roster:
                return _prefer_named_roster(roster, html, team, season)
        # Self-heal: Nuxt pages and WAF-challenged stubs parse empty, and a
        # wrong-season page (year-only slug redirecting to the current roster)
        # is better answered by the season-qualified URLs in the sweep.
        return _scrape_js_render(team, season)
    elif 'w-baskbl' in team['url']:
        html = fetch_baskbl_roster(team['url'], season)
        if html is None or not verify_roster_season(html, season, team):
            return []
        return parse_roster_baskbl(team, html, season)
    # DEFAULT: standard roster page.
    html = fetch_roster(team['url'], season)
    if html and verify_roster_season(html, season, team):
        roster = parse_roster(team, html, season)
        if roster:
            return roster

    # Self-heal: render with a real browser and try every known layout. This
    # also covers WAF-challenged pages (an Incapsula stub parses empty),
    # server pages that redirect the season path to a JS-rendered current one,
    # and a season-verification rejection on the year-only fetch — the sweep's
    # season-qualified URLs (including the derived academic-year slug) are the
    # better answer for archival rosters.
    return _scrape_js_render(team, season)


# Maps a team's optional `scraper` key (in data/teams.json) to its handler.
# Teams with no `scraper` key fall through to _scrape_default (URL routing).
def _scrape_wyoming(team: Dict, season: str) -> List[Player]:
    """Wyoming is behind Incapsula WAF that blocks headless browsers.

    Plain requests works fine. The roster list page embeds player names +
    profile URLs in schema.org JSON-LD; individual profile pages hold jersey,
    position, class, height, and hometown in dl/dt/dd pairs.
    """
    year = season.split('-')[0]
    base = team['url'].rstrip('/')
    headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'}

    try:
        r = requests.get(f"{base}/roster/{year}", headers=headers, timeout=15)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"Wyoming: roster list fetch failed: {e}")
        return []

    html = BeautifulSoup(r.text, 'html.parser')
    if not verify_roster_season(html, season, team):
        return []
    player_links: List[tuple] = []
    for s in html.find_all('script'):
        t = s.get_text()
        if '@type' not in t or 'Person' not in t:
            continue
        m = re.search(r'\{.*\}', t, re.DOTALL)
        if not m:
            continue
        try:
            data = json.loads(m.group(0))
            for item in data.get('item', []):
                if item.get('@type') == 'Person':
                    name = item.get('name', '').strip()
                    url = item.get('url', '').strip()
                    if name and url:
                        player_links.append((name, url))
        except (json.JSONDecodeError, KeyError, AttributeError):
            pass

    players = []
    for name, profile_url in player_links:
        try:
            pr = requests.get(profile_url, headers=headers, timeout=15)
            pr.raise_for_status()
        except requests.RequestException:
            continue

        phtml = BeautifulSoup(pr.text, 'html.parser')
        jersey_el = phtml.find(class_='sidearm-roster-player-jersey-number')
        jersey = jersey_el.get_text(strip=True).lstrip('#') if jersey_el else ''

        fields: Dict[str, str] = {}
        for dl in phtml.find_all('dl'):
            for dt, dd in zip(dl.find_all('dt'), dl.find_all('dd')):
                fields[dt.get_text(strip=True)] = dd.get_text(strip=True)

        rp_id_m = re.search(r'rp_id=(\d+)', profile_url)
        player_id = rp_id_m.group(1) if rp_id_m else None
        hometown_raw = fields.get('Hometown', '')
        hometown, high_school = split_town_high_school(hometown_raw)

        players.append(Player(
            team_id=team['ncaa_id'],
            team=team['team'],
            player_id=player_id,
            name=name,
            year=fields.get('Class', ''),
            hometown=hometown,
            high_school=fields.get('High School') or high_school or None,
            previous_school=fields.get('Previous School', ''),
            height=fields.get('Height', ''),
            position=fields.get('Position', 'N/A'),
            jersey=jersey,
            url=profile_url,
            season=season,
        ))

    return players


SCRAPERS = {
    'byu': _scrape_byu,
    'sanjose': fetch_and_parse_sanjose,
    'miami': fetch_and_parse_miami,
    'clemson': fetch_and_parse_clemson,
    'iowa_state': fetch_and_parse_iowa_state,
    'nebraska': fetch_and_parse_nebraska,
    'vandy': fetch_and_parse_vandy,
    'airforce': _with_fallback(shotscraper_airforce),
    'shotscraper_table': _with_fallback(shotscraper_table),
    'shotscraper_card': _with_fallback(shotscraper_card),
    'shotscraper_list_item': _with_fallback(shotscraper_list_item),
    'shotscraper_roster_player': _with_fallback(shotscraper_roster_player),
    'shotscraper_data_tables': _with_fallback(shotscraper_data_tables),
    'js_render': _scrape_js_render,
    'wyoming': _scrape_wyoming,
}


def scrape_team_roster(team: Dict, season: str) -> List:
    """
    Route a single team to the appropriate scraper and return its roster.

    The strategy comes from the team's `scraper` key in data/teams.json;
    teams without one use URL-pattern routing. Returns a list of Player
    objects or dicts (both are accepted downstream). Skip decisions are
    handled by the caller.
    """
    strategy = team.get('scraper')
    handler = SCRAPERS.get(strategy, _scrape_default)
    roster = handler(team, season)
    if roster or handler in (_scrape_default, _scrape_js_render, None):
        return roster
    # Self-heal for stale pinned strategies: a site that has since migrated to
    # another layout (or renamed its season URL) produces nothing; retry with
    # the generic renderer, which sweeps every URL form and layout parser.
    logger.info(f"Pinned strategy '{strategy}' found no players for "
                f"{team['team']}, trying js_render sweep")
    return _scrape_js_render(team, season)


def player_to_row(player, season: str) -> List:
    """Build a CSV row from a Player object or a JS-scraper dict."""
    if isinstance(player, Player):
        d = player.to_dict()
        return [d['team_id'], d['team'], d['player_id'], d['name'], d['year'],
                d['hometown'], d['high_school'], d['previous_school'], d['height'],
                d['position'], d['jersey'], d['url'], season]
    # JS-scraper dicts get the same string cleaning and school-field hygiene
    # that Player.to_dict applies.
    d = {
        'team_id': player['team_id'], 'team': player['team'],
        'player_id': player.get('id'), 'name': player['name'],
        'year': player['year'], 'hometown': player['hometown'],
        'high_school': player.get('high_school') or '',
        'previous_school': player.get('previous_school') or '',
        'height': player['height'], 'position': player['position'],
        'jersey': player['jersey'], 'url': player['url'],
    }
    for k, v in list(d.items()):
        if isinstance(v, str):
            d[k] = FieldExtractors.clean_text(v)
    reclassify_high_school_from_previous(d)
    dedupe_school_fields(d)
    return [d['team_id'], d['team'], d['player_id'], d['name'], d['year'],
            d['hometown'], d['high_school'], d['previous_school'], d['height'],
            d['position'], d['jersey'], d['url'], season]


def write_rosters_csv(path: str, new_rows: List[List], replace_ids: set = None):
    """
    Write roster rows to `path` atomically (temp file + rename), so a crash
    mid-run never leaves a truncated CSV.

    If `replace_ids` is None, the file is fully overwritten (full-season run).
    Otherwise, existing rows are preserved except those whose ncaa_id is in
    `replace_ids` (the teams we just re-scraped), and `new_rows` is appended.

    Before the replace, when the target already exists and its bytes differ
    from the bytes about to be written, the previous file is archived verbatim
    (byte copy, never re-encoded) to `_raw_archive_dir(path)/<stem>.<UTC
    timestamp>.csv`, preserving prior raw vintages. A rewrite with identical
    bytes archives nothing.
    """
    existing_rows = []
    if replace_ids is not None and os.path.exists(path):
        with open(path, newline='') as f:
            reader = csv.reader(f)
            rows = list(reader)
        # Drop the header if present, keep rows for teams we did not re-scrape.
        data_rows = rows[1:] if rows and rows[0] == CSV_HEADER else rows
        existing_rows = [r for r in data_rows if r and r[0] not in replace_ids]

    tmp_path = f"{path}.tmp"
    with open(tmp_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        writer.writerows(existing_rows)
        writer.writerows(new_rows)

    if os.path.exists(path):
        with open(path, 'rb') as f:
            current_bytes = f.read()
        with open(tmp_path, 'rb') as f:
            incoming_bytes = f.read()
        if current_bytes != incoming_bytes:
            _archive_previous_snapshot(path)

    os.replace(tmp_path, path)


def _raw_archive_dir(path: str) -> str:
    """Directory that holds prior vintages of the CSV at `path`.

    Defaults to a `raw_archive` directory alongside the CSV (a season file in
    `data/` therefore archives to `data/raw_archive/`). Set the
    ROSTERS_RAW_ARCHIVE_DIR environment variable to override.
    """
    override = os.environ.get('ROSTERS_RAW_ARCHIVE_DIR')
    if override:
        return override
    return os.path.join(os.path.dirname(path) or '.', 'raw_archive')


def _archive_previous_snapshot(path: str) -> None:
    """Copy the current bytes of `path` to the raw archive before replacement.

    A byte-preserving shutil.copy2 (no re-encode) so the archived vintage is
    exactly what the file was. No-op when the target does not exist.
    """
    if not os.path.exists(path):
        return
    archive_dir = _raw_archive_dir(path)
    os.makedirs(archive_dir, exist_ok=True)
    timestamp = time.strftime('%Y%m%dT%H%M%S', time.gmtime())
    stem = Path(path).stem or Path(path).name
    archive_path = os.path.join(archive_dir, f"{stem}.{timestamp}.csv")
    # Same-second rewrites must not overwrite an archived vintage.
    counter = 0
    while os.path.exists(archive_path):
        counter += 1
        archive_path = os.path.join(
            archive_dir, f"{stem}.{timestamp}.{counter}.csv")
    shutil.copy2(path, archive_path)
    logger.info(f"Archived previous snapshot of {path} to {archive_path}")


# ============================================================================
# PER-TEAM RESUME CACHE
# ============================================================================

def _cache_file_for(season: str, ncaa_id: Any) -> str:
    """Per-team cache file for a canonical season label."""
    return f"data/cache/rosters_{season}/{ncaa_id}.json"


def _write_cached_rows(season: str, ncaa_id: Any, rows: List[List]) -> None:
    """Atomically cache one team's CSV rows so an interrupted run can resume.

    `season` is the canonical academic-year label. Only successful
    (non-empty) rosters are cached, so re-running with resume still retries
    the teams that failed or parsed empty. Delete
    ``data/cache/rosters_<label>/`` to force a full re-scrape.
    """
    path = _cache_file_for(season, ncaa_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, 'w') as f:
            json.dump({'ncaa_id': ncaa_id, 'season': season, 'rows': rows}, f)
        os.replace(tmp_path, path)
    except OSError as e:
        logger.warning(f"Cache write failed for ncaa_id {ncaa_id}: {e}")


def _read_cached_rows(season: str, ncaa_id: Any) -> Optional[List[List]]:
    """Return a team's cached CSV rows, or None when absent/corrupt."""
    path = _cache_file_for(season, ncaa_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            rows = json.load(f).get('rows')
        if isinstance(rows, list) and rows:
            return rows
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Cache read failed for ncaa_id {ncaa_id}: {e}")
    return None


def get_all_rosters(season: str, teams: List[int] = [], resume: bool = False) -> tuple:
    """
    Main function to scrape all rosters for a season

    Args:
        season: Season string, either year-only ('2025') or the full
            academic-year label ('2023-24'). All writes use the canonical
            label: the `season` column value, the output file
            data/rosters_<label>.csv and the resume cache
            data/cache/rosters_<label>/.
        teams: Optional list of team IDs to scrape (if empty, scrapes all)
        resume: When True, teams already scraped successfully for this season
            (present in data/cache/rosters_<label>/) are served from cache
            instead of being re-fetched

    Returns:
        Tuple of (unparsed_team_ids, skipped_team_ids)
    """
    unparsed = []
    skipped = []

    # Normalize to the full academic-year label used everywhere below (URL
    # slugs, row values, cache dir, output file). A year-only request keeps
    # working; the label is what lands on disk.
    label = canonical_season_label(season)
    if label != season:
        logger.info(f"Season {season!r} canonicalized to {label!r}")

    # Load teams
    teams_json = json.loads(open('data/teams.json').read())
    if len(teams) > 0:
        teams_json = [t for t in teams_json if t['ncaa_id'] in teams]
    teams_with_urls = [x for x in teams_json if "url" in x]

    # Accumulate rows in memory, then write atomically at the end so an
    # interrupted run can never truncate the season file.
    new_rows = []
    scraped_ids = set()

    for team in teams_with_urls:
        try:
            # Skip decisions come from data/teams.json config keys. Match both
            # the requested season and its canonical label.
            if team.get('scraper') == 'skip':
                continue
            skip_seasons = team.get('skip_seasons', [])
            if season in skip_seasons or label in skip_seasons:
                continue
            if 'roster' in team:
                continue

            logger.info(f"Processing {team['team']}")

            # Resume: serve teams with a settled cache instead of re-fetching.
            if resume:
                cached_rows = _read_cached_rows(label, team['ncaa_id'])
                if cached_rows:
                    logger.info(f"{team['team']}: serving {len(cached_rows)} players from cache")
                    scraped_ids.add(str(team['ncaa_id']))
                    new_rows.extend(cached_rows)
                    continue

            roster = scrape_team_roster(team, label)

            if roster:
                rows = [player_to_row(player, label) for player in roster]
                scraped_ids.add(str(team['ncaa_id']))
                new_rows.extend(rows)
                _write_cached_rows(label, team['ncaa_id'], rows)
            else:
                unparsed.append(team['ncaa_id'])
            if RATE_LIMIT_SECONDS:
                time.sleep(RATE_LIMIT_SECONDS)
        except requests.RequestException as e:
            # Network/transport failure — distinct from a parse failure so the
            # caller can tell "site unreachable" from "site changed".
            logger.error(f"Network error for {team['team']}: {e}")
            skipped.append(team['ncaa_id'])
            continue
        except Exception as e:
            logger.error(f"Parse error for {team['team']}: {e}")
            skipped.append(team['ncaa_id'])
            continue

    # A full-season run overwrites the file; a targeted `-teams` run merges the
    # newly scraped teams into the existing file, replacing only those teams.
    path = f"data/rosters_{label}.csv"
    if teams:
        write_rosters_csv(path, new_rows, replace_ids=scraped_ids)
    else:
        write_rosters_csv(path, new_rows, replace_ids=None)
    logger.info(f"Wrote {len(new_rows)} rows to {path}")

    return [unparsed, skipped]


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='NCAA Women\'s Volleyball Roster Scraper',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python rosters.py -season 2023-24
  python rosters.py -season 2025            (written as 2025-26)
  python rosters.py -season 2023-24 -teams 255 326
  python rosters.py -url https://example.com/sports/w-volley -season 2023-24

Season labels are the full academic year (fall sport: end year = start + 1):
-season 2025 writes data/rosters_2025-26.csv and resumes from
data/cache/rosters_2025-26/. Year-only input is accepted and canonicalized.
        """
    )
    parser.add_argument('-season', action='store', dest='season',
                       help='Season string: year-only ("2025") or the full '
                            'academic-year label ("2025-26")', required=True)
    parser.add_argument('-url', action='store', dest='url',
                       help='Base URL for a single team')
    parser.add_argument('-teams', nargs='+', type=int, dest='teams',
                       help='List of team IDs to scrape (space-separated)')
    parser.add_argument('-resume', action='store_true', dest='resume',
                       help='Reuse cached per-team results from a previous run '
                            'of this season (data/cache/rosters_<label>/) instead '
                            'of re-fetching teams that already succeeded')

    results = parser.parse_args()

    # Canonicalize up front so every below log line and cache reference names
    # the label that will actually be written (a bad season exits loudly).
    canonical = canonical_season_label(results.season)
    logger.info(f"Using canonical season label: {canonical}")

    if results.url:
        # Single team mode
        logger.info(f"Scraping single team: {results.url}")
        team = {'url': results.url, 'ncaa_id': 0, 'team': 'Single Team'}
        html = fetch_roster(results.url, canonical)
        roster = parse_roster(team, html, canonical)
        for player in roster:
            logger.info(player)
    else:
        # Bulk mode
        teams_to_scrape = results.teams if results.teams else []
        logger.info(f"Starting bulk scrape for season {canonical}")
        if teams_to_scrape:
            logger.info(f"Scraping specific teams: {teams_to_scrape}")
        unparsed, skipped = get_all_rosters(results.season, teams_to_scrape, resume=results.resume)
        logger.info(f"✓ Scraping complete")
        logger.info(f"Unparsed teams: {unparsed}")
        logger.info(f"Skipped teams: {skipped}")
