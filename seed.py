"""Generates a synthetic dataset: honest creators plus the abuse patterns we want to catch.

Everything here is made up. The `sim_kind` / `sim_fake` labels exist only so the
dashboard can grade the filters; the ranking code never reads them.

Run directly to rebuild the database:  python seed.py
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

import database
import metrics
import ranking
from util import iso, now_utc

VIEWER_ID = 1
ORGANIC_POOL = 1800

# One entry per reel. organic_taps are genuine accounts; click_p is the chance a
# DM recipient opens the link; unfollow_p is the chance a follow ends within 7 days.
REELS = [
    dict(handle="meera.bangles", age=520, kind="honest", emoji="💍", product="Bridal bangle set",
         caption="Hand-picked bangles for the wedding season. Tap for the link!",
         link="https://shop.example/meera/bangle-set", gate=1, views=8400, likes=630,
         organic_taps=260, click_p=0.80, unfollow_p=0.20, reports=0),
    dict(handle="tops_by_priya", age=410, kind="honest", emoji="👚", product="Cotton summer tops",
         caption="3 tops under ₹500, all bought by me. Link below!",
         link="https://shop.example/priya/tops", gate=1, views=6200, likes=496,
         organic_taps=180, click_p=0.78, unfollow_p=0.22, reports=0),
    dict(handle="study.with.sneha", age=300, kind="honest", emoji="📚", product="Free DSA notes (PDF)",
         caption="The notes I used for placements. Free PDF, tap to get it.",
         link="https://drive.example/sneha/dsa-notes.pdf", gate=1, views=9500, likes=570,
         organic_taps=330, click_p=0.90, unfollow_p=0.18, reports=0),
    dict(handle="ankita.crafts", age=640, kind="honest", emoji="🧶", product="Macrame wall hanging",
         caption="Made this over the weekend. Pattern link on tap (no follow needed).",
         link="https://shop.example/ankita/macrame", gate=0, views=3100, likes=217,
         organic_taps=90, click_p=0.75, unfollow_p=0.0, reports=0),
    dict(handle="rina.thrifts", age=200, kind="honest", emoji="🧥", product="Thrifted denim jacket",
         caption="Found this at a flea market. Want the seller link?",
         link="https://shop.example/rina/denim-jacket", gate=1, views=700, likes=42,
         organic_taps=12, click_p=0.80, unfollow_p=0.20, reports=0),
    # Abuse patterns -------------------------------------------------------
    dict(handle="giveaway.king", age=400, kind="clickbait", emoji="🎁", product="Mystery giveaway",
         caption="FREE phone giveaway!! Tap to enter!!",
         link="https://giveaway-hub.example/enter", gate=1, views=15000, likes=1200,
         organic_taps=900, click_p=0.15, unfollow_p=0.70, reports=6),
    dict(handle="trend.hub99", age=300, kind="farmed", emoji="🔥", product="Viral gadget",
         caption="Everyone is buying this. Tap for the link!",
         link="https://trend-hub.example/gadget", gate=1, views=6000, likes=200,
         organic_taps=40, click_p=0.60, unfollow_p=0.30, reports=0),
    dict(handle="quickdeals.store", age=800, kind="scam", emoji="📱", product="Phone at 90% off",
         caption="Phones at crazy prices, limited stock. Tap for the link!",
         link="https://quick-deals-store.example/phones", gate=1, views=3000, likes=240,
         organic_taps=120, click_p=0.50, unfollow_p=0.60, reports=4),
    dict(handle="fresh.finds", age=15, kind="honest", emoji="🛍️", product="Handmade earrings",
         caption="My first Reel! Earrings I made, link in DMs soon.",
         link="https://shop.example/fresh/earrings", gate=1, views=900, likes=60,
         organic_taps=0, click_p=0.0, unfollow_p=0.0, reports=0),
]


def _add_tap(rows, *, user_id, reel_id, creator_id, ts, gate, click_p, unfollow_p, fake, now, rng):
    """Append one tap, its DM, and (if the follow gate is on) its follow."""
    rows["taps"].append((user_id, reel_id, iso(ts), 0, int(fake)))
    clicked = None
    if rng.random() < click_p:
        clicked = iso(min(now, ts + timedelta(minutes=rng.uniform(1, 360))))
    rows["dms"].append([user_id, reel_id, iso(ts + timedelta(seconds=3)), clicked, None])
    if gate:
        ended = None
        if rng.random() < unfollow_p:
            end = ts + timedelta(days=rng.uniform(0.1, 6.9))
            ended = iso(end) if end <= now else None
        rows["follows"].append((user_id, creator_id, iso(ts), 1, reel_id, ended))


def seed(db_path: str, now: datetime | None = None, rng_seed: int = 7) -> None:
    rng = random.Random(rng_seed)
    now = now or now_utc()
    conn = database.connect(db_path)
    database.init_schema(conn)

    # --- Users ----------------------------------------------------------------
    users = [(VIEWER_ID, "you", 400, 0.9, 0, 1)]
    next_id = 2
    organic_ids = []
    for i in range(ORGANIC_POOL):
        users.append((next_id, f"user{i:04d}", rng.randint(60, 1500), round(rng.uniform(0.3, 1.0), 2), 0, 1))
        organic_ids.append(next_id)
        next_id += 1

    creator_ids = {}
    for spec in REELS:
        creator_ids[spec["handle"]] = next_id
        users.append((next_id, spec["handle"], spec["age"], 0.9, 1, 1))
        next_id += 1

    # --- Reels ----------------------------------------------------------------
    reels = []
    for rid, spec in enumerate(REELS, start=1):
        eligible = ranking.creator_eligible(spec["age"], True)
        reels.append((rid, creator_ids[spec["handle"]], spec["caption"], spec["product"], spec["emoji"],
                      spec["link"], spec["gate"], int(eligible), 0, spec["views"], spec["likes"], spec["kind"]))

    rows = {"taps": [], "dms": [], "follows": []}
    bot_users = []

    for rid, spec in enumerate(REELS, start=1):
        cid = creator_ids[spec["handle"]]
        n = spec["organic_taps"]
        extra = 120 if spec["kind"] == "farmed" else 0  # hijacked accounts, see below
        sample = rng.sample(organic_ids, n + extra)

        for uid in sample[:n]:
            ts = now - timedelta(seconds=rng.uniform(60, 28 * 86400))
            _add_tap(rows, user_id=uid, reel_id=rid, creator_id=cid, ts=ts, gate=spec["gate"],
                     click_p=spec["click_p"], unfollow_p=spec["unfollow_p"], fake=False, now=now, rng=rng)

        if spec["kind"] == "farmed":
            # (a) 2,500 brand-new bot accounts and (b) 400 dormant old accounts,
            # arriving in three bursts over the last 20 days.
            bursts = [now - timedelta(days=d, minutes=rng.randint(0, 600)) for d in (4, 11, 18)]
            for j in range(2900):
                uid = next_id
                next_id += 1
                if j < 2500:
                    bot_users.append((uid, f"bot{uid}", rng.randint(0, 10), round(rng.uniform(0, 0.08), 2), 0, 1))
                else:
                    bot_users.append((uid, f"dormant{uid}", rng.randint(100, 900), round(rng.uniform(0, 0.1), 2), 0, 1))
                ts = bursts[j % 3] + timedelta(minutes=rng.uniform(0, 25))
                _add_tap(rows, user_id=uid, reel_id=rid, creator_id=cid, ts=ts, gate=1,
                         click_p=0.02, unfollow_p=0.5, fake=True, now=now, rng=rng)
            # (c) 120 hijacked *real* accounts all tapping inside four minutes.
            # They pass the account filter, so only the burst cap catches them.
            start = (now - timedelta(days=2)).replace(minute=2, second=0)
            for uid in sample[n:]:
                ts = start + timedelta(seconds=rng.uniform(0, 240))
                _add_tap(rows, user_id=uid, reel_id=rid, creator_id=cid, ts=ts, gate=1,
                         click_p=0.0, unfollow_p=0.0, fake=True, now=now, rng=rng)

    # Reports: sample some DMs of each reel that has a report target.
    for rid, spec in enumerate(REELS, start=1):
        if spec["reports"]:
            idxs = [i for i, d in enumerate(rows["dms"]) if d[1] == rid]
            for i in rng.sample(idxs, spec["reports"]):
                rows["dms"][i][4] = iso(min(now, parse_ts(rows["dms"][i][2]) + timedelta(days=1)))

    # Viewer already follows one creator, so the "already following" path is visible.
    rows["follows"].append((VIEWER_ID, creator_ids["tops_by_priya"], iso(now - timedelta(days=40)), 0, None, None))

    conn.executemany("INSERT INTO users VALUES (?,?,?,?,?,?)", users + bot_users)
    conn.executemany("INSERT INTO reels VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", reels)
    conn.executemany("INSERT INTO taps (user_id, reel_id, ts, was_following, sim_fake) VALUES (?,?,?,?,?)", rows["taps"])
    conn.executemany("INSERT INTO dms (user_id, reel_id, ts, clicked_ts, reported_ts) VALUES (?,?,?,?,?)",
                     [tuple(d) for d in rows["dms"]])
    conn.executemany(
        "INSERT INTO follows (user_id, creator_id, ts, via_button, via_reel_id, ended_ts) VALUES (?,?,?,?,?,?)",
        rows["follows"],
    )
    conn.commit()
    metrics.evaluate_pauses(conn, now)
    conn.close()


def parse_ts(s: str) -> datetime:
    from util import parse
    return parse(s)


if __name__ == "__main__":
    import os

    path = os.environ.get("REEL_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "reels.db"))
    seed(path)
    print(f"Seeded {path}")
