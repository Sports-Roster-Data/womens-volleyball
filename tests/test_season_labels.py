"""Tests for canonical season labels, the school-field ports, the
parenthesized hometown split, the archive-on-change CSV writer, and the
snapshot register script."""
import csv
import hashlib
import pathlib
import subprocess
import sys

import pytest

import rosters

SCRIPTS_DIR = pathlib.Path(rosters.__file__).resolve().parent.parent / 'scripts'
REGISTER_SCRIPT = SCRIPTS_DIR / 'register_snapshot.py'


# ============================================================================
# canonical_season_label
# ============================================================================

@pytest.mark.parametrize('given,expected', [
    ('2025', '2025-26'),
    ('2026-27', '2026-27'),
    ('2023-24', '2023-24'),
    (' 2025 ', '2025-26'),
])
def test_canonical_season_label_conversions(given, expected):
    assert rosters.canonical_season_label(given) == expected


@pytest.mark.parametrize('bad', [
    'abc', '25', '25-26', '2025-6', '2025-262', '20251', '', '20 25', None,
])
def test_canonical_season_label_rejects(bad):
    with pytest.raises(ValueError):
        rosters.canonical_season_label(bad)


# ============================================================================
# Transfer-suffix / marker reclassify (ported from the field-hockey scraper)
# ============================================================================

def test_reclassifies_university_transfer_suffix():
    row = {'high_school': '', 'previous_school':
           'Deer Park HS (University of Houston)'}
    assert rosters.reclassify_high_school_from_previous(row) is True
    assert row['high_school'] == 'Deer Park HS'
    assert row['previous_school'] == 'University of Houston'


def test_reclassifies_uc_transfer_suffix():
    row = {'high_school': '',
           'previous_school': 'La Costa Canyon HS (UC Davis)'}
    assert rosters.reclassify_high_school_from_previous(row) is True
    assert row['high_school'] == 'La Costa Canyon HS'
    assert row['previous_school'] == 'UC Davis'


def test_transfer_suffix_requires_university_or_uc():
    # A non-transfer paren suffix is not a transfer; the whole value moves
    # wholesale into high_school instead.
    row = {'high_school': '', 'previous_school': 'Deer Park HS (Ohio State)'}
    assert rosters.reclassify_high_school_from_previous(row) is True
    assert row['high_school'] == 'Deer Park HS (Ohio State)'
    assert row['previous_school'] == ''


def test_lyceum_marker():
    row = {'high_school': '', 'previous_school': 'Lyceum Alpinum Zuoz'}
    assert rosters.reclassify_high_school_from_previous(row) is True
    assert row['high_school'] == 'Lyceum Alpinum Zuoz'
    assert row['previous_school'] == ''


def test_no_marker_stays_alone():
    row = {'high_school': '', 'previous_school': 'Ohio State'}
    assert rosters.reclassify_high_school_from_previous(row) is False
    assert row['high_school'] == ''


# ============================================================================
# split_town_high_school
# ============================================================================

def test_parenthesized_hometown_split():
    assert rosters.split_town_high_school(
        "La Jolla, Calif. (The Bishop's School)") == ('La Jolla, Calif.',
                                                      "The Bishop's School")


def test_slash_split_still_first():
    assert rosters.split_town_high_school(
        'Huber Heights, Ohio / Wayne High School') == (
        'Huber Heights, Ohio', 'Wayne High School')


def test_plain_hometown_unsplit():
    assert rosters.split_town_high_school('Massena, NY') == ('Massena, NY', None)
    assert rosters.split_town_high_school('') == ('', None)
    assert rosters.split_town_high_school(None) == ('', None)


# ============================================================================
# write_rosters_csv archive-on-change
# ============================================================================

def csv_row(name, ncaa_id='100', season='2025-26'):
    return [ncaa_id, 'Team', '', name, '', '', '', '', '', '', '', '', season]


def read_names(path):
    rows = list(csv.reader(open(path)))
    return [r[3] for r in rows[1:]]


def test_archive_created_on_changed_content(tmp_path):
    path = tmp_path / 'rosters_2025-26.csv'
    rosters.write_rosters_csv(str(path), [csv_row('v1')], replace_ids=None)
    rosters.write_rosters_csv(str(path), [csv_row('v2')], replace_ids=None)

    archive_dir = tmp_path / 'raw_archive'
    assert archive_dir.is_dir()
    archives = list(archive_dir.iterdir())
    assert len(archives) == 1
    name = archives[0].name
    assert name.startswith('rosters_2025-26.') and name.endswith('.csv')
    assert read_names(str(archives[0])) == ['v1']
    assert read_names(str(path)) == ['v2']


def test_no_archive_on_identical_rewrite(tmp_path):
    path = tmp_path / 'rosters_2025-26.csv'
    rows = [csv_row('v1', ncaa_id='100'), csv_row('v2', ncaa_id='200')]
    rosters.write_rosters_csv(str(path), rows, replace_ids=None)
    rosters.write_rosters_csv(str(path), rows, replace_ids=None)
    # A partial replace that re-writes the last team's rows with identical
    # content yields identical bytes, so it must not archive a vintage either.
    rosters.write_rosters_csv(str(path), [csv_row('v2', ncaa_id='200')],
                              replace_ids={'200'})
    assert not (tmp_path / 'raw_archive').exists()


def test_no_archive_on_first_write(tmp_path):
    path = tmp_path / 'rosters_2025-26.csv'
    rosters.write_rosters_csv(str(path), [csv_row('v1')], replace_ids=None)
    assert not (tmp_path / 'raw_archive').exists()


def test_env_override_archive_dir(tmp_path, monkeypatch):
    archive_dir = tmp_path / 'elsewhere'
    monkeypatch.setenv('ROSTERS_RAW_ARCHIVE_DIR', str(archive_dir))
    path = tmp_path / 'rosters_2025-26.csv'
    rosters.write_rosters_csv(str(path), [csv_row('v1')], replace_ids=None)
    rosters.write_rosters_csv(str(path), [csv_row('v2')], replace_ids=None)
    assert list(archive_dir.iterdir())
    assert not (tmp_path / 'raw_archive').exists()


# ============================================================================
# register_snapshot.py
# ============================================================================

def write_season_csv(root, label, name):
    data = root / 'data'
    data.mkdir(parents=True, exist_ok=True)
    with open(data / f'rosters_{label}.csv', 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(rosters.CSV_HEADER)
        w.writerows([csv_row(name)])


def run_register(root, *args):
    return subprocess.run(
        [sys.executable, str(REGISTER_SCRIPT), *args],
        capture_output=True, text=True, cwd=root)


def reference_rows(root):
    ref = root / 'data' / 'reference' / 'raw_snapshots.csv'
    return list(csv.DictReader(open(ref)))


def test_register_snapshot_workflow(tmp_path):
    root = tmp_path
    write_season_csv(root, '2025-26', 'v1')
    result = run_register(root, '-season', '2025-26')
    assert result.returncode == 0, result.stderr
    rows = reference_rows(root)
    assert len(rows) == 1
    assert rows[0]['season'] == '2025-26'
    assert rows[0]['raw_season_value'] == '2025-26'
    assert rows[0]['snapshot_id'] == '2025-26_1'
    assert rows[0]['raw_path'] == 'data/rosters_2025-26.csv'
    assert rows[0]['active'] == 'TRUE'
    assert rows[0]['notes'] == '-season run'
    assert rows[0]['retrieved_at'].endswith('Z')
    assert rows[0]['sha256'] == hashlib.sha256(
        open(root / 'data' / 'rosters_2025-26.csv', 'rb').read()).hexdigest()

    # Registering the same bytes again is a no-op.
    again = run_register(root, '-season', '2025')
    assert again.returncode == 0
    assert 'already registered' in again.stdout
    assert len(reference_rows(root)) == 1

    # A changed file appends a second snapshot and deactivates the first.
    write_season_csv(root, '2025-26', 'v2')  # overwrite in place
    changed = run_register(root, '-season', '2025-26', '-teams')
    assert changed.returncode == 0, changed.stderr
    rows = reference_rows(root)
    assert [r['snapshot_id'] for r in rows] == ['2025-26_1', '2025-26_2']
    assert [r['active'] for r in rows] == ['FALSE', 'TRUE']
    assert rows[1]['notes'] == '-teams target'
    assert rows[0]['sha256'] != rows[1]['sha256']


def test_register_snapshot_skips_used_ids(tmp_path):
    root = tmp_path
    write_season_csv(root, '2025-26', 'v1')
    ref = root / 'data' / 'reference'
    ref.mkdir(parents=True)
    seed = [
        ['2025-26', '2025-26_1', 'data/rosters_2025-26.csv', '2025-26',
         '0' * 64, '2026-01-01T00:00:00Z', 'FALSE', 'seeded'],
        ['2025-26', '2025-26_3', 'data/rosters_2025-26.csv', '2025-26',
         '1' * 64, '2026-01-01T00:00:00Z', 'FALSE', 'seeded'],
        ['2024-25', '2024-25_1', 'data/rosters_2024-25.csv', '2024-25',
         '2' * 64, '2026-01-01T00:00:00Z', 'TRUE', 'other season'],
    ]
    with open(ref / 'raw_snapshots.csv', 'w', newline='') as f:
        w = csv.writer(f)
        w.writerows([[
            'season', 'snapshot_id', 'raw_path', 'raw_season_value',
            'sha256', 'retrieved_at', 'active', 'notes']])
        w.writerows(seed)
    changed = run_register(root, '-season', '2025-26',
                           '-retrieved-at', '2026-02-03T04:05:06Z')
    assert changed.returncode == 0, changed.stderr
    rows = reference_rows(root)
    assert [r['active'] for r in rows] == ['FALSE', 'FALSE', 'TRUE', 'TRUE']
    assert rows[3]['snapshot_id'] == '2025-26_2'   # smallest unused
    assert rows[3]['retrieved_at'] == '2026-02-03T04:05:06Z'
    # The other season's active row is untouched.
    assert rows[2]['season'] == '2024-25'


def test_register_snapshot_missing_file(tmp_path):
    root = tmp_path
    (root / 'data').mkdir(parents=True)
    result = run_register(root, '-season', '2025-26')
    assert result.returncode != 0
    assert 'does not exist' in result.stderr
    assert not (root / 'data' / 'reference').exists()
