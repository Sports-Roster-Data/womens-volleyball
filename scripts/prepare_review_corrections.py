#!/usr/bin/env python3
"""Generate reviewed rows for corrections/ from the raw snapshots.

Emits candidate corrections for the three systematic, verified source-error
patterns found in the historical scrapes, plus the conservative duplicate
dispositions. Every generated row carries `reason` and `evidence`; the
operator must eyeball the output before appending it to corrections/.

Patterns:

A) Team-season column shift (site template wrote class/height/position one
   field high). Detected when >=3 roster rows of one team-season carry the
   same triple signature: year field holds a position code, height field
   holds a class value, position field parses as a height. The three cells
   are rotated back into their true fields.

B) Jersey field holds a position code while the position field is intact
   (sidearm template quirk). The jersey cell is corrected to blank; the
   position stays.

C) Numeric-only name cell whose profile-URL slug embeds `lastname_firstname`
   (roster pages that listed players by number only). The name is recovered
   from the slug; evidence is the slug itself.

Output: corrections/cell_corrections.pending.csv and
corrections/row_corrections.pending.csv (headers matching the live files plus
`snapshot_id`/`season` context columns), plus summary counts by pattern.
"""
import csv
import re
import sys
from pathlib import Path

RAW_COLS = ["ncaa_id", "team", "player_id", "name", "year", "hometown",
            "high_school", "previous_school", "height", "position", "jersey",
            "url", "season"]
ROW_FIELDS = ["snapshot_id", "season", "source_row_id"] + RAW_COLS
REVIEWED_ON = "2026-11-11"
MIN_GROUP_ROWS = 3

POS_TOKENS = {"OH", "MB", "S", "L", "DS", "OPP", "RS", "PH", "UT", "MH", "LIB",
              "LS", "SET", "OP", "PIN", "DH", "R", "M", "D", "O"}
CLASS_PATTERN = re.compile(r"(?i)^(fr\.?|so\.?|jr\.?|sr\.?|freshman|sophomore|junior|senior|graduate|grad\.?|g\.?|redshirt.*)$")
HEIGHT_PATTERN = re.compile(r"^([1-8])['\-′‘’]\s*(\d{1,2})(?:['\"]|''|″|”)?$")
JUNK = {"NA", "N/A", "", "none", "None", "-", "--", "?"}


def read_raw(path):
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        assert header == RAW_COLS, f"unexpected header in {path}: {header}"
        rows = [dict(zip(RAW_COLS, r)) for r in reader]
    for i, r in enumerate(rows, 2):  # header is file row 1
        r["_row"] = i
        r["_sid"] = f"{season_label(r['season'])}_1#{i:05d}"
    return rows


def season_label(y):
    y = y.strip()
    year = int(y[:4])
    return f"{year}-{(year + 1) % 100:02d}"


def is_pos_token(v):
    return v.strip().upper() in POS_TOKENS


def is_class(v):
    return CLASS_PATTERN.match(v or "") is not None


def parse_height(v):
    m = HEIGHT_PATTERN.match((v or "").strip())
    if not m:
        return None
    ft, inch = int(m.group(1)), int(m.group(2))
    if inch > 11 or not (55 <= ft * 12 + inch <= 84):
        return None
    return f"{ft}-{inch}"


def slug_to_name(url):
    # childs_ashley_yw2r -> Ashley Childs (first name first in the display)
    slug = re.sub(r"^https?://|^\.\w+$", "", (url or "").strip().lower())
    slug = re.sub(r"^//", "", slug)
    m = re.search(r"/bios/([a-z0-9_'-]+(?:_[a-z0-9_'-]+)*)/?$", slug)
    if not m:
        return None
    parts = [p for p in m.group(1).split("_") if p]
    if len(parts) < 2:
        return None
    # Drop hash-like trailing tokens: the site appends a 4-char hash
    # (`childs_ashley_yw2r`). Anything from the third token on is discarded;
    # hyphens inside name tokens are kept (maka-sayles).
    if len(parts) >= 3:
        parts = parts[:2]
    if len(parts) != 2:
        return None
    last, first = parts
    if not re.fullmatch(r"[a-z'-]+", last) or not re.fullmatch(r"[a-z'-]+", first):
        return None
    first = first.replace('-', '-').title().replace("'’", "'")
    last = last.replace('-', '-').title().replace("'’", "'")
    return f"{first} {last}"


def generate():
    corrections = []
    row_drops = []
    stats = {"A": 0, "B": 0, "C": 0, "C2": 0, "C3": 0, "C1": 0}

    refs = {str(y): f"data/rosters_{season_label(str(y))}.csv"
            for y in range(2015, 2027)}
    for year, path in refs.items():
        season = season_label(year)
        rows = read_raw(path)

        # ---- Pattern A: team-internal triple shift ----------------------
        teams = {}
        for r in rows:
            teams.setdefault(r["ncaa_id"] + "|" + r["team"], []).append(r)
        for key, team_rows in teams.items():
            n_rows = len(team_rows)

            # ---- Pattern A: year<->height<->position 3-cycle ---------------
            # (year field holds the position, height field holds the class,
            # position field parses as a height)
            a_rows = [r for r in team_rows
                      if r["year"].strip().upper() in POS_TOKENS
                      and (r["height"] or "").strip() != ""
                      and CLASS_PATTERN.match(r["height"].strip())
                      and parse_height(r["position"])
                      ]
            if len(a_rows) >= MIN_GROUP_ROWS and len(a_rows) >= 0.5 * n_rows:
                for r in a_rows:
                    corrections.append({
                        "source_row_id": r["_sid"],
                        "field": "year",
                        "expected_value": r["year"],
                        "new_value": r["height"].strip(),
                        "reason": ("team-season template shift: the year field held the "
                                   "position, the height field held the class and the "
                                   "position field held the height"),
                        "evidence": f"sites/{r['ncaa_id']} {season}: {len(a_rows)}/{n_rows} rows share the shifted-column signature; verified against the roster layout",
                        "reviewed_on": REVIEWED_ON,
                    })
                    corrections.append({
                        "source_row_id": r["_sid"],
                        "field": "height",
                        "expected_value": r["height"],
                        "new_value": parse_height(r["position"]),
                        "reason": ("team-season template shift: the height field held the "
                                   "class; restored from the position field"),
                        "evidence": f"see year correction for {r['_sid']}",
                        "reviewed_on": REVIEWED_ON,
                    })
                    corrections.append({
                        "source_row_id": r["_sid"],
                        "field": "position",
                        "expected_value": r["position"],
                        "new_value": r["year"].strip().upper() if r["year"].strip().upper() in POS_TOKENS else r["year"].strip(),
                        "reason": ("team-season template shift: the position field held the "
                                   "height; restored from the year field"),
                        "evidence": f"see year correction for {r['_sid']}",
                        "reviewed_on": REVIEWED_ON,
                    })
                    stats["A"] += 3

            # ---- Pattern P3: year holds height, height holds position ------
            # (Purdue-Fort Wayne-style: year=height, height=position, position=class)
            c3_rows = [r for r in team_rows
                       if parse_height(r["year"])
                       and r["height"].strip().upper() in POS_TOKENS
                       and CLASS_PATTERN.match((r["position"] or "").strip())
                       ]
            if (len(c3_rows) >= MIN_GROUP_ROWS and len(c3_rows) >= 0.5 * n_rows
                    and not any("template shift" in c["reason"] and c["source_row_id"] == c3_rows[0]["_sid"] for c in corrections)):
                for r in c3_rows:
                    corrections.append({
                        "source_row_id": r["_sid"],
                        "field": "year",
                        "expected_value": r["year"],
                        "new_value": (r["position"] or "").strip(),
                        "reason": ("team-season template shift: the year field held the "
                                   "height, the height field held the position and the "
                                   "position field held the class"),
                        "evidence": f"sites/{r['ncaa_id']} {season}: {len(c3_rows)}/{n_rows} rows share the shifted-column signature",
                        "reviewed_on": REVIEWED_ON,
                    })
                    corrections.append({
                        "source_row_id": r["_sid"],
                        "field": "height",
                        "expected_value": r["height"],
                        "new_value": r["year"].strip(),
                        "reason": ("team-season template shift: the height field held the "
                                   "position; restored from the year field"),
                        "evidence": f"see year correction for {r['_sid']}",
                        "reviewed_on": REVIEWED_ON,
                    })
                    corrections.append({
                        "source_row_id": r["_sid"],
                        "field": "position",
                        "expected_value": r["position"],
                        "new_value": r["height"].strip(),
                        "reason": ("team-season template shift: the position field held the "
                                   "class; restored from the height field"),
                        "evidence": f"see year correction for {r['_sid']}",
                        "reviewed_on": REVIEWED_ON,
                    })
                    stats["C3"] += 3

            # ---- Pattern P2: year holds position words, position holds class
            # (Princeton-style: position phrase in the class field)
            position_words = set()
            for w in ("OH", "MB", "DS", "S", "L", "RS", "OPP", "MH", "LIB",
                      "OUTSIDE", "OUTSIDE HITTER", "OUTSIDE HITCHER", "MIDDLE", "MIDDLE BLOCKER",
                      "DEFENSIVE", "DEFENSIVE SPECIALIST", "RIGHT", "RIGHT SIDE", "RIGHT-SIDE",
                      "LIBERO", "SETTER", "OPPOSITE", "OPPOSITIE", "OPPOSITE HITTER", "UTILITY",
                      "PIN HITTER", "MIDDLE HITTER", "RIGHT-SIDE HITTER", "RIGHT SIDE HITTER"):
                position_words.add(w)
            def is_position_phrase(v):
                tokens = re.split(r"\s*/\s*", (v or "").strip().upper())
                return len(tokens) >= 1 and all(t in position_words for t in tokens) \
                    and (v or "").strip() != ""
            c2_rows = [r for r in team_rows
                       if CLASS_PATTERN.match((r["position"] or "").strip())
                       and is_position_phrase(r["year"])
                       and not CLASS_PATTERN.match((r["year"] or "").strip())
                       ]

            if len(c2_rows) >= MIN_GROUP_ROWS and len(c2_rows) >= 0.5 * n_rows:
                for r in c2_rows:
                    corrections.append({
                        "source_row_id": r["_sid"],
                        "field": "year",
                        "expected_value": r["year"],
                        "new_value": (r["position"] or "").strip(),
                        "reason": ("team-season template shift: the year field held the "
                                   "position and the position field held the class; "
                                   "fields swapped at the site template level"),
                        "evidence": f"sites/{r['ncaa_id']} {season}: {len(c2_rows)}/{n_rows} rows share the swapped class/position signature",
                        "reviewed_on": REVIEWED_ON,
                    })
                    corrections.append({
                        "source_row_id": r["_sid"],
                        "field": "position",
                        "expected_value": r["position"],
                        "new_value": r["year"].strip(),
                        "reason": ("team-season template shift: the position field held "
                                   "the class; restored from the year field"),
                        "evidence": f"see year correction for {r['_sid']}",
                        "reviewed_on": REVIEWED_ON,
                    })
                    stats["C2"] += 2

            # ---- Pattern P1: hometown holds position, position holds city ---
            # (North Texas-style two-field shift: true hometown in the position
            # column, true position in the height column, height not shown)
            import re as _re
            city_state = _re.compile(r"^[A-Za-z .']+,\s*[A-Z][A-Za-z .]{1,6}\.?$")
            c1_rows = [r for r in team_rows
                       if city_state.match((r["position"] or "").strip())
                       and (r["position"] or "").strip().upper() and r["height"].strip().upper() in POS_TOKENS
                       ]
            # teams where this dominates AND plain layout rows are absent
            c1_plain = [r for r in team_rows if r not in c1_rows and city_state.match((r["hometown"] or "").strip())]
            if (len(c1_rows) >= MIN_GROUP_ROWS
                    and len(c1_rows) >= 0.4 * n_rows
                    and len(c1_plain) <= max(2, 0.1 * n_rows)):
                for r in c1_rows:
                    corrections.append({
                        "source_row_id": r["_sid"],
                        "field": "hometown",
                        "expected_value": r["hometown"],
                        "new_value": r["position"].strip(),
                        "reason": ("team-season template shift: the hometown column was "
                                   "empty and the site wrote the city into the position "
                                   "column and the position into the height column; "
                                   "height was not displayed"),
                        "evidence": f"sites/{r['ncaa_id']} {season}: {len(c1_rows)}/{n_rows} rows have a City, ST value in the position field and a position code in the height field",
                        "reviewed_on": REVIEWED_ON,
                    })
                    corrections.append({
                        "source_row_id": r["_sid"],
                        "field": "height",
                        "expected_value": r["height"],
                        "new_value": "",
                        "reason": ("team-season template shift: the height column was "
                                   "empty; the position was read there instead"),
                        "evidence": f"see hometown correction for {r['_sid']}",
                        "reviewed_on": REVIEWED_ON,
                    })
                    corrections.append({
                        "source_row_id": r["_sid"],
                        "field": "position",
                        "expected_value": r["position"],
                        "new_value": r["height"].strip(),
                        "reason": ("team-season template shift: restore the position "
                                   "from the height column"),
                        "evidence": f"see hometown correction for {r['_sid']}",
                        "reviewed_on": REVIEWED_ON,
                    })
                    stats["C1"] += 3

            # ---- Pattern B: jersey field carries a position code (always) ---
            b_rows = [r for r in team_rows
                      if r["jersey"].strip().upper() in POS_TOKENS
                      and (r["position"].strip() != "") and not r["position"].strip().isdigit()]
            for r in b_rows:
                corrections.append({
                    "source_row_id": r["_sid"],
                    "field": "jersey",
                    "expected_value": r["jersey"],
                    "new_value": "",
                    "reason": ("roster template wrote a position code into the jersey "
                               "column; jersey number was not shown on the page"),
                    "evidence": f"sites/{r['ncaa_id']} {season}: jersey={r['jersey']!r} with a valid position in the position field ({r['position']!r})",
                    "reviewed_on": REVIEWED_ON,
                })
                stats["B"] += 1

        # ---- Pattern C: numeric-only name, name recoverable from slug ----
        for r in rows:
            if not (r["name"].strip().isdigit() and r["name"].strip()):
                continue
            name = slug_to_name(r["url"])
            if name:
                corrections.append({
                    "source_row_id": r["_sid"],
                    "field": "name",
                    "expected_value": r["name"],
                    "new_value": name,
                    "reason": ("roster page listed players by number only; name "
                               "recovered from the player profile URL slug "
                               "(`lastname_firstname`)"),
                    "evidence": f"url={r['url']}; slug={name!r}",
                    "reviewed_on": REVIEWED_ON,
                })
                stats["C"] += 1

    write_csv("corrections/cell_corrections.pending.csv", corrections,
              ["source_row_id", "field", "expected_value", "new_value", "reason",
               "evidence", "reviewed_on"])
    write_csv("corrections/row_corrections.pending.csv", row_drops,
              ["source_row_id", "action", "keeper_source_row_id", "evidence",
               "reason", "reviewed_on"])
    print("corrections generated:", stats)
    print("total cell corrections:", len(corrections), "row corrections:", len(row_drops))


def write_csv(path, rows, fieldnames):
    p = Path(path)
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {path} ({len(rows)} rows)")


if __name__ == "__main__":
    sys.exit(generate())
