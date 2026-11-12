#!/usr/bin/env python3
"""Register a raw rosters snapshot into data/reference/raw_snapshots.csv.

Computes the sha256 of the current raw season CSV and upserts one row,
keeping a lineage of every distinct raw vintage so the R cleaning pipeline
can pin a run to an exact file.

Example:
    python scripts/register_snapshot.py -season 2025-26
    python scripts/register_snapshot.py -season 2025        # year-only works
    python scripts/register_snapshot.py -season 2025-26 -teams

Schema (exact header):
    season,snapshot_id,raw_path,raw_season_value,sha256,retrieved_at,active,notes
"""

import argparse
import csv
import hashlib
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Let the script import the scraper's helpers when run from the repo root
# (python scripts/register_snapshot.py) or from anywhere else.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.rosters import canonical_season_label  # noqa: E402

HEADER = [
    'season', 'snapshot_id', 'raw_path', 'raw_season_value', 'sha256',
    'retrieved_at', 'active', 'notes',
]

# Data paths resolve against the working directory, like every other data
# path in this repo (run the script from the repository root).
REFERENCE_PATH = Path('data') / 'reference' / 'raw_snapshots.csv'
RETRIEVED_AT_RE = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z')


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_rows(path: Path, rows) -> None:
    """Write all rows (header + records) atomically via temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name, suffix='.tmp')
    os.close(fd)
    try:
        with open(tmp_name, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(HEADER)
            writer.writerows(rows)
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Register a raw roster CSV snapshot (sha256 lineage).')
    parser.add_argument('-season', dest='season', required=True,
                        help='Season: year-only ("2025") or full academic-year '
                             'label ("2025-26")')
    parser.add_argument('-retrieved-at', dest='retrieved_at', default=None,
                        help='Override the retrieved_at timestamp '
                             '(UTC, "%%Y-%%m-%%dT%%H:%%M:%%SZ") for '
                             'reproducibility')
    parser.add_argument('-teams', dest='teams', action='store_true',
                        help='Mark the notes as a targeted -teams run '
                             '(default notes: -season run)')
    parser.add_argument('-notes', dest='notes', default=None,
                        help='Free-text notes for the new row')
    args = parser.parse_args()

    if args.retrieved_at and not RETRIEVED_AT_RE.match(args.retrieved_at):
        print(f"ERROR: -retrieved-at must be UTC '%Y-%m-%dT%H:%M:%SZ'; "
              f"got {args.retrieved_at!r}", file=sys.stderr)
        return 2

    label = canonical_season_label(args.season)
    raw_path = f"data/rosters_{label}.csv"
    csv_path = Path(raw_path)
    if not csv_path.exists():
        print(f"ERROR: {raw_path} does not exist; run the scraper first",
              file=sys.stderr)
        return 1

    digest = sha256_of(csv_path)

    existing = []
    if REFERENCE_PATH.exists():
        with open(REFERENCE_PATH, newline='') as f:
            reader = csv.reader(f)
            rows = list(reader)
        if rows and rows[0] == HEADER:
            existing = rows[1:]
        else:
            existing = rows

    # No-op when this exact vintage is already registered.
    raw_path_idx = HEADER.index('raw_path')
    sha_idx = HEADER.index('sha256')
    for row in existing:
        if len(row) > max(raw_path_idx, sha_idx) and \
                row[raw_path_idx] == raw_path and row[sha_idx] == digest:
            print(f"already registered {raw_path} sha256={digest}")
            return 0

    season_idx = HEADER.index('season')
    id_idx = HEADER.index('snapshot_id')
    used = set()
    for row in existing:
        if len(row) > id_idx and row[season_idx] == label and row[id_idx]:
            match = re.fullmatch(re.escape(label) + r'_(\d+)', row[id_idx])
            if match:
                used.add(int(match.group(1)))
    snapshot_id = f"{label}_{next(n for n in range(1, len(used) + 2) if n not in used)}"

    retrieved_at = args.retrieved_at or datetime.now(
        timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    notes = args.notes if args.notes is not None else (
        '-teams target' if args.teams else '-season run')
    new_row = [label, snapshot_id, raw_path, label, digest,
               retrieved_at, 'TRUE', notes]

    # Deactivate any other active row for this season in the same write.
    active_idx = HEADER.index('active')
    updated = []
    for row in existing:
        if len(row) > active_idx and row[season_idx] == label and \
                row[active_idx] == 'TRUE':
            row = list(row)
            row[active_idx] = 'FALSE'
        updated.append(row)
    updated.append(new_row)

    atomic_write_rows(REFERENCE_PATH, updated)
    print(f"registered {snapshot_id} {raw_path} sha256={digest}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
