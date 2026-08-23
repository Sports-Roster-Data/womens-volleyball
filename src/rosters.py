#!/usr/bin/env python3
"""
NCAA Women's Volleyball Roster Scraper - Unified Architecture
Adapted from Soccer scraper architecture for volleyball-specific needs

Usage:
    python rosters.py -season 2023-24
    python rosters.py -season 2023-24 -teams 255 326
    python rosters.py -url https://example.com/sports/w-volley -season 2023-24
"""

import os
import re
import csv
import time
import json
import argparse
import logging
import subprocess
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from requests_html import HTMLSession
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
        ]

        for p in patterns:
            text = re.sub(p, '', text, flags=re.IGNORECASE).strip()

        return text


# ============================================================================
# SCRAPER UTILITIES
# ============================================================================

def fetch_url_with_javascript(url: str, timeout: int = 45) -> Optional[BeautifulSoup]:
    """
    Fetch URL with JavaScript rendering using shot-scraper

    This uses 'uv run shot-scraper html' to render JavaScript-heavy pages
    and return the rendered HTML for parsing.

    Args:
        url: URL to fetch
        timeout: Timeout in seconds (default 45)

    Returns:
        BeautifulSoup object or None if failed
    """
    try:
        # Use shot-scraper via uv to render JavaScript
        result = subprocess.run(
            ['uv', 'run', 'shot-scraper', 'html', url, '--wait', '3000'],
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

        for player in parsed_data:
            player['team_id'] = team['ncaa_id']
            player['team'] = team['team']
            player['season'] = season

        return parsed_data
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
# PARSER FUNCTIONS
# ============================================================================

def split_town_high_school(town: Optional[str]) -> tuple:
    """Split a combined 'Hometown / High School' cell.

    Tolerates a missing '/' (returns the whole string as the town and None for
    the high school) and a missing/None cell, so a malformed row never raises.
    """
    if not town:
        return '', None
    parts = [x.strip() for x in town.split('/', maxsplit=1)]
    if len(parts) == 2:
        return parts[0], parts[1]
    return parts[0], None


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

    tbody = html.find('tbody')
    if not tbody:
        logger.warning(f"No tbody found for {team['team']}")
        return roster
    raw_players = [x for x in tbody.find_all('tr')]

    for raw_player in raw_players:
        [x.span.decompose() for x in raw_player.find_all('td') if x.find('span')]
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

    # Remove unwanted columns
    unwanted_cols = ['Pronounciation', 'Club Team', 'Major', 'MAJOR', 'Major/Minor',
                     'College', 'Wt.', 'Ltrs.', 'Pronouns']
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
        [x.span.decompose() for x in raw_player.find_all('td') if x.find('span')]
        raw_player_list = [x.text.strip() for x in raw_player.find_all('td')
                          if x.text.replace('*', '').replace('(she/her/hers)', '').replace('she/her/hers', '').strip() != '']

        # Team-specific name insertion logic
        if team['ncaa_id'] in [1340, 760, 510, 227]:
            raw_name = raw_player.find('a').text.strip().replace("  ", " ").replace("\t", "").replace("\r", "").replace("\n", "").split(' ')
            raw_player_list.insert(1, " ".join([x.strip() for x in raw_name if x != '']))

        # Skip specific problematic entries
        if team['ncaa_id'] == 73 and len(raw_player_list) > 0 and raw_player_list[0] == '43':
            continue

        # Special handling for specific teams
        if team['ncaa_id'] in [114, 1050, 1059, 1199, 22626, 24317, 30037, 341, 1315, 46, 641, 730, 75, 806, 817, 89, 145, 217, 247, 2713, 2798, 28594, 30002, 30225, 467, 567, 569, 137, 715, 779, 808, 8688, 379, 1461]:
            raw_player_list = [x.text.strip() for x in raw_player.find_all('td')]

        if len(raw_player_list) == 4:
            raw_player_list = [None] + raw_player_list
        elif len(raw_player_list) < 4:
            logger.info(f"Skipping {raw_player_list}")
            continue

        raw_player_list[4] = " ".join([x for x in raw_player_list[4].split()])

        # Team-specific adjustments
        if team['ncaa_id'] == 1036 and len(raw_player_list) == len(new_cols):
            raw_player_list.pop()
        if team['ncaa_id'] == 1096 and len(raw_player_list) >= len(new_cols):
            raw_player_list.pop()
        if team['ncaa_id'] == 142:
            del raw_player_list[2]

        # Filter out major column if present
        if any(major in raw_player_list for major in major_list):
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

        # Extract name
        try:
            name = player.find('a')['aria-label'].split(' - ')[0].strip()
        except (TypeError, KeyError, AttributeError):
            name = player.find('h3').text.strip()
        if 'Instagram' in name:
            name = player.find('h3').text.strip()

        roster.append(Player(
            team_id=team['ncaa_id'],
            team=team['team'],
            player_id=player['data-player-id'],
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

def fetch_and_parse_clemson(team: Dict, season: str) -> List[Dict]:
    """Clemson - Custom table format"""
    roster = []
    er = tldextract.extract(team['url'])
    url = f"{team['url']}/roster/season/{season[0:4]}"
    r = fetch_url(url)
    html = BeautifulSoup(r.text, features="html.parser")
    cols = [x.text for x in html.find_all('th') if x.text not in ['MAJOR']]
    cols = cols[0:-4]
    unmapped = [c for c in cols if c not in HEADERS]
    if unmapped:
        logger.warning(f"Clemson: unmapped columns {unmapped}, skipping")
        return []
    new_cols = [HEADERS[c] for c in cols]
    players = html.find('table').find_all('tr')[1:]

    for player in players:
        raw_player_list = [x.text.strip() for x in player.find_all('td')][0:-2]
        player_dict = dict(zip(new_cols, raw_player_list))
        roster.append({
            'team_id': team['ncaa_id'],
            'team': team['team'],
            'id': None,
            'name': player_dict['name'],
            'year': player_dict['academic_year'],
            'hometown': player_dict['town'],
            'high_school': None,
            'previous_school': None,
            'height': player_dict['height'],
            'position': player_dict['position'],
            'jersey': player_dict['jersey'],
            'url': f"https://www.{er.domain}.{er.suffix}{player.find('a')['href']}",
            'season': season
        })
    return roster


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
    url = f"{team['url']}/roster/season/{season}"
    return shotscraper_caller(team, season, url, javascript_code)


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
    url = f"{team['url']}/roster/season/{season}"
    return shotscraper_caller(team, season, url, javascript_code)


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
    url = f"{team['url']}/roster/season/{season}"
    return shotscraper_caller(team, season, url, javascript_code)


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
    url = f"{team['url']}/roster/season/{season}"
    return shotscraper_caller(team, season, url, javascript_code)


def fetch_and_parse_iowa_state(team: Dict, season: str) -> List[Player]:
    """Iowa State - HTMLSession rendered"""
    roster = []
    er = tldextract.extract(team['url'])
    url = f"{team['url']}/roster/{season}"
    session = HTMLSession()
    r = session.get(url)
    r.html.render(timeout=30)
    players = r.html.find('li.sidearm-roster-list-item')

    for player in players:
        roster.append(Player(
            team_id=team['ncaa_id'],
            team=team['team'],
            player_id=None,
            name=player.find('a', first=True).text,
            year=player.find('span.sidearm-roster-list-item-year', first=True).text,
            hometown=player.find('div.sidearm-roster-list-item-hometown', first=True).text,
            high_school=player.find('span.sidearm-roster-list-item-highschool', first=True).text,
            previous_school=None,
            height=player.find('span.sidearm-roster-list-item-height', first=True).text,
            position=player.find('span.sidearm-roster-list-item-position', first=True).text,
            jersey=player.find('span')[0].text,
            url=f"https://www.{er.domain}.{er.suffix}{player.find('a', first=True).attrs['href']}",
            season=season
        ))
    return roster

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
        const jersey = jerseyEl?.innerText ?? '';
        const url = el.querySelector('a')?.href ?? '';
        
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    }).filter(item => item !== null)
    """
    url = f"{team['url']}/roster/{season}"
    return shotscraper_caller(team, season, url, javascript_code)


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
    url = f"{team['url']}/roster/{season}"
    return shotscraper_caller(team, season, url, javascript_code)


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
    url = f"{team['url']}/roster/{season}"
    return shotscraper_caller(team, season, url, javascript_code)


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
        const jersey = el.querySelector('.s-stamp__text')?.innerText ?? '';
        const url = el.querySelector('a')?.href ?? '';
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """
    url = f"{team['url']}/roster/{season}"
    return shotscraper_caller(team, season, url, javascript_code)


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
    url = f"{team['url']}/roster/{season}"
    return shotscraper_caller(team, season, url, javascript_code)


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
    url = f"{team['url']}/roster/{season}"
    return shotscraper_caller(team, season, url, javascript_code)


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
    return parse_roster(team, html, season)


def _with_fallback(scraper):
    """Wrap a shot-scraper so an empty result falls back to a standard fetch."""
    def run(team, season):
        return scraper(team, season) or standard_fallback(team, season)
    return run


def _scrape_byu(team: Dict, season: str) -> List:
    # BYU uses a four-digit end year (e.g. "2025-26" -> "2025-2026").
    byu_season = f"{season[0:5]}20{season[5:7]}"
    return fetch_and_parse_byu(team, byu_season)


def _scrape_js_render(team: Dict, season: str) -> List:
    """Render the page with JavaScript, then parse by URL pattern."""
    url = f"{team['url']}/roster/{season}"
    html = fetch_url_with_javascript(url)
    if html:
        if 'wvball' in team['url']:
            return parse_roster_wbkb(team, html, season)
        elif 'w-baskbl' in team['url']:
            return parse_roster_baskbl(team, html, season)
        return parse_roster(team, html, season)
    # If JS rendering fails, try a standard fetch as a fallback.
    logger.info(f"JS rendering failed for {team['team']}, trying standard fetch as fallback")
    if 'wvball' in team['url']:
        html = fetch_wbkb_roster(team['url'], season)
        return parse_roster_wbkb(team, html, season) if html else []
    elif 'w-baskbl' in team['url']:
        html = fetch_baskbl_roster(team['url'], season)
        return parse_roster_baskbl(team, html, season)
    html = fetch_roster(team['url'], season)
    return parse_roster(team, html, season)


def _scrape_default(team: Dict, season: str) -> List:
    """URL-pattern routing for teams without an explicit `scraper` strategy."""
    if 'wvball' in team['url']:
        # wvball teams use either standard Sidearm or a table format.
        html = fetch_roster(team['url'], season)
        if not html:
            return []
        roster = parse_roster(team, html, season)
        return roster if roster else parse_roster_wbkb(team, html, season)
    elif 'w-baskbl' in team['url']:
        html = fetch_baskbl_roster(team['url'], season)
        return parse_roster_baskbl(team, html, season)
    # DEFAULT: standard roster page.
    html = fetch_roster(team['url'], season)
    return parse_roster(team, html, season)


# Maps a team's optional `scraper` key (in data/teams.json) to its handler.
# Teams with no `scraper` key fall through to _scrape_default (URL routing).
SCRAPERS = {
    'byu': _scrape_byu,
    'sanjose': fetch_and_parse_sanjose,
    'miami': fetch_and_parse_miami,
    'clemson': fetch_and_parse_clemson,
    'iowa_state': fetch_and_parse_iowa_state,
    'vandy': fetch_and_parse_vandy,
    'airforce': _with_fallback(shotscraper_airforce),
    'shotscraper_table': _with_fallback(shotscraper_table),
    'shotscraper_card': _with_fallback(shotscraper_card),
    'shotscraper_list_item': _with_fallback(shotscraper_list_item),
    'shotscraper_roster_player': _with_fallback(shotscraper_roster_player),
    'shotscraper_data_tables': _with_fallback(shotscraper_data_tables),
    'js_render': _scrape_js_render,
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
    return handler(team, season)


def player_to_row(player, season: str) -> List:
    """Build a CSV row from a Player object or a JS-scraper dict."""
    if isinstance(player, Player):
        d = player.to_dict()
        return [d['team_id'], d['team'], d['player_id'], d['name'], d['year'],
                d['hometown'], d['high_school'], d['previous_school'], d['height'],
                d['position'], d['jersey'], d['url'], season]
    return [player['team_id'], player['team'], player.get('id'), player['name'],
            player['year'], player['hometown'], player['high_school'],
            player['previous_school'], player['height'], player['position'],
            player['jersey'], player['url'], season]


def write_rosters_csv(path: str, new_rows: List[List], replace_ids: set = None):
    """
    Write roster rows to `path` atomically (temp file + rename), so a crash
    mid-run never leaves a truncated CSV.

    If `replace_ids` is None, the file is fully overwritten (full-season run).
    Otherwise, existing rows are preserved except those whose ncaa_id is in
    `replace_ids` (the teams we just re-scraped), and `new_rows` is appended.
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
    os.replace(tmp_path, path)


def get_all_rosters(season: str, teams: List[int] = []) -> tuple:
    """
    Main function to scrape all rosters for a season

    Args:
        season: Season string (e.g., '2023-24')
        teams: Optional list of team IDs to scrape (if empty, scrapes all)

    Returns:
        Tuple of (unparsed_team_ids, skipped_team_ids)
    """
    unparsed = []
    skipped = []

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
            # Skip decisions come from data/teams.json config keys.
            if team.get('scraper') == 'skip':
                continue
            if season in team.get('skip_seasons', []):
                continue
            if 'roster' in team:
                continue

            logger.info(f"Processing {team['team']}")

            roster = scrape_team_roster(team, season)

            if roster:
                scraped_ids.add(str(team['ncaa_id']))
                for player in roster:
                    new_rows.append(player_to_row(player, season))
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
    path = f"data/rosters_{season}.csv"
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
  python rosters.py -season 2023-24 -teams 255 326
  python rosters.py -url https://example.com/sports/w-volley -season 2023-24
        """
    )
    parser.add_argument('-season', action='store', dest='season',
                       help='Season string such as "2023-24"', required=True)
    parser.add_argument('-url', action='store', dest='url',
                       help='Base URL for a single team')
    parser.add_argument('-teams', nargs='+', type=int, dest='teams',
                       help='List of team IDs to scrape (space-separated)')

    results = parser.parse_args()

    if results.url:
        # Single team mode
        logger.info(f"Scraping single team: {results.url}")
        team = {'url': results.url, 'ncaa_id': 0, 'team': 'Single Team'}
        html = fetch_roster(results.url, results.season)
        roster = parse_roster(team, html, results.season)
        for player in roster:
            logger.info(player)
    else:
        # Bulk mode
        teams_to_scrape = results.teams if results.teams else []
        logger.info(f"Starting bulk scrape for season {results.season}")
        if teams_to_scrape:
            logger.info(f"Scraping specific teams: {teams_to_scrape}")
        unparsed, skipped = get_all_rosters(results.season, teams_to_scrape)
        logger.info(f"✓ Scraping complete")
        logger.info(f"Unparsed teams: {unparsed}")
        logger.info(f"Skipped teams: {skipped}")
