#!/usr/bin/env python3
"""Run the repo's own JS-render fetcher against one WAF-challenge team's 2025
roster URLs and report what page actually comes back (title, season verdict,
parser results).

Usage: uv run python -I analysis/probe_rendered_2025.py
"""
import json
import sys

sys.path.insert(0, 'src')
from rosters import (SeasonVerifier, _best_parse, _is_nuxt_page,  # noqa: E402
                     fetch_url_with_javascript)

TEAMS = {
    8981: 'Endicott',      # ecgulls.com
    939: 'Anderson (IN)',  # anderson.edu
    689: 'Tampa',          # tampaspartans.com (PrestoSport)
}
PATHS = ['/roster/2025', '/roster/2025-26', '/roster/season/2025-26',
         '/2025-26/roster', '/roster']

teams = {t['ncaa_id']: t for t in json.load(open('data/teams.json'))}
for tid in sys.argv[1:]:
    tid = int(tid)
    t = teams[tid]
    base = t['url'].rstrip('/')
    if base.endswith('/index'):
        stripped = base[:-6]
    else:
        stripped = None
    print(f'=== {tid} {t["team"]} (base {base})', flush=True)
    for base_ in [base] + ([stripped] if stripped else []):
        for path in PATHS:
            url = base_ + path
            html = fetch_url_with_javascript(url, wait_ms=5000)
            if html is None:
                print(f'  {path}: render failed', flush=True)
                continue
            title = html.title.get_text().strip() if html.title else '(no title)'
            verdict = SeasonVerifier.classify(html, '2025')
            roster = _best_parse(html, t, '2025') if verdict != 'mismatch' else []
            nuxt = _is_nuxt_page(html)
            print(f'  {path}: title="{title[:70]}" | season={verdict} | '
                  f'nuxt={nuxt} | players={len(roster)}', flush=True)
