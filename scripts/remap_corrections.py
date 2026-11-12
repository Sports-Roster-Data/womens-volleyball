#!/usr/bin/env python3
"""Re-point snapshot-scoped corrections to the current snapshot id.

Corrections in corrections/cell_corrections.csv and row_corrections.csv are
scoped to one snapshot (`<season>_<n>#<file-line>`), and the R build only
applies rows whose id prefix matches the season's ACTIVE snapshot. When a
season is re-scraped and re-registered, old ids go stale and their reviewed
decisions silently stop applying (the loader filters stale prefixes, it
does not warn).

This script rewrites stale ids to the active snapshot by matching rows
between the old vintage (found in data/raw_archive/ by its registered
sha256) and the current raw file:

- rows map by (ncaa_id, occurrence of the row's 13-field content within
  that team), so identical-content rows keep distinct targets;
- a row unmapped in the current file is obsolete (it described a replaced
  bad/echo row); its corrections are dropped, with a log line each.

Row ids are 1-indexed file lines with the header as line 1 (the R build's
`source_row_number = row_number() + 1`).

Re-run the affected seasons' builds after this script.
"""
import csv
import hashlib
import sys
from collections import defaultdict
from pathlib import Path

REGISTRY = Path("data/reference/raw_snapshots.csv")
CORRECTIONS_DIR = Path("corrections")
ARCHIVE_DIR = Path("data/raw_archive")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_rows(path: Path):
    with path.open(newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        return header, list(reader)


def occurrences(rows):
    """line number (header=1) per (ncaa_id, row content)."""
    by_key = defaultdict(list)
    for line, row in enumerate(rows, start=2):
        by_key[(row[0], tuple(row))].append(line)
    return by_key


def find_vintage(old_sha: str, raw_stem: str):
    """Archive file whose bytes hash to the old snapshot's registered sha."""
    for p in sorted(ARCHIVE_DIR.glob(f"{raw_stem}*")):
        if sha256_file(p) == old_sha:
            return p
    return None


class Remapper:
    def __init__(self, registry):
        self.by_season = defaultdict(list)
        for r in registry:
            self.by_season[r["season"]].append(r)
        self.mapping_cache = {}

    def season_of_prefix(self, prefix):
        """'2015-16_1' -> ('2015-16', '2015-16_1')."""
        label, n = prefix.rsplit("_", 1)
        return label, prefix

    def map_id(self, old_id):
        """Return the re-pointed id, the old id, or None if the row is gone."""
        if "#" not in old_id:
            return old_id
        prefix, line_s = old_id.split("#")
        label = prefix.rsplit("_", 1)[0]
        actives = [r for r in self.by_season[label] if r["active"] == "TRUE"]
        if not actives:
            return old_id
        active = actives[-1]
        if prefix == active["snapshot_id"]:
            return old_id
        snaps = {r["snapshot_id"]: r for r in self.by_season[label]}
        old = snaps.get(prefix)
        if old is None:
            raise SystemExit(f"registry has no snapshot {prefix!r}")
        cache_key = prefix
        if cache_key not in self.mapping_cache:
            raw_stem = Path(old["raw_path"]).stem
            vintage = find_vintage(old["sha256"], raw_stem)
            if vintage is None:
                raise SystemExit(f"no archive matches the registered sha for {prefix}")
            _, old_rows = read_rows(vintage)
            _, new_rows = read_rows(Path(active["raw_path"]))
            occ = occurrences(new_rows)
            used = set()
            mapping = {}
            for line, row in enumerate(old_rows, start=2):
                cands = [c for c in occ.get((row[0], tuple(row)), [])
                         if c not in used]
                target = cands[0] if cands else None
                mapping[line] = target
                if target is not None:
                    used.add(target)
            self.mapping_cache[cache_key] = mapping
        target = self.mapping_cache[cache_key].get(int(line_s))
        if target is None:
            return None
        # The R build formats row ids as sprintf("%05d", source_row_number).
        return f"{active['snapshot_id']}#{target:05d}"


def main():
    remapper = Remapper(load_registry := list(csv.DictReader(REGISTRY.open())))
    cell_path, row_path = (CORRECTIONS_DIR / "cell_corrections.csv",
                           CORRECTIONS_DIR / "row_corrections.csv")

    stats = {"remapped": 0, "stale_dropped": 0}
    dropped = []
    for path in (cell_path, row_path):
        if not path.exists():
            continue
        with path.open(newline="") as f:
            reader = csv.DictReader(f)
            fields = reader.fieldnames
            rows = list(reader)
        kept = []
        for r in rows:
            new_id = remapper.map_id(r["source_row_id"])
            keeper = r.get("keeper_source_row_id") or ""
            new_keeper = remapper.map_id(keeper) if keeper else keeper
            if new_id is None or (keeper and new_keeper is None):
                stats["stale_dropped"] += 1
                dropped.append((path.name, r["source_row_id"],
                                keeper or r.get("action", "")))
                continue
            if new_id != r["source_row_id"] or new_keeper != keeper:
                stats["remapped"] += 1
            r["source_row_id"] = new_id
            if keeper:
                r["keeper_source_row_id"] = new_keeper
            kept.append(r)
        with path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(kept)
        print(f"{path}: {len(kept)} corrections kept")

    print(f"ids remapped: {stats['remapped']} | obsolete dropped: {stats['stale_dropped']}")
    for name, rid, extra in dropped:
        print(f"  dropped {name}: {rid} ({extra})")


if __name__ == "__main__":
    sys.exit(main())
