#!/usr/bin/env python3
"""Probe the 2025-unparsed teams' sites: which roster URL forms exist, and
does the served page identify season 2025 or the current season (2026)?

Usage: uv run python -I analysis/probe_2025_urls.py
"""
import json
import re
import sys
import time

import requests

IDS = [37, 415, 312, 513, 626, 61, 127, 255, 523, 692, 473, 1000, 1023, 1050,
       1130, 119, 1199, 125, 12830, 1348, 1355, 64, 216, 217, 218, 224, 227,
       243, 24317, 25719, 2798, 284, 30042, 30085, 443, 1315, 449, 455, 486,
       510, 517, 525, 538, 566, 569, 641, 684, 689, 74, 746, 762, 785, 806,
       809, 8981, 939]

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/95.0.4638.69 Safari/537.36")

def forms(base: str):
    b = base.rstrip('/')
    b2 = b[:-len('/index')] if b.endswith('/index') else b
    return {
        '/roster/2025': b + '/roster/2025',
        '/roster/2025-26': b2 + '/roster/2025-26',
        '/2025/roster': b2 + '/2025/roster',
        '/2025-26/roster': b2 + '/2025-26/roster',
        '/roster/season/2025': b2 + '/roster/season/2025',
        '/roster/season/2025-26': b2 + '/roster/season/2025-26',
    }

def title_of(text: str) -> str:
    m = re.search(r'<title[^>]*>([^<]*)', text)
    return (m.group(1) if m else '').strip()

def main():
    teams = {t['ncaa_id']: t for t in json.load(open('data/teams.json'))}
    s = requests.Session()
    s.headers['user-agent'] = UA
    stats = {}
    for tid in IDS:
        t = teams[tid]
        base = t['url']
        out = [f"{tid} {t['team']}:"]
        for label, url in forms(base).items():
            try:
                r = s.get(url, timeout=15, allow_redirects=True)
                title = title_of(r.text)
                verdict = '?'
                if r.status_code == 200:
                    if '2025' in title and '2026' not in title.split('2025')[-1][:4]:
                        verdict = '2025!' if 'roster' in title.lower() else '2025?'
                    if '2025-26' in title or '2025-2026' in title:
                        verdict = '2025-26'
                    elif re.search(r'\b2025\b', title):
                        verdict = '2025'
                    elif re.search(r'\b(2026|2026-27|2027)\b', title):
                        verdict = 'shows 2026'
                    elif r.status_code == 200 and ('not found' in title.lower() or title.lower().startswith('40')):
                        verdict = 'soft-404'
                    else:
                        verdict = 'no year in title'
                else:
                    verdict = f'HTTP {r.status_code}'
                out.append(f"{label}={verdict}")
            except requests.RequestException as e:
                out.append(f"{label}=ERR")
                stats['err'] = stats.get('err', 0) + 1
            time.sleep(0.25)
        print(' | '.join(out), flush=True)
        stats[None] = None

if __name__ == '__main__':
    main()
