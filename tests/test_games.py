"""Unit tests for the vb games module (pure functions; no network)."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vb import games


class TestCanonicalSeasonLabel:
    def test_year_only(self):
        assert games.canonical_season_label("2026") == "2026-27"
        assert games.canonical_season_label("1999") == "1999-00"

    def test_full_label_passthrough(self):
        assert games.canonical_season_label("2025-26") == "2025-26"

    def test_strips_whitespace(self):
        assert games.canonical_season_label(" 2026 ") == "2026-27"


class TestValidateSeason:
    def test_accepts_full_labels(self):
        games.validate_season("2025-26")
        games.validate_season("1999-00")

    def test_rejects_year_only(self):
        try:
            games.validate_season("2025")
        except ValueError:
            pass
        else:
            raise AssertionError("year-only season must be rejected pre-canonicalization")

    def test_rejects_mismatched_years(self):
        try:
            games.validate_season("2025-27")
        except ValueError:
            pass
        else:
            raise AssertionError("non-consecutive years must be rejected")


class TestGameIdsFromLinks:
    def test_aspnet_style(self):
        assert games.game_ids_from_links(
            ["/boxscore.aspx?id=21789", "/boxscore.aspx?id=21788&path=wvolley"]) \
            == ["21789", "21788"]

    def test_modern_segment_style(self):
        href = "/sports/womens-volleyball/stats/2025/florida/boxscore/16437"
        assert games.game_ids_from_links([href]) == ["16437"]

    def test_mixed_layouts_deduplicated(self):
        links = ["/boxscore.aspx?id=100", "https://x.com/a/boxscore/100"]
        assert games.game_ids_from_links(links) == ["100"]

    def test_skips_non_ids(self):
        assert games.game_ids_from_links(["/sports/boxscore-index/"]) == []


class TestSeasonDateBounds:
    def test_fall_to_late_spring_window(self):
        lo, hi = games.season_date_bounds("2025-26")
        assert (lo, hi) == (date(2025, 8, 1), date(2026, 6, 30))

    def test_accepts_december_and_spring(self):
        lo, hi = games.season_date_bounds("2020-21")
        assert date(2020, 9, 1) >= lo and date(2021, 4, 1) <= hi


class TestGameInSeason:
    def _json(self, d):
        return {"Game": {"Date": d, "Type": "VolleyballGame"}}

    def test_current_season_mixed_into_old_slug_is_rejected(self):
        # rolltide-style echo: /stats/2025 serves 11/11/2026 games.
        assert games.game_in_season(self._json("11/11/2026"), "2025-26") is False

    def test_in_season_dates(self):
        assert games.game_in_season(self._json("9/5/2025"), "2025-26") is True
        assert games.game_in_season(self._json("12/12/2025"), "2025-26") is True
        assert games.game_in_season(self._json("4/10/2026"), "2025-26") is True

    def test_prior_season_rejected(self):
        assert games.game_in_season(self._json("9/5/2024"), "2025-26") is False

    def test_missing_date_is_none(self):
        assert games.game_in_season({"Game": {}}, "2025-26") is None
        assert games.game_in_season(None, "2025-26") is None

    def test_iso_format_date(self):
        assert games.game_in_season(self._json("2025-09-05"), "2025-26") is True


class TestSlugify:
    def test_matches_wbb_layout(self):
        assert games.slugify({"ncaa_id": 8, "team": "Alabama"}) == "8-alabama"
        assert games.slugify({"ncaa_id": 721, "team": "Air Force"}) == "721-air-force"


class TestScheduleUrls:
    def test_includes_season_and_hints(self):
        urls = games.schedule_urls("https://rolltide.com/sports/womens-volleyball", "2025-26")
        assert urls[0].endswith("/schedule/season/2025-26/")
        assert any("path=wvolley" in u for u in urls)
