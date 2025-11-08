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
import json
import argparse
import logging
import subprocess
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from pathlib import Path

import requests
from requests_html import HTMLSession
from bs4 import BeautifulSoup
import tldextract

# Configure tldextract to not fetch updates (to avoid 403 errors)
tldextract.extract = tldextract.TLDExtract(suffix_list_urls=None)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ============================================================================
# CONSTANTS
# ============================================================================

SEASONS = [
    '2025-26', '2024-25', '2023-24', '2022-23', '2021-22', '2020-21', '2019-20', '2018-19', '2017-18', '2016-17',
    '2015-16', '2014-15', '2013-14', '2012-13', '2011-12', '2010-11', '2009-10', '2008-09',
    '2007-08', '2006-07', '2005-06', '2004-05', '2003-04', '2002-03', '2001-02', '2000-01',
    '1999-00', '1998-99', '1997-98', '1996-97', '1995-96', '1994-95', '1993-94', '1992-93',
    '1991-92', '1990-91'
]

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
    """Common utilities for extracting player fields from text and HTML"""

    @staticmethod
    def extract_jersey_number(text: str) -> str:
        """Extract jersey number from various text patterns"""
        if not text:
            return ''

        patterns = [
            r'Jersey Number[:\s]+(\d+)',
            r'#(\d{1,2})\b',
            r'No\.?[:\s]*(\d{1,2})\b',
            r'\b(\d{1,2})\s+(?=[A-Z])',
            r'^\s*(\d{1,2})\s*$',
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        return ''

    @staticmethod
    def extract_height(text: str) -> str:
        """
        Extract height from various formats

        Formats supported:
        - 6'2" or 6-2 (imperial)
        - 6'2" / 1.88m (both)
        - 1.88m (metric only)
        """
        if not text:
            return ''

        patterns = [
            r"(\d+['\′]\s*\d+[\"\″']{1,2}(?:\s*/\s*\d+\.\d+m)?)",
            r"(\d+['\′]\s*\d+[\"\″']{1,2})",
            r"(\d+-\d+)",
            r"(\d+\.\d+m)",
            r"Height:\s*([^\,\n]+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()
        return ''

    @staticmethod
    def extract_position(text: str) -> str:
        """
        Extract position from text - VOLLEYBALL VERSION

        Volleyball positions: S, OH, MB, RS, L, DS (Setter, Outside Hitter, Middle Blocker, etc.)
        """
        if not text:
            return ''

        text = text.strip()

        # Look for abbreviated position patterns
        position_match = re.search(
            r'\b(S|SETTER|OH|OUTSIDE|OUTSIDE HITTER|MB|MIDDLE|MIDDLE BLOCKER|'
            r'RS|RIGHT SIDE|RIGHT SIDE HITTER|L|LIBERO|DS|DEFENSIVE SPECIALIST|'
            r'OPP|OPPOSITE)\b',
            text,
            re.IGNORECASE
        )
        if position_match:
            pos = position_match.group(1).upper()

            # Normalize variations
            if pos in ('S', 'SETTER'):
                return 'S'
            elif pos in ('OH', 'OUTSIDE', 'OUTSIDE HITTER'):
                return 'OH'
            elif pos in ('MB', 'MIDDLE', 'MIDDLE BLOCKER'):
                return 'MB'
            elif pos in ('RS', 'RIGHT SIDE', 'RIGHT SIDE HITTER', 'OPP', 'OPPOSITE'):
                return 'RS'
            elif pos in ('L', 'LIBERO'):
                return 'L'
            elif pos in ('DS', 'DEFENSIVE SPECIALIST'):
                return 'DS'

            return pos

        return ''

    @staticmethod
    def normalize_academic_year(year_text: str) -> str:
        """Normalize academic year abbreviations to full forms"""
        if not year_text:
            return ''

        year_map = {
            'Fr': 'Freshman', 'Fr.': 'Freshman', 'FR': 'Freshman',
            'So': 'Sophomore', 'So.': 'Sophomore', 'SO': 'Sophomore',
            'Jr': 'Junior', 'Jr.': 'Junior', 'JR': 'Junior',
            'Sr': 'Senior', 'Sr.': 'Senior', 'SR': 'Senior',
            'Gr': 'Graduate', 'Gr.': 'Graduate', 'GR': 'Graduate',
            'R-Fr': 'Redshirt Freshman', 'R-Fr.': 'Redshirt Freshman',
            'R-So': 'Redshirt Sophomore', 'R-So.': 'Redshirt Sophomore',
            'R-Jr': 'Redshirt Junior', 'R-Jr.': 'Redshirt Junior',
            'R-Sr': 'Redshirt Senior', 'R-Sr.': 'Redshirt Senior',
            '1st': 'Freshman', 'First': 'Freshman',
            '2nd': 'Sophomore', 'Second': 'Sophomore',
            '3rd': 'Junior', 'Third': 'Junior',
            '4th': 'Senior', 'Fourth': 'Senior',
        }

        cleaned = year_text.strip()
        return year_map.get(cleaned, year_text)

    @staticmethod
    def parse_hometown_school(text: str) -> Dict[str, str]:
        """
        Parse hometown and school information from combined text

        Handles:
        - "City, State / High School"
        - "City, Country / High School"
        - "City, State / High School / Previous College"
        """
        result = {'hometown': '', 'high_school': '', 'previous_school': ''}

        if not text:
            return result

        # Clean the text
        text = re.sub(r'\s*(Instagram|Twitter|Opens in a new window).*$', '', text)
        text = re.sub(r'\s+', ' ', text).strip()

        # Pattern: City, State/Country followed by school info separated by /
        if '/' in text:
            parts = [p.strip() for p in text.split('/')]
            result['hometown'] = parts[0] if parts else ''

            if len(parts) > 1:
                # Check if second part looks like a college/university
                college_indicators = ['University', 'College', 'State', 'Tech', 'Institute']
                if any(indicator in parts[1] for indicator in college_indicators):
                    result['previous_school'] = parts[1]
                else:
                    result['high_school'] = parts[1]

            if len(parts) > 2:
                result['previous_school'] = parts[2]
        else:
            # No separator, just store as hometown
            result['hometown'] = text

        return result

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
# URL BUILDER
# ============================================================================

class URLBuilder:
    """Build roster URLs for different site patterns"""

    @staticmethod
    def build_roster_url(base_url: str, season: str, url_format: str = 'default') -> str:
        """
        Build roster URL based on site pattern

        Args:
            base_url: Team's URL from teams.json
            season: Season string (e.g., '2023-24')
            url_format: URL pattern type

        Returns:
            Full roster URL
        """
        base_url = base_url.rstrip('/')

        if url_format == 'default':
            # Most common pattern
            return f"{base_url}/roster/{season}"
        elif url_format == 'wvball_index':
            # /sports/wvball/index pattern
            if '/index' in base_url:
                return base_url.replace('/index', f'/roster/{season}')
            else:
                return f"{base_url}/roster/{season}"
        elif url_format == 'wbkb':
            # Women's basketball pattern
            return base_url.replace('index', season + '/roster?view=list')
        elif url_format == 'baskbl':
            # Basketball pattern with season parameter
            if 'index' in base_url:
                return base_url.replace('index', 'roster/?season=' + season)
            elif base_url.endswith('w-baskbl'):
                return f"{base_url}/{season}/roster"
            else:
                return base_url + 'roster/?season=' + season
        elif url_format == 'clemson':
            # Clemson uses /roster/season/{year}
            year = season.split('-')[0]  # Extract year from season
            return f"{base_url}/roster/season/{year}"
        else:
            # Fallback to default
            logger.warning(f"Unknown url_format '{url_format}', using default")
            return f"{base_url}/roster/{season}"

    @staticmethod
    def extract_base_domain(full_url: str) -> str:
        """Extract base domain from full URL"""
        extracted = tldextract.extract(full_url)

        if extracted.subdomain:
            domain = f"{extracted.subdomain}.{extracted.domain}.{extracted.suffix}"
        else:
            domain = f"{extracted.domain}.{extracted.suffix}"

        return f"https://{domain}"


# ============================================================================
# TEAM CONFIGURATION
# ============================================================================

class TeamConfig:
    """Team-specific configuration and categorization"""

    # Teams requiring JavaScript rendering
    # These teams use fetch_url_with_javascript() + parse_roster()
    # Note: Teams with specific shotscraper_* functions are NOT in this list
    JS_TEAMS = [
        # Original JS teams (non-overlapping with specific scrapers)
        8, 31, 66, 72, 80, 224, 327, 334, 463, 513, 528, 623, 648, 694, 706, 721, 725,
        735, 742, 809, 811, 1000,
        # Previously uncategorized teams (adding as fallback to attempt scraping)
        22, 28, 46, 47, 56, 59, 67, 81, 86, 90, 101, 110, 129, 140, 142, 148, 157, 158,
        164, 166, 169, 172, 176, 193, 196, 204, 215, 217, 229, 235, 241, 255, 280, 288,
        317, 326, 331, 345, 352, 355, 365, 388, 390, 406, 414, 416, 417, 419, 433, 434,
        440, 454, 456, 457, 458, 469, 502, 504, 509, 518, 523, 529, 539, 545, 562, 598,
        599, 610, 626, 649, 657, 659, 673, 674, 682, 695, 697, 698, 703, 716, 718, 732,
        756, 760, 768, 772, 796, 798, 800, 807, 812, 1001, 1014, 1036, 1104, 1162, 1174,
        1196, 1340, 1356, 1400, 1403, 1461, 2707, 2711, 2810, 8688, 8746, 11538, 13028,
        23725, 30031, 30037, 30135, 30173, 505160,
        # Big conference teams with heavy JavaScript rendering
        312,  # Iowa
        328,  # Kansas
        473,  # New Mexico
        # Small schools needing JavaScript
        1064,  # Eastern Nazarene
        1111,  # Greenville
    ]

    # Team-specific URL formats
    TEAM_URL_FORMATS = {
        77: 'byu',        # BYU
        128: 'ucf',       # UCF
        147: 'clemson',   # Clemson
        311: 'iowa_state',  # Iowa State
        312: 'iowa',      # Iowa
        327: 'kansas_state',  # Kansas State
        334: 'kentucky',  # Kentucky
        415: 'miami',     # Miami
        463: 'nebraska',  # Nebraska
        513: 'notre_dame',  # Notre Dame
        630: 'san_jose',  # San Jose State
        648: 'south_carolina',  # South Carolina
        694: 'tennessee',  # Tennessee
        735: 'valpo',     # Valparaiso
        736: 'vandy',     # Vanderbilt
        742: 'virginia_tech',  # Virginia Tech
        811: 'wyoming',   # Wyoming
    }

    @classmethod
    def requires_javascript(cls, team_id: int) -> bool:
        """Check if a team requires JavaScript rendering"""
        return team_id in cls.JS_TEAMS

    @classmethod
    def get_url_format(cls, team_id: int, team_url: str = '') -> str:
        """Get URL format for a team"""
        # Check explicit configuration
        if team_id in cls.TEAM_URL_FORMATS:
            return cls.TEAM_URL_FORMATS[team_id]

        # Auto-detect from URL
        if team_url:
            if 'wvball' in team_url:
                return 'wvball_index'
            elif 'w-baskbl' in team_url:
                return 'baskbl'
            elif '/index' in team_url:
                return 'wbkb'

        return 'default'


# ============================================================================
# LAZY JSON DECODER
# ============================================================================

class LazyDecoder(json.JSONDecoder):
    """JSON decoder that handles malformed JSON"""
    def decode(self, s, **kwargs):
        regex_replacements = [
            (re.compile(r'([^\\])\\([^\\])'), r'\1\\\\\2'),
            (re.compile(r',(\s*])'), r'\1'),
        ]
        for regex, replacement in regex_replacements:
            s = regex.sub(replacement, s)
        return super().decode(s, **kwargs)


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

    r = requests.get(url, headers=headers)

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
    r = requests.get(url, headers=headers)

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
    r = requests.get(url, headers=headers)

    # Try curl fallback on 403
    if r.status_code == 403:
        logger.warning(f"Got 403 for {url}, trying curl fallback")
        content = fetch_url_with_curl(url)
        if content:
            return BeautifulSoup(content, features="html.parser")

    if r.status_code == 404:
        url = base_url.replace('index', f"/{season}/roster")
        r = requests.get(url, headers=headers)
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

    new_cols = [HEADERS[c] for c in cols]

    tbody = html.find('tbody')
    if not tbody:
        logger.warning(f"No tbody found for {team['team']}")
        return roster
    raw_players = [x for x in tbody.find_all('tr')]

    for raw_player in raw_players:
        [x.span.decompose() for x in raw_player.find_all('td') if x.find('span')]
        raw_player_list = [x.text.strip() for x in raw_player.find_all('td')]
        raw_player_list[4] = " ".join([x for x in raw_player_list[4].split()])
        if len(raw_player_list) < len(new_cols):
            name = " ".join([x.strip() for x in raw_player.find('a').text.replace("  ", "").strip().split()])
            raw_player_list.insert(1, name)
        player_dict = dict(zip(new_cols, raw_player_list))
        if 'high_school' not in player_dict:
            player_dict['town'], player_dict['high_school'] = [x.strip() for x in player_dict['town'].split('/', maxsplit=1)]
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

    new_cols = [HEADERS[c] for c in cols]
    tbody = html.find('tbody')
    if not tbody:
        logger.warning(f"No tbody found for {team['team']}")
        return roster
    raw_players = [x for x in tbody.find_all('tr')]

    # Major list for filtering (extensive list from original code)
    major_list = [
        'Nursing', 'Biology', 'Public Health', 'Exercise Science', 'Pre-Nursing', 'Economics',
        'Physical Therapy', 'Psychology', 'Business Administration', 'Criminal Justice/Psychology',
        'Forensic Science', 'Undecided', 'Management', 'Psychology / Management',
        'Political Science', 'Psychology / Pre-Medicine', 'Undeclared', 'Biomedical Engineering',
        'Business Marketing', 'Chemistry', 'Business', 'Computer Science', 'Business Management',
        # ... (abbreviated for brevity, but would include all majors from original)
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
                player_dict['town'], player_dict['high_school'] = [x.strip() for x in player_dict['town'].split('/', maxsplit=1)]

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
    except:
        return []

    for player in players:
        position = None

        # Skip specific known bad entries
        if player.find('a')['aria-label'].split(' - ')[0].strip() == 'Addison Jeansonne':
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
        except:
            hometown = None

        # Extract position (with multiple fallback strategies)
        if player.find('div', {'class': 'sidearm-roster-player-position'}).text.strip() == '':
            position = 'N/A'
        if not position and '"' in player.find('div', {'class': 'sidearm-roster-player-position'}).text.strip():
            position = player.find('div', {'class': 'sidearm-roster-player-position'}).text.strip().split()[0]
        if not position and player.find('div', {'class': 'sidearm-roster-player-position'}).find('span', {'class': 'text-bold'}).find('span', {'class': 'sidearm-roster-player-position-long-short hide-on-small-down'}):
            try:
                position = player.find('div', {'class': 'sidearm-roster-player-position'}).find('span', {'class': 'text-bold'}).find('span', {'class': 'sidearm-roster-player-position-long-short hide-on-small-down'}).text.strip()
            except AttributeError:
                position = None
        if not position and player.find('div', {'class': 'sidearm-roster-player-position'}).find('span', {'class': 'text-bold'}):
            try:
                position = player.find('div', {'class': 'sidearm-roster-player-position'}).find('span', {'class': 'text-bold'}).text.strip()
            except AttributeError:
                position = None

        # Extract jersey
        try:
            jersey = player.find('span', {'class': 'sidearm-roster-player-jersey-number'}).text.strip()
        except:
            jersey = None

        # Extract academic year
        try:
            academic_year = player.find_all('span', {'class': 'sidearm-roster-player-academic-year'})[1].text
        except:
            academic_year = None

        # Extract name
        try:
            name = player.find('a')['aria-label'].split(' - ')[0].strip()
        except:
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
            url=f"https://www.{er.domain}.{er.suffix}{player.find('a')['href']}",
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
        const id = '';
        const name = el.querySelector('.s-person-details__personal-single-line').innerText;
        const year = el.querySelectorAll('.s-person-details__bio-stats span')[2].innerText.split('\\n')[1].trim();
        const height = el.querySelectorAll('.s-person-details__bio-stats span')[4].innerText.split('\\n')[1].trim();
        const position = el.querySelectorAll('.s-person-details__bio-stats span')[0].innerText.split('\\n')[1].trim();
        const hometown = el.querySelectorAll('span.s-person-card__content__person__location-item')[0].innerText.replace("Hometown\\n","");
        const high_school = el.querySelectorAll('span.s-person-card__content__person__location-item')[1].innerText.split('\\n')[1];
        const previous_school = '';
        const jersey = el.querySelector('span.s-stamp__text').innerText;
        const url = el.querySelector('a')['href']
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """
    url = f"{team['url']}/roster/{season}"
    return shotscraper_caller(team, season, url, javascript_code)


def shotscraper_oregon_state(team: Dict, season: str) -> List[Dict]:
    """Oregon State - JavaScript rendered with s-table-body__row format"""
    javascript_code = """
    Array.from(document.querySelectorAll('.s-table-body__row'), el => {
        const id = '';
        const name = el.querySelectorAll('td')[2].innerText;
        const year = el.querySelectorAll('td')[5].innerText;
        const height = el.querySelectorAll('td')[4].innerText;
        const position = el.querySelectorAll('td')[3].innerText;
        const hometown = el.querySelectorAll('td')[6].innerText;
        const high_school = el.querySelectorAll('td')[7].innerText;
        const previous_school = el.querySelectorAll('td')[8].innerText;
        const jersey = el.querySelectorAll('td')[0].innerText;
        const url = el.querySelector('a')['href'];
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """
    url = f"{team['url']}/roster/{season}"
    return shotscraper_caller(team, season, url, javascript_code)


def shotscraper_roster_player2(team: Dict, season: str) -> List[Dict]:
    """Scraper for .sidearm-roster-player-container format (variant 2)"""
    javascript_code = """
    Array.from(document.querySelectorAll('.sidearm-roster-player-container'), el => {
        const id = '';
        const name = el.querySelector('h3').innerText;
        const year = el.querySelector('.sidearm-roster-player-academic-year').innerText;
        ht_el = el.querySelector('span.sidearm-roster-player-height');
        const height = ht_el ? ht_el.innerText : '';
        const position = el.querySelector('.sidearm-roster-player-position').innerText.split(' ')[0];
        const hometown = el.querySelector('.sidearm-roster-player-hometown').innerText;
        const high_school = el.querySelector('.sidearm-roster-player-highschool').innerText;
        ps_el = el.querySelector('.sidearm-roster-player-previous-school');
        const previous_school = ps_el ? ps_el.innerText : '';
        const jersey = el.querySelector('.sidearm-roster-player-jersey-number').innerText;
        const url = el.querySelector('a')['href']
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
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
     const name = el.querySelector('.sidearm-roster-player-name').innerText;
     const year = el.querySelector('.sidearm-roster-list-item-year').innerText;
     const height = el.querySelector('.sidearm-roster-list-item-height').innerText;
     const position = el.querySelector('.sidearm-roster-list-item-position').innerText;
     const hometown = el.querySelector('.sidearm-roster-list-item-hometown').innerText;
     const high_school = el.querySelector('.sidearm-roster-list-item-highschool').innerText;
     let prev_school = el.querySelector('.sidearm-roster-list-item-previous-school');
     const previous_school = prev_school ? prev_school.innerText : '';
     const jersey = el.querySelector('.sidearm-roster-list-item-photo-number').innerText;
     const url = el.querySelector('.sidearm-roster-player-name a')['href'];
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
     const name = el.querySelector(".sidearm-table-player-name").innerText;
     const year = el.querySelector(".roster_class").innerText;
     const height = el.querySelector(".height").innerText;
     const position = el.querySelector(".rp_position_short").innerText;
     const hometown = el.querySelector(".hometownhighschool").innerText.split('/')[0].trim();
     let hs = el.querySelector(".hometownhighschool").innerText.split('/');
     const high_school = hs[1] ? hs[1].trim() : '';
     const previous_school = el.querySelector(".player_previous_school").innerText;
     const jersey = el.querySelector(".roster_jerseynum").innerText;
     const url = el.querySelector(".sidearm-table-player-name a")['href'];
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
        const name = el.querySelector('.s-person-details__personal-single-line').innerText;
        const year = el.querySelectorAll('.s-person-details__bio-stats-item')[1].innerText.replace('Academic Year\\n ','');
        let ht = el.querySelectorAll('.s-person-details__bio-stats-item')[2];
        const height = ht ? ht.innerText.replace('Height\\n ','') : '';
        const position = el.querySelectorAll('.s-person-details__bio-stats-item')[0].innerText.replace('Position\\n','');
        const hometown = el.querySelectorAll('.s-person-card__content__person__location-item')[0].innerText.replace('Hometown\\n','');
        let hs_el = el.querySelectorAll('.s-person-card__content__person__location-item')[1];
        const high_school = hs_el ? hs_el.innerText.replace('Last School\\n','') : '';
        const previous_school = '';
        let j = el.querySelector('.s-stamp__text');
        const jersey = j ? j.innerText : '';
        const url = el.querySelector('a')['href']
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
        const name = el.querySelectorAll('td')[1].innerText;
        const year = el.querySelectorAll('td')[2].innerText;
        const height = el.querySelectorAll('td')[3].innerText;
        const position = el.querySelectorAll('td')[4].innerText;
        const hometown = el.querySelectorAll('td')[5].innerText;
        hs_el = el.querySelectorAll('td')[6];
        const high_school = hs_el ? hs_el.innerText : '';
        const previous_school = el.querySelectorAll('td')[7].innerText;
        const jersey = el.querySelectorAll('td')[0].innerText;
        const url = el.querySelectorAll('td')[1].querySelector('a')['href']
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
        const name = el.querySelector('h3').innerText;
        const year = el.querySelector('.sidearm-roster-player-academic-year').innerText;
        const height = el.querySelector('.sidearm-roster-player-height').innerText;
        const position = el.querySelector('.sidearm-roster-player-position-long-short').innerText.trim();
        const hometown = el.querySelector('.sidearm-roster-player-hometown').innerText;
        hs_el = el.querySelector('.sidearm-roster-player-highschool');
        const high_school = hs_el ? hs_el.innerText : '';
        ps_el = el.querySelector('.sidearm-roster-player-previous-school');
        const previous_school = ps_el ? ps_el.innerText : '';
        j = el.querySelector('.sidearm-roster-player-jersey-number');
        const jersey = j ? j.innerText : '';
        const url = el.querySelector('a')['href']
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """
    url = f"{team['url']}/roster/{season}"
    return shotscraper_caller(team, season, url, javascript_code)


# ============================================================================
# MAIN SCRAPING LOGIC
# ============================================================================

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

    # Open CSV for writing
    with open(f"data/rosters_{season}.csv", 'w') as output_file:
        csv_file = csv.writer(output_file)
        csv_file.writerow(['ncaa_id', 'team', 'player_id', 'name', 'year', 'hometown',
                          'high_school', 'previous_school', 'height', 'position', 'jersey',
                          'url', 'season'])

        for team in teams_with_urls:
            try:
                if team['ncaa_id'] == 26107 and season == '2021-22':
                    continue
                if 'roster' in team:
                    continue

                logger.info(f"Processing {team['team']}")

                # Route to appropriate scraper based on team ID
                roster = []

                # Skip specific teams
                if team['ncaa_id'] == 532:
                    continue

                # SPECIFIC FUNCTION SCRAPERS (must come first!)
                # BYU - Custom season format
                if team['ncaa_id'] == 77:
                    if str(season[0:1]):
                        season = f"{str(season)[0:5]}20{str(season[5:7])}"
                        roster = fetch_and_parse_byu(team, season)
                # San Jose State
                elif team['ncaa_id'] == 630:
                    roster = fetch_and_parse_sanjose(team, season)
                # Miami
                elif team['ncaa_id'] == 415:
                    roster = fetch_and_parse_miami(team, season)
                # Clemson
                elif team['ncaa_id'] == 147:
                    roster = fetch_and_parse_clemson(team, season)
                # Iowa State
                elif team['ncaa_id'] == 311:
                    roster = fetch_and_parse_iowa_state(team, season)
                # Vanderbilt
                elif team['ncaa_id'] == 736:
                    roster = fetch_and_parse_vandy(team, season)

                # SHOTSCRAPER WITH JAVASCRIPT EXTRACTION
                elif team['ncaa_id'] in [5, 308, 497, 554]:
                    roster = shotscraper_table(team, season)
                    if not roster:
                        logger.info(f"Shotscraper failed for {team['team']}, trying standard fetch")
                        html = fetch_roster(team['url'], season)
                        roster = parse_roster(team, html, season)
                elif team['ncaa_id'] in [9, 71, 83, 96, 99, 156, 173, 180, 191, 234, 249, 257,
                                        301, 306, 367, 387, 392, 400, 404, 418, 428, 441, 490,
                                        521, 522, 559, 574, 603, 635, 664, 671, 676, 688, 690,
                                        700, 719, 749, 758]:
                    roster = shotscraper_card(team, season)
                    if not roster:
                        logger.info(f"Shotscraper failed for {team['team']}, trying standard fetch")
                        html = fetch_roster(team['url'], season)
                        roster = parse_roster(team, html, season)
                elif team['ncaa_id'] in [51, 248, 731]:
                    roster = shotscraper_list_item(team, season)
                    if not roster:
                        logger.info(f"Shotscraper failed for {team['team']}, trying standard fetch")
                        html = fetch_roster(team['url'], season)
                        roster = parse_roster(team, html, season)
                elif team['ncaa_id'] in [37, 52, 175, 316, 487]:
                    roster = shotscraper_roster_player(team, season)
                    if not roster:
                        logger.info(f"Shotscraper failed for {team['team']}, trying standard fetch")
                        html = fetch_roster(team['url'], season)
                        roster = parse_roster(team, html, season)
                elif team['ncaa_id'] == 556:
                    roster = shotscraper_data_tables(team, season)
                    if not roster:
                        logger.info(f"Shotscraper failed for {team['team']}, trying standard fetch")
                        html = fetch_roster(team['url'], season)
                        roster = parse_roster(team, html, season)

                # TEAMS NEEDING JAVASCRIPT RENDERING (fetch HTML then parse with BeautifulSoup)
                elif TeamConfig.requires_javascript(team['ncaa_id']):
                    url = f"{team['url']}/roster/{season}"
                    html = fetch_url_with_javascript(url)
                    if html:
                        # Use appropriate parser based on URL pattern
                        if 'wvball' in team['url']:
                            roster = parse_roster_wbkb(team, html, season)
                        elif 'w-baskbl' in team['url']:
                            roster = parse_roster_baskbl(team, html, season)
                        else:
                            roster = parse_roster(team, html, season)
                    else:
                        # If JS rendering fails, try standard fetching as fallback
                        logger.info(f"JS rendering failed for {team['team']}, trying standard fetch as fallback")
                        if 'wvball' in team['url']:
                            html = fetch_wbkb_roster(team['url'], season)
                            if html:
                                roster = parse_roster_wbkb(team, html, season)
                        elif 'w-baskbl' in team['url']:
                            html = fetch_baskbl_roster(team['url'], season)
                            roster = parse_roster_baskbl(team, html, season)
                        else:
                            html = fetch_roster(team['url'], season)
                            roster = parse_roster(team, html, season)

                # URL-BASED ROUTING
                elif 'wvball' in team['url']:
                    # wvball teams can use either standard Sidearm or table format
                    # Try standard fetch first
                    html = fetch_roster(team['url'], season)
                    roster = []
                    if html:
                        # Try standard Sidearm parser first (most common)
                        roster = parse_roster(team, html, season)
                        # If standard parser returns nothing, try wbkb table parser
                        if not roster:
                            roster = parse_roster_wbkb(team, html, season)
                elif 'w-baskbl' in team['url']:
                    html = fetch_baskbl_roster(team['url'], season)
                    roster = parse_roster_baskbl(team, html, season)

                # DEFAULT: Standard roster page
                else:
                    html = fetch_roster(team['url'], season)
                    roster = parse_roster(team, html, season)

                # Write to CSV
                if len(roster) > 0:
                    for player in roster:
                        if isinstance(player, Player):
                            player_dict = player.to_dict()
                            csv_file.writerow([
                                player_dict['team_id'], player_dict['team'], player_dict['player_id'],
                                player_dict['name'], player_dict['year'], player_dict['hometown'],
                                player_dict['high_school'], player_dict['previous_school'],
                                player_dict['height'], player_dict['position'], player_dict['jersey'],
                                player_dict['url'], season
                            ])
                        else:
                            # Handle dict format from JS scrapers
                            csv_file.writerow([
                                player['team_id'], player['team'], player.get('id'),
                                player['name'], player['year'], player['hometown'],
                                player['high_school'], player['previous_school'],
                                player['height'], player['position'], player['jersey'],
                                player['url'], season
                            ])
                else:
                    unparsed.append(team['ncaa_id'])
            except Exception as e:
                logger.error(f"Error processing {team['team']}: {e}")
                skipped.append(team['ncaa_id'])
                continue

    return [unparsed, skipped]


def write_one_team(roster: List[Player], season: str):
    """Write a single team's roster to CSV (for adding missed teams)"""
    with open(f"rosters_{season}_adds.csv", 'a') as output_file:
        csv_file = csv.writer(output_file)
        for player in roster:
            if isinstance(player, Player):
                player_dict = player.to_dict()
                csv_file.writerow([
                    player_dict['team_id'], player_dict['team'], player_dict['player_id'],
                    player_dict['name'], player_dict['year'], player_dict['hometown'],
                    player_dict['high_school'], player_dict['previous_school'],
                    player_dict['height'], player_dict['position'], player_dict['jersey'],
                    player_dict['url'], season
                ])


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
