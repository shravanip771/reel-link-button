"""Pure trust + ranking logic. No Flask or database imports, so it is easy to test.

Every threshold below is an illustrative default for this simulation, not a tuned value.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from statistics import median

# --- Which taps count -------------------------------------------------------
# A tap only counts toward the public number and the ranking signal when the
# tapping account looks established and active.
MIN_ACCOUNT_AGE_DAYS = 30
MIN_ACTIVITY = 0.15

# --- Who can use the button -------------------------------------------------
MIN_CREATOR_AGE_DAYS = 90

# --- Burst control ----------------------------------------------------------
# Trusted-looking accounts can still be hijacked and used in a burst. In any
# 10-minute bucket we keep at most max(BURST_FLOOR, BURST_MULT * median bucket).
BURST_BUCKET_MIN = 10
BURST_FLOOR = 15
BURST_MULT = 4

# --- Quality signals --------------------------------------------------------
SMOOTH_K = 20          # pseudo-observations pulling small samples toward PRIOR
PRIOR = 0.5
RETENTION_DAYS = 7

# --- Auto-pause -------------------------------------------------------------
PAUSE_MIN_REPORTS = 5
PAUSE_MIN_RATE = 0.03  # reports / DMs delivered

# --- Ranking weights --------------------------------------------------------
TAP_WEIGHT = 2.0              # a tap is a stronger intent signal than a like
REPORT_PENALTY_SLOPE = 10.0   # 10% report rate wipes out the tap contribution

_EPOCH = datetime(1970, 1, 1)


def is_trusted_account(age_days: int, activity: float) -> bool:
    return age_days >= MIN_ACCOUNT_AGE_DAYS and activity >= MIN_ACTIVITY


def creator_eligible(age_days: int, in_good_standing: bool) -> bool:
    return bool(in_good_standing) and age_days >= MIN_CREATOR_AGE_DAYS


def _bucket(ts: datetime) -> int:
    return int((ts - _EPOCH).total_seconds() // (BURST_BUCKET_MIN * 60))


def filter_taps(taps: list[tuple[datetime, int, float]]) -> list[bool]:
    """Decide which taps count.

    taps: (timestamp, account_age_days, activity_score) per tap.
    Returns a list of booleans aligned with the input (True = counts).
    Step 1 drops untrusted accounts. Step 2 caps each 10-minute bucket so a
    sudden burst cannot inflate the number, even from accounts that pass step 1.
    """
    kept = [False] * len(taps)
    buckets: dict[int, list[int]] = defaultdict(list)
    for i, (ts, age, activity) in enumerate(taps):
        if is_trusted_account(age, activity):
            buckets[_bucket(ts)].append(i)
    if not buckets:
        return kept
    cap = int(max(BURST_FLOOR, BURST_MULT * median(len(v) for v in buckets.values())))
    for idxs in buckets.values():
        idxs.sort(key=lambda i: taps[i][0])
        for i in idxs[:cap]:
            kept[i] = True
    return kept


def smoothed(successes: int, total: int, prior: float = PRIOR, k: int = SMOOTH_K) -> float:
    """Shrink a rate toward `prior` so a handful of observations cannot swing it."""
    return (successes + prior * k) / (total + k)


def format_count(n: int) -> str:
    """Public display: rounded down so tiny manipulations are invisible."""
    if n < 100:
        return str(n)
    if n < 1000:
        return f"{n // 10 * 10}+"
    if n < 1_000_000:
        whole, frac = divmod(n // 100, 10)
        return f"{whole}.{frac}K+" if frac else f"{whole}K+"
    whole, frac = divmod(n // 100_000, 10)
    return f"{whole}.{frac}M+" if frac else f"{whole}M+"


def should_pause(reports: int, dms: int) -> bool:
    return dms > 0 and reports >= PAUSE_MIN_REPORTS and reports / dms >= PAUSE_MIN_RATE


def score_reel(
    *,
    views: int,
    likes: int,
    raw_taps: int,
    valid_taps: int,
    dms: int,
    clicks: int,
    reports: int,
    mature_follows: int,
    retained: int,
    follow_gate: bool,
    paused: bool,
) -> dict:
    """Return the rate metrics and the two scores shown on the dashboard.

    naive_score: what a ranker would see if it simply counted raw taps.
    trust_score: taps are filtered, weighted by quality, and cut by reports.
    """
    views = max(views, 1)
    like_rate = likes / views
    raw_rate = raw_taps / views
    valid_rate = valid_taps / views

    click_rate = smoothed(clicks, dms)
    retention = smoothed(retained, mature_follows) if follow_gate else None
    quality = 0.5 * click_rate + 0.5 * retention if retention is not None else click_rate

    report_rate = reports / dms if dms else 0.0
    report_penalty = max(0.0, 1.0 - REPORT_PENALTY_SLOPE * report_rate)
    quality_mult = quality / PRIOR  # 1.0 at a neutral 0.5

    naive_score = like_rate + TAP_WEIGHT * raw_rate
    tap_part = 0.0 if paused else TAP_WEIGHT * valid_rate * quality_mult * report_penalty
    trust_score = like_rate + tap_part

    return {
        "like_rate": like_rate,
        "raw_tap_rate": raw_rate,
        "valid_tap_rate": valid_rate,
        "click_rate": click_rate,
        "retention": retention,
        "quality": quality,
        "report_rate": report_rate,
        "report_penalty": report_penalty,
        "naive_score": naive_score,
        "trust_score": trust_score,
    }
