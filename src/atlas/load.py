"""Loading helpers and paths shared by the atlas steps."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CLEAN = ROOT / "data" / "clean"
REFERENCE = ROOT / "data" / "reference"
GEO = REFERENCE / "geo"
RAW_GEO = GEO / "raw"
AUDITS = ROOT / "data" / "derived" / "audits"
OUT = ROOT / "data" / "derived" / "atlas"

DIVISIONS = ("I", "II", "III")

ROSTER_COLS = [
    "source_row_id", "season", "ncaa_id", "team", "player_id", "name",
    "name_key", "profile_url", "total_inches", "primary_position",
    "year_clean", "redshirt", "high_school_clean", "previous_school_clean",
    "previous_school_type", "previous_school_ncaa_id", "hometown_clean",
    "hometown_status", "state", "region", "country", "team_state", "division",
]


def seasons() -> list[str]:
    return sorted(p.stem.removeprefix("vb_rosters_")
                  for p in CLEAN.glob("vb_rosters_*.csv"))


def load_rosters(cols: list[str] | None = None) -> pd.DataFrame:
    """All published clean rows, every season, as strings ("" for missing)."""
    cols = cols or ROSTER_COLS
    frames = [
        pd.read_csv(CLEAN / f"vb_rosters_{s}.csv", dtype=str,
                    keep_default_na=False, usecols=cols)
        for s in seasons()
    ]
    df = pd.concat(frames, ignore_index=True)
    return df.sort_values("source_row_id", kind="stable").reset_index(drop=True)


def read_out(name: str) -> pd.DataFrame:
    """Read a previously written atlas table back with string ids."""
    return pd.read_csv(OUT / name, dtype={"ncaa_id": str, "source_row_id": str,
                       "person_id": str, "from_ncaa_id": str, "to_ncaa_id": str,
                       "stint_id": str, "from_stint": str, "to_stint": str},
                       keep_default_na=False, na_values=[""])


def write_csv(df: pd.DataFrame, path: Path) -> None:
    """Deterministic CSV output: fixed column order, LF endings, no index."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, lineterminator="\n", float_format="%.6g")
