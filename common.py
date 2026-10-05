"""
common.py — shared data-loading and derivation logic for the onboarding funnel analysis.

Used by all three notebooks (A1, A2, A3/A4). One source of truth for the cohort cutoff,
the loss classification, the monthly-volume assumption, and the reconciliation check, so a
fix here only has to happen once instead of drifting between copies -- this project hit
that exact drift more than once already.

DESIGN NOTES:

1. DATA_DIR resolution: narrow, two-location search only (next to `start` or this file,
   or a 'data' subfolder there). Does not walk further up the tree -- an earlier draft did,
   and silently found an unrelated folder with same-named CSVs several levels up. A narrow
   search that fails loudly beats a wide one that can succeed silently on the wrong files.

2. load_captains() adds cohort_mature, signup_month, and required_docs_n as columns AT
   LOAD TIME, so every notebook that calls it gets a fully-derived table immediately.

3. classify_doc_loss_reason() returns master_mature with loss_bucket merged in as a
   COLUMN (not a bare Series), which is what makes groupby(["city", "loss_bucket"]) etc.
   possible for segmentation. The reconciliation assert lives inside it, so it can never
   be skipped by whoever calls it.

4. monthly_signup_volume() returns BOTH a historical average and the current run-rate,
   rather than picking one silently. Signup counts aren't right-censored the way outcomes
   are (a captain who signs up is recorded immediately, regardless of how their onboarding
   later turns out) -- so the most recent calendar month's raw count is a fair, real number,
   unlike approval/rejection rates, which genuinely do need the mature-cohort censoring
   rule. An earlier draft used only the historical average for every "extra approvals/month"
   projection; since signups grew ~29% over the data window, that silently understated
   every impact number by ~16-19%. Both notebooks that project monthly impact (A2, A4) call
   this function and report the current-run-rate basis as primary, historical as a stated
   comparison -- not a hidden choice.
"""

from pathlib import Path
import pandas as pd

SNAPSHOT_TS = pd.Timestamp("2026-06-30 23:59:00")
MATURITY_WINDOW = pd.Timedelta(days=30)   # stated assumption -- the in-progress backlog
                                           # empirically clears by 16 days post-signup
                                           # (proven live in A1, not just asserted here);
                                           # 30 is a conservative round buffer above that
MATURE_CUTOFF = SNAPSHOT_TS - MATURITY_WINDOW

REQUIRED_FILES = ["captains.csv", "doc_events.csv", "approvals.csv", "activation.csv", "nudges.csv"]

DOC_ORDER = ["DL", "RC", "AADHAAR", "PERMIT", "FITNESS", "INSURANCE"]
PERMIT_ELIGIBLE_VEHICLES = {"Auto", "Cab"}


def find_data_dir(required_files=None, start=None) -> Path:
    """
    Look ONLY in two places: `start` if given, or this file's own folder -- and a
    'data' subfolder in either case. Never walks further up the tree (see module
    docstring point 1).
    """
    required_files = required_files or REQUIRED_FILES
    here = Path(start).resolve() if start is not None else Path(__file__).resolve().parent
    candidates = [here, here / "data"]
    for c in candidates:
        if all((c / f).exists() for f in required_files):
            return c
    raise FileNotFoundError(
        f"Could not find {required_files}.\n"
        f"Checked: {[str(c) for c in candidates]}\n"
        f"Place the CSVs next to common.py (or wherever `start` points), "
        f"or in a 'data' subfolder there."
    )


def check_data_dir(data_dir: Path):
    """Fail loudly, with an exact list of what's missing and where it looked."""
    missing = [f for f in REQUIRED_FILES if not (data_dir / f).exists()]
    assert not missing, (
        f"Missing files: {missing}\n"
        f"Looked in: {data_dir.resolve()}\n"
        f"Place the CSVs next to common.py, or in a 'data' subfolder next to it."
    )


def required_docs_for(vehicle_type: str) -> list:
    docs = ["DL", "RC", "AADHAAR", "FITNESS", "INSURANCE"]
    if vehicle_type in PERMIT_ELIGIBLE_VEHICLES:
        docs.insert(3, "PERMIT")
    return docs


def load_captains(data_dir: Path) -> pd.DataFrame:
    captains = pd.read_csv(data_dir / "captains.csv", parse_dates=["signup_ts"])
    captains["cohort_mature"] = captains["signup_ts"] < MATURE_CUTOFF
    captains["signup_month"] = captains["signup_ts"].dt.to_period("M").astype(str)
    captains["required_docs_n"] = captains["vehicle_type"].apply(lambda v: len(required_docs_for(v)))
    return captains


def load_all(data_dir: Path):
    check_data_dir(data_dir)
    captains = load_captains(data_dir)
    doc_events = pd.read_csv(data_dir / "doc_events.csv", parse_dates=["event_ts"])
    approvals = pd.read_csv(data_dir / "approvals.csv", parse_dates=["decision_ts"])
    activation = pd.read_csv(data_dir / "activation.csv", parse_dates=["first_order_ts"])
    nudges = pd.read_csv(data_dir / "nudges.csv", parse_dates=["sent_ts"])

    # pandas does NOT raise an error when parse_dates fails -- it silently leaves the
    # column as a plain string. A CSV that's been opened and re-saved in Excel can
    # truncate a full timestamp (e.g. "2026-01-01 20:30:06.2") down to just "30:06.2"
    # (minutes:seconds, date and hour silently dropped) -- pandas will accept that as
    # a string without complaint, and every downstream sort/compare on that column
    # becomes meaningless without throwing any error. Check every parsed timestamp
    # column's dtype immediately, and fail loudly here rather than producing a
    # silently-wrong number three functions later.
    ts_columns = [
        (captains, "signup_ts", "captains.csv"),
        (doc_events, "event_ts", "doc_events.csv"),
        (approvals, "decision_ts", "approvals.csv"),
        (activation, "first_order_ts", "activation.csv"),
        (nudges, "sent_ts", "nudges.csv"),
    ]
    for df, col, fname in ts_columns:
        if not pd.api.types.is_datetime64_any_dtype(df[col]):
            sample = df[col].dropna().iloc[0] if df[col].notna().any() else "(all null)"
            raise TypeError(
                f"{fname}'s '{col}' column failed to parse as a timestamp (dtype is "
                f"{df[col].dtype}, sample value: {sample!r}).\n"
                f"This usually means the CSV was opened and re-saved in a spreadsheet "
                f"program (e.g. Excel), which can silently truncate full timestamps "
                f"down to just minutes:seconds. Get a fresh, unmodified copy of this "
                f"file -- do not open timestamp-bearing CSVs in Excel."
            )

    return captains, doc_events, approvals, activation, nudges


def build_master(captains: pd.DataFrame, approvals: pd.DataFrame, activation: pd.DataFrame):
    master = captains.merge(approvals, on="captain_id", how="left", validate="one_to_one")
    master = master.merge(activation, on="captain_id", how="left", validate="one_to_one")
    master["ever_activated"] = master["first_order_ts"].notna()
    master_mature = master[master["cohort_mature"]].copy()
    master_recent = master[~master["cohort_mature"]].copy()
    return master, master_mature, master_recent


def monthly_signup_volume(captains: pd.DataFrame):
    """
    Returns (historical_avg, current_runrate).

    historical_avg: mean signups/month across every month EXCEPT the most recent one.
    current_runrate: the most recent calendar month's actual signup count.

    Signup counts are not subject to the same right-censoring concern as approval/
    rejection outcomes (a signup is recorded the moment it happens, regardless of how
    that captain's onboarding later resolves) -- so the most recent month's raw count
    is a fair, real number to use as "today's" volume, unlike an approval rate, which
    genuinely does need the mature-cohort cutoff. Two numbers are returned, not one,
    so the choice of which to use for a projection is visible and explicit, not buried.
    """
    all_monthly = captains.groupby("signup_month").size()
    historical_avg = all_monthly.iloc[:-1].mean()
    current_runrate = all_monthly.iloc[-1]
    return historical_avg, current_runrate


def build_funnel(master_mature: pd.DataFrame, doc_events: pd.DataFrame) -> pd.DataFrame:
    """
    Stage-by-stage funnel by actual document name (not positional index -- ERickshaw
    skips PERMIT, so position 4 isn't the same document across vehicle types).

    Includes the eligibility/background-check gate as its own visible stage between
    "all docs cleared" and "approved" -- every rejected captain in this data cleared
    100% of required docs first (proven live in A1, not just asserted here), so
    rejection is a distinct post-doc gate, not a document failure.

    Uses docs_cleared >= required_docs_n rather than == -- proven live in A1 that
    docs_cleared never actually exceeds required_docs_n in this data, so the two
    operators are equivalent here; >= is used defensively in case that invariant
    doesn't hold on a future data pull.
    """
    n_signups = len(master_mature)
    passed = doc_events[doc_events["event_type"] == "verification_pass"]
    rows = [{"stage": "1. Signed up", "count": n_signups, "pct_of_signups": 1.0, "pct_of_eligible": None}]
    step = 2
    for doc_type in DOC_ORDER:
        eligible = (
            master_mature[master_mature["vehicle_type"].isin(PERMIT_ELIGIBLE_VEHICLES)]
            if doc_type == "PERMIT" else master_mature
        )
        passed_ids = set(passed[passed["doc_type"] == doc_type]["captain_id"])
        cleared = eligible[eligible["captain_id"].isin(passed_ids)]
        rows.append({
            "stage": f"{step}. {doc_type} cleared" + (" (Auto/Cab only)" if doc_type == "PERMIT" else ""),
            "count": len(cleared),
            "pct_of_signups": len(cleared) / n_signups,
            "pct_of_eligible": len(cleared) / len(eligible),
        })
        step += 1

    docs_fully_cleared = master_mature[master_mature["docs_cleared"] >= master_mature["required_docs_n"]]
    rows.append({
        "stage": f"{step}. All required docs cleared",
        "count": len(docs_fully_cleared),
        "pct_of_signups": len(docs_fully_cleared) / n_signups,
        "pct_of_eligible": len(docs_fully_cleared) / n_signups,
    })
    step += 1

    passed_eligibility = docs_fully_cleared[docs_fully_cleared["final_status"] != "rejected"]
    rows.append({
        "stage": f"{step}. Passed eligibility/background check",
        "count": len(passed_eligibility),
        "pct_of_signups": len(passed_eligibility) / n_signups,
        "pct_of_eligible": len(passed_eligibility) / max(len(docs_fully_cleared), 1),
    })
    step += 1

    approved = master_mature[master_mature["final_status"] == "approved"]
    rows.append({
        "stage": f"{step}. Approved (A2O complete)",
        "count": len(approved),
        "pct_of_signups": len(approved) / n_signups,
        "pct_of_eligible": len(approved) / max(len(passed_eligibility), 1),
    })
    return pd.DataFrame(rows)


def classify_doc_loss_reason(master_mature: pd.DataFrame, doc_events: pd.DataFrame) -> pd.DataFrame:
    """
    Returns master_mature with a new 'loss_bucket' column added:
      - 'approved'            : final_status == approved
      - 'post_doc_rejection'   : cleared all docs, rejected at eligibility/background gate
      - 'exhausted_attempts'   : used all 3 tries on some doc, still failed
      - 'never_started'        : signed up, zero document activity at all
      - 'failed_gave_up'       : failed a doc at least once, never came back
      - 'stalled_no_failure'   : passed everything attempted so far, cleanly, then went silent

    doc_events passed in should already be scoped to the mature cohort by the caller,
    to stay consistent with how master_mature itself is scoped (avoids re-introducing
    the same right-censoring bug the mature/recent split exists to fix).
    """
    rejected_ids = set(master_mature.loc[master_mature["final_status"] == "rejected", "captain_id"])
    stalled_ids = set(master_mature.loc[
        master_mature["final_status"].isin(["dropped_in_docs", "in_progress"]), "captain_id"
    ])
    doc_s = doc_events[doc_events["captain_id"].isin(stalled_ids)]

    exhausted_ids = set(doc_s.loc[
        (doc_s["attempt_no"] == 3) & (doc_s["event_type"] == "verification_fail"), "captain_id"
    ])
    has_any_event = set(doc_s["captain_id"])
    has_any_fail = set(doc_s[doc_s["event_type"] == "verification_fail"]["captain_id"])
    never_started_ids = stalled_ids - has_any_event

    reasons = {cid: "post_doc_rejection" for cid in rejected_ids}
    for cid in stalled_ids:
        if cid in exhausted_ids:
            reasons[cid] = "exhausted_attempts"
        elif cid in never_started_ids:
            reasons[cid] = "never_started"
        elif cid in has_any_fail:
            reasons[cid] = "failed_gave_up"
        else:
            reasons[cid] = "stalled_no_failure"

    loss_series = pd.Series(reasons, name="loss_bucket")
    result = master_mature.merge(
        loss_series.rename("loss_bucket"), left_on="captain_id", right_index=True, how="left"
    )
    result["loss_bucket"] = result["loss_bucket"].fillna("approved")

    total_lost_actual = (result["loss_bucket"] != "approved").sum()
    total_lost_expected = len(master_mature) - (master_mature["final_status"] == "approved").sum()
    assert total_lost_actual == total_lost_expected, (
        f"loss_bucket total ({total_lost_actual}) does not reconcile with "
        f"n_signups - approved ({total_lost_expected}). Find the gap before trusting this table."
    )
    return result


def post_failure_outcome(doc_events: pd.DataFrame, doc_type: str) -> dict:
    """
    Per-document retry behaviour: of everyone who ever failed this doc, what fraction
    never came back vs. eventually passed? doc_events should be pre-scoped to the
    mature cohort by the caller, for the same censoring reason as classify_doc_loss_reason.
    """
    d = doc_events[doc_events["doc_type"] == doc_type].sort_values(["captain_id", "attempt_no", "event_ts"])
    failed_ever = set(d[d["event_type"] == "verification_fail"]["captain_id"])
    last_event = d.groupby("captain_id").last()
    never_retried_after_fail = last_event[
        (last_event.index.isin(failed_ever)) & (last_event["event_type"] == "verification_fail")
    ]
    eventually_passed = last_event[
        (last_event.index.isin(failed_ever)) & (last_event["event_type"] == "verification_pass")
    ]
    n_failed = len(failed_ever)
    return {
        "doc_type": doc_type,
        "captains_who_ever_failed": n_failed,
        "never_retried_after_fail": len(never_retried_after_fail),
        "never_retried_pct": len(never_retried_after_fail) / n_failed if n_failed else 0,
        "eventually_passed_pct": len(eventually_passed) / n_failed if n_failed else 0,
    }


def rc_fail_rate_by_device_tier(doc_events_mature: pd.DataFrame, master_mature: pd.DataFrame):
    """
    RC first-attempt fail rate by device tier, computed at the CAPTAIN level.

    attempt_no==1 has TWO raw event rows per captain (an upload_success plus a
    pass/fail outcome) -- grouping by captain_id first is required, or the rate
    silently comes out roughly half of the true value (this exact bug shipped in
    an earlier draft and survived one review round before being caught).

    Returns (fail_rate: Series indexed by device_tier, n_by_tier: Series, RC_ATTEMPT_RATE: float).
    """
    rc = doc_events_mature[doc_events_mature["doc_type"] == "RC"]
    rc_attempt1 = rc[rc["attempt_no"] == 1].merge(master_mature[["captain_id", "device_tier"]], on="captain_id")
    first_outcome = rc_attempt1.groupby(["captain_id", "device_tier"])["event_type"].apply(
        lambda s: "fail" if "verification_fail" in set(s) else ("pass" if "verification_pass" in set(s) else "other")
    ).reset_index(name="outcome")
    n_by_tier = first_outcome["device_tier"].value_counts()
    fail_count_by_tier = first_outcome[first_outcome["outcome"] == "fail"].groupby("device_tier").size()
    fail_rate = fail_count_by_tier / n_by_tier
    rc_attempt_rate = len(first_outcome) / len(master_mature)
    return fail_rate, n_by_tier, rc_attempt_rate
