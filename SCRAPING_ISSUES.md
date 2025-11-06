# Scraping Issues for 2025-26 Season - Teams 721, 8, 697, 725, 528

## Summary
All 5 teams failed to scrape due to **SSL certificate validation failures** in the current environment. While browser automation tools are installed and working, they cannot connect to HTTPS sites due to invalid/missing SSL certificates.

## Teams Affected
- **721** - Air Force
- **8** - Alabama
- **697** - Texas A&M
- **725** - Army
- **528** - Oregon State

## Root Cause: SSL Certificate Validation Failure

All teams require JavaScript rendering, which works in the environment. However, Playwright/shot-scraper fails with:

```
playwright._impl._errors.Error: Page.goto: net::ERR_CERT_AUTHORITY_INVALID
```

This affects ALL HTTPS sites when using browser automation. The environment's SSL certificate chain is misconfigured or incomplete.

## What Works

✅ **Playwright browsers are installed** (chromium_headless_shell-1194)
✅ **shot-scraper is installed and functional**
✅ **`uv run shot-scraper html`** command works for non-HTTPS or properly certified sites
✅ **curl can fetch pages** (with valid certs)

## What Doesn't Work

❌ **SSL certificate validation** - Fails for all college athletics HTTPS sites
❌ **Browser access to HTTPS** - Cannot navigate to roster pages
❌ **shot-scraper has no --ignore-https-errors flag** for html command

## Additional Infrastructure Issues

### 1. HTTP Request Blocking
Python's `requests` library gets `403 Forbidden` from college athletics websites (workaround: use curl)

### 2. Browser Version Mismatch (Solved)
- Playwright 1.55.0 installed version 1194 browsers
- shot-scraper 1.8 expected version 1187 browsers
- **Solution**: Created symlink from 1187 → 1194

## Attempted Solutions

1. ✅ **Installed shot-scraper** via uv
2. ✅ **Fixed browser version mismatch** - Created symlink 1187 → 1194
3. ✅ **Added curl fallback** - works for HTTP but can't execute JavaScript
4. ✅ **Fixed tldextract offline mode** - resolved 403 from publicsuffix.org
5. ✅ **Updated rosters.py** - Now uses `uv run shot-scraper html` (soccer scraper approach)
6. ❌ **SSL certificate validation** - Cannot be bypassed in shot-scraper

## Requirements for Success

These teams REQUIRE an environment with:

1. **Valid SSL certificates** - Must be able to validate HTTPS connections
2. **Playwright browsers installed** - Already present (with symlink fix)
3. **shot-scraper accessible** - Already installed
4. **No network restrictions** - Or proper SSL cert chain configured

## Code Improvements Made

Even though scraping failed, the following improvements were added to `rosters.py`:

1. **curl fallback** for 403 errors from Python requests (lines 450-486)
2. **Offline tldextract** to avoid publicsuffix.org 403 (line 29)
3. **Better error handling** in fetch functions

These improvements will help when running in an environment with proper network access.

## Team-Specific Details

### Teams Using shot-scraper (JS Required)
- **721 (Air Force)**: Uses `shotscraper_airforce` - requires `.s-person-card` elements
- **8 (Alabama)**: Uses `shotscraper_roster_player2` - requires `.sidearm-roster-player-container`
- **725 (Army)**: Uses `shotscraper_roster_player2` - requires `.sidearm-roster-player-container`
- **528 (Oregon State)**: Uses `shotscraper_oregon_state` - requires `.s-table-body__row`

### Teams Using Standard Scrapers (Still JS Required)
- **697 (Texas A&M)**: Uses standard `parse_roster` - requires `.sidearm-roster-player`, but page is JS-rendered

## Recommended Next Steps

### Option 1: Fix SSL Certificates (Recommended)
1. **Install/update CA certificates** on the system
2. **Configure proper SSL cert chain** for Playwright/Chromium
3. Re-run the scraper - all code improvements are already in place

### Option 2: Run in Different Environment
1. **Local machine** with proper SSL certificates
2. **Cloud environment** (AWS, GCP, Azure) with valid certs
3. **Docker container** with proper SSL configuration

### Option 3: Manual Workaround
Since `curl` works with SSL, you could:
1. Use `curl` to download JavaScript-rendered pages (but this won't execute JS)
2. Use a proxy service that pre-renders JavaScript
3. Contact NCAA for API access (if available)

## Testing Commands

```bash
# Test SSL certificate issue
uv run shot-scraper html "https://goairforcefalcons.com/sports/womens-volleyball/roster/2025-26" --wait 3000

# If SSL works, test a single team
uv run python rosters.py -season 2025-26 -teams 721

# Test all 5 failing teams
uv run python rosters.py -season 2025-26 -teams 721 8 697 725 528
```

## Browser Symlink Fix (Required)

The environment has this browser version mismatch. To fix it:

```bash
# Create symlink for browser compatibility
cd /root/.cache/ms-playwright
ln -s chromium_headless_shell-1194 chromium_headless_shell-1187
```

This symlink is already created in the current environment.
