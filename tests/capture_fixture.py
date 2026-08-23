#!/usr/bin/env python3
"""Capture a live roster page as a test fixture.

Run this manually (never in CI) when a site changes and you need a fresh
fixture to test a parser against. It fetches a team's rendered HTML through
the same code path the scraper uses and saves it under tests/fixtures/.

Usage:
    uv run python tests/capture_fixture.py <parser_dir> <name> <url> [season]

Example:
    uv run python tests/capture_fixture.py parse_roster nebraska \\
        https://huskers.com/sports/womens-volleyball 2025-26

Then add assertions for it in tests/test_parsers.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import rosters  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    parser_dir, name, url = sys.argv[1], sys.argv[2], sys.argv[3]
    season = sys.argv[4] if len(sys.argv) > 4 else "2025-26"

    soup = rosters.fetch_roster(url, season)
    if soup is None:
        print(f"Fetch returned nothing for {url}")
        sys.exit(1)

    out = FIXTURES / parser_dir / f"{name}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(str(soup))
    print(f"Wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
