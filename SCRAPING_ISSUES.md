# Scraping Issues for 2025-26 Season - Teams 721, 8, 697, 725, 528

## Summary
All 5 teams failed to scrape due to infrastructure limitations in the current environment. These teams require JavaScript rendering to load roster data, but browser automation tools cannot be installed.

## Teams Affected
- **721** - Air Force
- **8** - Alabama
- **697** - Texas A&M
- **725** - Army
- **528** - Oregon State

## Root Causes

### 1. JavaScript-Rendered Pages
All 5 teams use modern JavaScript-heavy roster pages that don't include player data in the initial HTML. The data is loaded dynamically after the page loads.

### 2. Browser Installation Blocked
The environment has network restrictions preventing browser downloads:

- **Playwright** (used by shot-scraper):
  ```
  403 Forbidden from cdn.playwright.dev
  403 Forbidden from playwright.download.prss.microsoft.com
  ```

- **Pyppeteer** (used by requests-html):
  ```
  DNS resolution failure for storage.googleapis.com
  ```

### 3. HTTP Request Blocking
Python's `requests` library gets `403 Forbidden` from college athletics websites, though `curl` gets `200 OK`. However, curl still can't execute JavaScript.

## Attempted Solutions

1. ✅ **Installed shot-scraper** - but browsers can't be downloaded
2. ✅ **Added curl fallback** - works for HTTP but can't execute JavaScript
3. ✅ **Tried requests-html** - can't download embedded browser
4. ✅ **Fixed tldextract offline mode** - resolved 403 from publicsuffix.org
5. ❌ **JavaScript rendering** - fundamentally blocked by infrastructure

## Requirements for Success

These teams REQUIRE one of the following:

1. **Pre-installed browser** (Chromium/Playwright browsers in environment)
2. **Different network environment** (no blocking of CDN downloads)
3. **Alternative data source** (API or pre-rendered pages)
4. **Different scraping environment** (local machine, cloud VM with browser support)

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

1. **Run in local environment** with internet access and browser support
2. **Use a cloud environment** (AWS, GCP, Azure) with full network access
3. **Pre-install Playwright browsers** in the environment before running
4. **Contact IT** if this is a corporate/restricted network to whitelist CDN domains

## Testing Commands

To test if the environment can support scraping:

```bash
# Test Playwright installation
shot-scraper install

# Test a single team
uv run python rosters.py -season 2025-26 -teams 697

# Test all 5 failing teams
uv run python rosters.py -season 2025-26 -teams 721 8 697 725 528
```
