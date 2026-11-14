"""Transfer events, freshman-cohort retention and linker validation."""
from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd

from .link import YEAR_RANK, hs_match, season_index, stints_for
from .load import REFERENCE, seasons

YEAR_LABEL = {v: k for k, v in YEAR_RANK.items() if k != "Fifth Year"}


def _stints(r: pd.DataFrame, links: pd.DataFrame) -> pd.DataFrame:
    st = stints_for(r, links)
    sus = links.groupby("stint_id").suspect_tenure.agg(
        lambda s: s.astype(str).eq("True").any())
    st["suspect_tenure"] = st.index.map(sus).fillna(False)
    return st


def transfer_events(r: pd.DataFrame, links: pd.DataFrame,
                    pairs: pd.DataFrame) -> pd.DataFrame:
    """One row per accepted cross-team link, plus 'reported' rows for
    players whose roster names a previous college that the linker could not
    match to an NCAA stint in the data (junior colleges, NAIA, pre-2015)."""
    s = season_index()
    inv = {v: k for k, v in s.items()}
    st = _stints(r, links)
    linked = pairs.merge(st.add_prefix("from_"), left_on="from_stint",
                         right_index=True).merge(
        st.add_prefix("to_"), left_on="to_stint", right_index=True)
    linked = linked[~linked.from_suspect_tenure & ~linked.to_suspect_tenure]
    ev = pd.DataFrame({
        "person_id": linked.from_person_id,
        "kind": "linked",
        "tier": linked.tier,
        "evidence": linked.evidence,
        "from_ncaa_id": linked.from_ncaa_id,
        "from_team": linked.from_team,
        "from_division": linked.from_division,
        "from_last_season": linked.from_last_si.map(inv),
        "from_last_class": linked.from_last_year.map(YEAR_LABEL),
        "from_seasons": linked.from_n_seasons,
        "to_ncaa_id": linked.to_ncaa_id,
        "to_team": linked.to_team,
        "to_division": linked.to_division,
        "to_first_season": linked.to_first_si.map(inv),
        "to_first_class": linked.to_first_year.map(YEAR_LABEL),
        "seasons_between": linked.to_first_si - linked.from_last_si - 1,
        "home_state": linked.from_home_state.where(linked.from_home_state != "",
                                                   linked.to_home_state),
        "previous_school_reported": "",
    })

    # previous school named on the roster but no linked NCAA predecessor
    m = r.merge(links[["source_row_id", "stint_id", "person_id"]],
                on="source_row_id")
    first = (m.assign(si=m.season.map(s)).sort_values("si")
             .drop_duplicates("stint_id"))
    rep = first[first.previous_school_type.isin(["College", "Junior College"])
                | (first.previous_school_ncaa_id != "")]
    rep = rep[~rep.stint_id.isin(set(pairs.to_stint))]
    rep = rep[~rep.stint_id.map(st.suspect_tenure).fillna(False)]
    prev_ncaa = rep.previous_school_ncaa_id
    rep_ev = pd.DataFrame({
        "person_id": rep.person_id,
        "kind": np.where(rep.previous_school_type == "Junior College",
                         "reported_juco", "reported_college"),
        "tier": "reported",
        "evidence": "previous_school",
        "from_ncaa_id": prev_ncaa,
        "from_team": "",
        "from_division": "",
        "from_last_season": "",
        "from_last_class": "",
        "from_seasons": np.nan,
        "to_ncaa_id": rep.ncaa_id,
        "to_team": rep.team,
        "to_division": rep.division,
        "to_first_season": rep.season,
        "to_first_class": rep.year_clean,
        "seasons_between": np.nan,
        "home_state": rep.state,
        "previous_school_reported": rep.previous_school_clean,
    })
    out = pd.concat([ev, rep_ev], ignore_index=True)
    out["direction"] = np.where(
        (out.kind == "linked") & (out.from_division != "") & (out.to_division != ""),
        out.from_division + "->" + out.to_division, "")
    return out.sort_values(["to_first_season", "to_ncaa_id", "person_id",
                            "kind"]).reset_index(drop=True)


def excluded_team_seasons() -> set[tuple[str, str]]:
    out = set()
    for f in ("source_exclusions.csv", "coverage_exceptions.csv"):
        t = pd.read_csv(REFERENCE / f, dtype=str, keep_default_na=False)
        out |= set(zip(t.ncaa_id, t.season))
    return out


def cohorts(r: pd.DataFrame, links: pd.DataFrame,
            transfers: pd.DataFrame) -> pd.DataFrame:
    """Freshman cohorts by program and entering season.

    A freshman is a stint whose first row is class Freshman. Tenure counts
    observed seasons on that roster; seasons the team itself is missing from
    the data (exclusions, COVID-cancelled scrapes) neither end nor extend a
    tenure. Outcome when the stint ends:
      still_on_roster   stint reaches the last season in the data (censored)
      transferred       an accepted link to a later NCAA stint
      completed         4+ seasons on the roster
      left              none of the above (quit, medical, NAIA/abroad, or an
                        unlinked transfer)
    """
    s = season_index()
    last = len(seasons()) - 1
    st = _stints(r, links)
    st = st[~st.suspect_tenure & (st.first_year == 1)]
    gone_to = set(transfers.loc[transfers.kind == "linked", "person_id"])
    linked_from = transfers[transfers.kind == "linked"].groupby(
        ["person_id", "from_ncaa_id"]).size()
    team_obs: dict[str, set] = defaultdict(set)
    for t, ss in r[["ncaa_id", "season"]].drop_duplicates().itertuples(index=False):
        team_obs[t].add(s[ss])
    excluded = excluded_team_seasons()

    rows = []
    inv = {v: k for k, v in s.items()}
    for sid, x in st.iterrows():
        obs_after = [i for i in team_obs[x.ncaa_id] if i > x.last_si]
        censored = x.last_si == last or not obs_after
        transferred = (x.person_id in gone_to
                       and (x.person_id, x.ncaa_id) in linked_from.index)
        if transferred:
            outcome = "transferred"
        elif censored:
            outcome = "still_on_roster"
        elif x.n_seasons >= 4:
            outcome = "completed"
        else:
            outcome = "left"
        rows.append((inv[x.first_si], x.ncaa_id, x.team, x.division,
                     x.person_id, int(x.n_seasons), outcome,
                     int(last - x.first_si + 1)))
    per = pd.DataFrame(rows, columns=["cohort", "ncaa_id", "team", "division",
                                      "person_id", "seasons_on_roster",
                                      "outcome", "seasons_observable"])
    per = per[~per.apply(lambda q: (q.ncaa_id, q.cohort) in excluded, axis=1)]

    def agg(g: pd.DataFrame) -> pd.Series:
        n = len(g)
        obs = int(g.seasons_observable.iloc[0])
        return pd.Series({
            "freshmen": n,
            "seasons_observable": obs,
            "retained_2nd_season": round((g.seasons_on_roster >= 2).mean(), 4) if obs >= 2 else np.nan,
            "retained_4th_season": round((g.seasons_on_roster >= 4).mean(), 4) if obs >= 4 else np.nan,
            "transferred": int((g.outcome == "transferred").sum()),
            "left": int((g.outcome == "left").sum()),
            "completed": int((g.outcome == "completed").sum()),
            "still_on_roster": int((g.outcome == "still_on_roster").sum()),
        })

    out = (per.groupby(["cohort", "ncaa_id", "team", "division"], sort=True)
           .apply(agg, include_groups=False).reset_index())
    for c in ("freshmen", "seasons_observable", "transferred", "left",
              "completed", "still_on_roster"):
        out[c] = out[c].astype(int)
    return out


def validation(r: pd.DataFrame, links: pd.DataFrame,
               transfers: pd.DataFrame) -> pd.DataFrame:
    """Recall against the labelled set (rows whose roster names an NCAA
    previous school that is in the data), and a high-school agreement proxy
    for precision among hometown-only (probable) links."""
    s = season_index()
    m = r.merge(links[["source_row_id", "stint_id", "person_id"]],
                on="source_row_id")
    first = (m.assign(si=m.season.map(s)).sort_values("si")
             .drop_duplicates("stint_id"))
    lab = first[first.previous_school_ncaa_id != ""]
    lab = lab[lab.previous_school_ncaa_id.isin(set(r.ncaa_id))]
    lab = lab[lab.season != seasons()[0]]   # predecessor can't be observed
    lt = transfers[transfers.kind == "linked"]
    found = set(zip(lt.person_id, lt.from_ncaa_id, lt.to_ncaa_id))
    lab = lab.assign(recovered=[(p, f, t) in found for p, f, t in
                                zip(lab.person_id, lab.previous_school_ncaa_id,
                                    lab.ncaa_id)])
    # was the predecessor present under the same name at all?
    names = set(zip(r.ncaa_id, r.name_key))
    lab = lab.assign(name_present=[(f, k) in names for f, k in
                                   zip(lab.previous_school_ncaa_id, lab.name_key)])
    rows = [
        ("labelled_transfers", len(lab), ""),
        ("recovered", int(lab.recovered.sum()),
         f"{lab.recovered.mean():.3f}" if len(lab) else ""),
        ("labelled_with_same_name_at_previous_team", int(lab.name_present.sum()), ""),
        ("recovered_given_same_name", int((lab.recovered & lab.name_present).sum()),
         f"{lab[lab.name_present].recovered.mean():.3f}" if lab.name_present.any() else ""),
    ]
    prob = lt[lt.tier == "probable"]
    st = stints_for(r, links)
    p = prob.merge(st[["person_id", "ncaa_id", "hs"]].rename(
        columns={"hs": "hs_from"}), left_on=["person_id", "from_ncaa_id"],
        right_on=["person_id", "ncaa_id"]).merge(
        st[["person_id", "ncaa_id", "hs"]].rename(columns={"hs": "hs_to"}),
        left_on=["person_id", "to_ncaa_id"], right_on=["person_id", "ncaa_id"])
    both = p[(p.hs_from != "") & (p.hs_to != "")]
    rows.append(("probable_links_with_hs_on_both", len(both), ""))
    # by construction a probable link has hometown XOR high-school
    # agreement, so this counts hometown-based probables whose schools differ
    agree = [hs_match(a, b) for a, b in zip(both.hs_from, both.hs_to)]
    rows.append(("probable_hs_agree", int(sum(agree)),
                 f"{np.mean(agree):.3f}" if len(both) else ""))
    return pd.DataFrame(rows, columns=["metric", "count", "rate"])
