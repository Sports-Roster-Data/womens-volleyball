"""Unit tests for the vb logs module (pure paths + a synthetic archive)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from vb import games, logs


class TestSideTeamStats:
    VALUES = {
        'Points': '67', 'Kills': '58', 'AttackErrors': '24',
        'TotalAttackAttempts': '190', 'HittingPercentage': '.179',
        'Assists': '56', 'ServiceAces': '5', 'ServiceErrors': '9',
        'BlockSolos': '0', 'BlockAssists': '8', 'BlockErrors': '1',
        'Digs': '91', 'TotalBlocks': '4.0', 'ReceptionErrors': '5',
        'BallHandlingErrors': '0',
    }
    SIDE = {'Totals': {'Values': VALUES}}

    def test_reference_mapping(self):
        stats = logs.side_team_stats(self.SIDE)
        assert stats['kills'] == 58
        assert stats['errors'] == 24
        assert stats['total_attacks'] == 190
        assert stats['assists'] == 56
        assert stats['aces'] == 5
        assert stats['serr'] == 9
        assert stats['digs'] == 91
        assert stats['rerr'] == 5
        assert stats['block_solos'] == 0
        assert stats['block_assists'] == 8
        assert stats['berr'] == 1
        assert stats['pts'] == 67
        assert stats['bhe'] == 0

    def test_hit_pct_computed_from_totals(self):
        # (58-24)/190 = 0.179 in this case; use values where it differs from
        # the feed's rounding to prove recomputation wins.
        values = dict(self.VALUES, Kills='41', AttackErrors='22',
                      TotalAttackAttempts='125', HittingPercentage='.136')
        stats = logs.side_team_stats({'Totals': {'Values': values}})
        assert stats['hit_pct'] == 0.152  # (41-22)/125, not the feed's .136

    def test_hit_pct_falls_back_to_feed(self):
        values = dict(self.VALUES, TotalAttackAttempts='0')
        stats = logs.side_team_stats({'Totals': {'Values': values}})
        assert stats['hit_pct'] == 0.179

    def test_tb_from_blocks(self):
        stats = logs.side_team_stats(self.SIDE)
        assert stats['tb'] == 4.0
        values = dict(self.VALUES, BlockSolos='2', BlockAssists='7')
        stats = logs.side_team_stats({'Totals': {'Values': values}})
        assert stats['tb'] == 5.5

    def test_retatt_has_no_counterpart(self):
        stats = logs.side_team_stats(self.SIDE)
        assert stats['retatt'] is None


class TestParseGameJson:
    def _game(self):
        return {
            'Game': {
                'Type': 'VolleyballGame', 'Date': '9/25/2025',
                'Location': 'Tuscaloosa, AL', 'Attendance': '1321',
                'Officials': '',
                'ClientHostname': 'belmontbruins.com',
                'HomeTeam': {'Name': 'Valparaiso', 'Score': 3,
                             'PeriodScores': [25, 21, 19]},
                'VisitingTeam': {'Name': 'Belmont', 'Score': 1,
                                 'PeriodScores': [29, 16, 25]},
            },
            'Stats': {
                'HomeTeam': {'Totals': {'Values': {'Kills': '50'}}},
                'VisitingTeam': {'Totals': {'Values': {'Kills': '58'}}},
            },
        }

    def test_both_sides_parsed(self):
        sides = logs.parse_game_json(self._game())
        assert sides['home']['name'] == 'Valparaiso'
        assert sides['visitor']['name'] == 'Belmont'
        assert sides['home']['period_scores'] == [25, 21, 19]
        assert sides['visitor']['stats']['kills'] == 58

    def test_non_volleyball_rejected(self):
        data = self._game()
        data['Game']['Type'] = 'BasketballGame'
        assert logs.parse_game_json(data) is None
        assert logs.parse_game_json({'data': {}}) is None


class TestResolveSides:
    PARSED = {
        'home': {'name': 'Valparaiso'},
        'visitor': {'name': 'Belmont'},
    }

    def test_slug_exact(self):
        assert logs.resolve_sides('belmont', 'Belmont', self.PARSED) == 'visitor'
        assert logs.resolve_sides('valparaiso', 'Valparaiso', self.PARSED) == 'home'

    def test_manifest_name_variant(self):
        # Slug misses ("belmontbruins"-style slug tail), manifest name hits.
        assert logs.resolve_sides('belmont-bruins', 'Belmont', self.PARSED) == 'visitor'

    def test_containment_resolves_uniquely(self):
        parsed = {'home': {'name': 'Belmont Bruins'}, 'visitor': {'name': 'Valparaiso'}}
        assert logs.resolve_sides('belmont', 'Belmont', parsed) == 'home'

    def test_ambiguous_containment_stays_unresolved(self):
        # Two sides both beyond the slug: neither or both matching stays blank.
        parsed = {'home': {'name': 'Belmont Away'}, 'visitor': {'name': 'Belmont Home'}}
        assert logs.resolve_sides('belmont', 'Belmont', parsed) is None
        parsed = {'home': {'name': 'Valparaiso'}, 'visitor': {'name': 'Purdue'}}
        assert logs.resolve_sides('belmont', 'Belmont', parsed) is None

    def test_no_match_stays_unresolved(self):
        parsed = {'home': {'name': 'Valparaiso'}, 'visitor': {'name': 'Purdue'}}
        assert logs.resolve_sides('belmont', 'Belmont', parsed) is None


class TestMatchTeamId:
    MANIFEST = {1: {'team': 'Belmont'}, 2: {'team': 'Fresno State'},
                3: {'team': 'Texas A&M-Corpus Christi'}, 4: {'team': 'Texas A&M'}}

    def test_exact(self):
        assert logs.match_team_id('Belmont', self.MANIFEST) == 1
        assert logs.match_team_id('FRESNO ST.', self.MANIFEST) == 2

    def test_prefix_ambiguity_stays_blank(self):
        assert logs.match_team_id('Texas A&M-Corpus Christi', self.MANIFEST) == 3
        assert logs.match_team_id('Texas A&M', self.MANIFEST) == 4  # exact wins

    def test_abbreviations_substring_match(self):
        assert logs.match_team_id('Fresno St.', self.MANIFEST) == 2
        assert logs.match_team_id('Belmont Bruins', self.MANIFEST) == 1

    def test_no_match(self):
        assert logs.match_team_id('Nowhere University', self.MANIFEST) is None

    def test_short_names_not_prefix_matched(self):
        assert logs.match_team_id('Bel', self.MANIFEST) is None

    def test_excludes_self(self):
        assert logs.match_team_id('Belmont', self.MANIFEST, exclude_id=1) is None


class TestOutputName:
    def test(self):
        # End-year label, matching the wbb log naming convention (wbblogs26_i).
        assert logs.output_name('2026-27', 'I') == 'vblogs27_i.csv'
        assert logs.output_name('2026-27', 'III') == 'vblogs27_iii.csv'


# ------------------------------------------------ synthetic archive test

def _raw_file(home_id, home, visitor, date='10/15/2025', host='belmontbruins.com'):
    return {
        'Game': {'Type': 'VolleyballGame', 'Date': date,
                 'Location': 'Curb Event Center', 'Attendance': '900',
                 'Officials': 'A. Ref; B. Ump', 'ClientHostname': host,
                 'HomeTeam': {'Name': home['name'], 'Score': home['sets'],
                              'PeriodScores': home['periods'],
                              'Logo': f'https://{host}/logos/{home["name"]}.png'},
                 'VisitingTeam': {'Name': visitor['name'], 'Score': visitor['sets'],
                                  'PeriodScores': visitor['periods']},
        },
        'Stats': {
            'HomeTeam': {'Totals': {'Values': {**_STATS, 'Kills': '50'}}},
            'VisitingTeam': {'Totals': {'Values': {**_STATS, 'Kills': '58'}}},
        },
    }


_STATS = {'Points': '67', 'Kills': '50', 'AttackErrors': '24',
          'TotalAttackAttempts': '190', 'HittingPercentage': '.179',
          'Assists': '56', 'ServiceAces': '5', 'ServiceErrors': '9',
          'BlockSolos': '0', 'BlockAssists': '8', 'BlockErrors': '1',
          'Digs': '91', 'TotalBlocks': '4.0', 'ReceptionErrors': '5',
          'BallHandlingErrors': '0'}


@pytest.fixture
def archive(tmp_path, monkeypatch):
    """A two-team synthetic archive: Belmont (D-I) hosting Valparaiso, plus
    a D-II team's game and a wrong-season game in Belmont's folder."""
    monkeypatch.setattr(logs, 'TEAMS_PATH',
                        Path(__file__).resolve().parent / 'fixtures' / 'teams_for_logs.json')
    monkeypatch.setattr(logs, 'DIVISIONS_PATH',
                        Path(__file__).resolve().parent / 'fixtures' / 'vb_teams_for_logs.csv')
    belmont = tmp_path / '14927-belmont' / '2025-26'
    valpo = tmp_path / '599-valparaiso' / '2025-26'   # Valparaiso is D-I? (fixture says no) - see csv
    belmont.mkdir(parents=True)
    valpo.mkdir(parents=True)
    (belmont / '7001.json').write_text(json.dumps(_raw_file(
        14927, {'name': 'Belmont', 'sets': 3, 'periods': [25, 21, 19, 26]},
        {'name': 'Valparaiso', 'sets': 1, 'periods': [29, 16, 25, 24]})))
    # Same game scraped by the visitor's site too.
    (valpo / '7001.json').write_text(json.dumps(_raw_file(
        599, {'name': 'Valparaiso', 'sets': 1, 'periods': [29, 16, 25, 24]},
        {'name': 'Belmont', 'sets': 3, 'periods': [25, 21, 19, 26]},
        host='valpo.brightspotcloud.com')))
    # A D-II division team's game (excluded from a D-I log).
    d2 = tmp_path / '100-dii-team' / '2025-26'
    d2.mkdir(parents=True)
    (d2 / '7002.json').write_text(json.dumps(_raw_file(
        100, {'name': 'DII Team', 'sets': 3, 'periods': [25, 25, 25]},
        {'name': 'Somebody', 'sets': 0, 'periods': [15, 15, 15]},
        host='diiteam.com')))
    # A wrong-season (site-echo) game inside Belmont's folder.
    (belmont / '7003.json').write_text(json.dumps(_raw_file(
        14927, {'name': 'Belmont', 'sets': 3, 'periods': [25, 25, 25]},
        {'name': 'Echo Foe', 'sets': 0, 'periods': [15, 15, 15]},
        date='10/15/2024')))
    return tmp_path


def _rows(archive, division='I', season='2025-26'):
    return logs.build_game_logs(season, division, data_dir=archive)


def test_build_rows(archive):
    rows, info = _rows(archive)
    # One row for Belmont (D-I); Valparaiso is not in the fixture manifest,
    # so its copy contributes nothing; D-II excluded; wrong season excluded.
    assert len(rows) == 1
    row = rows[0]
    assert row['team'] == 'Belmont'
    assert row['opponent'] == 'Valparaiso'
    assert row['home_away'] == 'Home'
    assert row['team_score'] == 3 and row['opponent_score'] == 1
    assert row['result'] == 'W'
    assert row['s'] == 4
    assert row['set1'] == '25-29'  # Belmont lost the first set
    assert row['set4'] == '26-24'
    assert row['kills'] == 50      # Belmont is home side
    assert row['defensive_kills'] == 58
    assert row['defensive_hit_pct'] == 0.179  # (58-24)/190
    assert row['hit_pct'] == 0.137  # (50-24)/190
    assert row['attendance'] == 900
    assert row['location'] == 'Curb Event Center'
    assert row['officials'] == 'A. Ref; B. Ump'
    assert row['opponent_ncaa_id'] == 599
    assert row['game_no'] == 1
    assert row['date'] == '2025-10-15'
    assert row['url'].endswith('14927-belmont/2025-26/7001.json')
    assert info['outside_season'] == 1
    assert info['outside_division'] == 2  # Valparaiso (D-III) + DII Team folders
    assert info['teams'] == 1


def test_visitor_side_rows(archive):
    rows, _ = _rows(archive, division='I')
    row = rows[0]
    assert row['kills'] == 50 and row['defensive_kills'] == 58


def test_division_filter(archive, tmp_path):
    # Make the D-II team D-I: its game must then appear.
    rows, info = logs.build_game_logs('2025-26', 'II', data_dir=archive)
    assert len(rows) == 1
    assert rows[0]['team'] == 'DII Team'


def test_write(archive, tmp_path):
    rows, info = _rows(archive)
    out = tmp_path / 'vblogs25_i.csv'
    logs.write_game_logs(rows, out)
    text = out.read_text().splitlines()
    header = text[0].split(',')
    assert header[:9] == ['ncaa_id', 'date', 'team', 'opponent', 'home_away',
                          'result', 'team_score', 'opponent_score', 's']
    assert 'defensive_kills' in header
    assert 'opponent_ncaa_id' in header
    values = dict(zip(header, text[1].split(',')))
    assert values['team'] == 'Belmont'
