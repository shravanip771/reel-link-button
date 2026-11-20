"""Reads events from SQLite and turns them into per-reel metrics."""
from __future__ import annotations

from datetime import datetime, timedelta

import ranking
from util import now_utc, parse

_REEL_SQL = """
SELECT r.*, u.handle, u.account_age_days AS creator_age
FROM reels r JOIN users u ON u.id = r.creator_id
"""


def _compute(db, reel, now: datetime) -> dict:
    rid = reel["id"]

    tap_rows = db.execute(
        """SELECT t.ts, u.account_age_days, u.activity_score, t.sim_fake
           FROM taps t JOIN users u ON u.id = t.user_id WHERE t.reel_id = ?""",
        (rid,),
    ).fetchall()
    kept = ranking.filter_taps(
        [(parse(r["ts"]), r["account_age_days"], r["activity_score"]) for r in tap_rows]
    )
    raw_taps = len(tap_rows)
    valid_taps = sum(kept)

    # Ground-truth labels exist only in the simulation; used to grade the filters.
    fake_total = sum(1 for r in tap_rows if r["sim_fake"])
    fake_kept = sum(1 for r, k in zip(tap_rows, kept) if r["sim_fake"] and k)
    organic_total = raw_taps - fake_total
    organic_dropped = sum(1 for r, k in zip(tap_rows, kept) if not r["sim_fake"] and not k)

    d = db.execute(
        "SELECT COUNT(*) n, COUNT(clicked_ts) c, COUNT(reported_ts) r FROM dms WHERE reel_id = ?",
        (rid,),
    ).fetchone()

    cutoff = now - timedelta(days=ranking.RETENTION_DAYS)
    mature = retained = 0
    for f in db.execute("SELECT ts, ended_ts FROM follows WHERE via_reel_id = ?", (rid,)):
        started = parse(f["ts"])
        if started > cutoff:
            continue  # too recent to judge 7-day retention
        mature += 1
        if f["ended_ts"] is None or parse(f["ended_ts"]) > started + timedelta(days=ranking.RETENTION_DAYS):
            retained += 1
    follows_total = db.execute(
        "SELECT COUNT(*) FROM follows WHERE via_reel_id = ?", (rid,)
    ).fetchone()[0]

    scores = ranking.score_reel(
        views=reel["views"],
        likes=reel["likes"],
        raw_taps=raw_taps,
        valid_taps=valid_taps,
        dms=d["n"],
        clicks=d["c"],
        reports=d["r"],
        mature_follows=mature,
        retained=retained,
        follow_gate=bool(reel["follow_gate"]),
        paused=bool(reel["paused"]),
    )

    out = dict(reel)
    out.update(scores)
    out.update(
        raw_taps=raw_taps,
        valid_taps=valid_taps,
        discounted=raw_taps - valid_taps,
        public_count=ranking.format_count(valid_taps),
        dms=d["n"],
        clicks=d["c"],
        reports=d["r"],
        follows_total=follows_total,
        mature_follows=mature,
        retained=retained,
        fake_total=fake_total,
        fake_kept=fake_kept,
        organic_total=organic_total,
        organic_dropped=organic_dropped,
    )
    return out


def compute_reel(db, reel_id: int, now: datetime | None = None) -> dict:
    row = db.execute(_REEL_SQL + " WHERE r.id = ?", (reel_id,)).fetchone()
    return _compute(db, row, now or now_utc())


def compute_all(db, now: datetime | None = None) -> list[dict]:
    now = now or now_utc()
    return [_compute(db, r, now) for r in db.execute(_REEL_SQL + " ORDER BY r.id")]


def evaluate_pauses(db, now: datetime | None = None) -> list[int]:
    """Pause every reel that crosses the report threshold. Returns newly paused ids."""
    paused_now = []
    for m in compute_all(db, now):
        if not m["paused"] and ranking.should_pause(m["reports"], m["dms"]):
            db.execute("UPDATE reels SET paused = 1 WHERE id = ?", (m["id"],))
            paused_now.append(m["id"])
    db.commit()
    return paused_now
