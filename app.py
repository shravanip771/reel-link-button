"""Flask app: a fake Reels feed with a "Get link + Follow" button, a DM inbox, and an analytics dashboard.

Independent student prototype with synthetic data. Not affiliated with Instagram or Meta.
"""
from __future__ import annotations

import os

from flask import Flask, jsonify, render_template, request

import database
import metrics
import ranking
import seed
from util import iso, now_utc

VIEWER_ID = 1  # the demo viewer ("you")


def create_app(db_path: str | None = None) -> Flask:
    app = Flask(__name__)
    base = os.path.dirname(os.path.abspath(__file__))
    app.config["DB_PATH"] = db_path or os.environ.get("REEL_DB") or os.path.join(base, "reels.db")
    path = app.config["DB_PATH"]
    if not os.path.exists(path) or not database.schema_is_current(path):
        seed.seed(path)  # first run, or a demo database from an older version
    app.teardown_appcontext(database.close_db)

    # ------------------------------------------------------------------ pages
    @app.get("/")
    def feed():
        mode = "naive" if request.args.get("rank") == "naive" else "trust"
        db = database.get_db()
        items = metrics.compute_all(db)
        key = "naive_score" if mode == "naive" else "trust_score"
        items.sort(key=lambda m: m[key], reverse=True)
        tapped = {r["reel_id"] for r in db.execute("SELECT reel_id FROM taps WHERE user_id = ?", (VIEWER_ID,))}
        following = {
            r["creator_id"]
            for r in db.execute("SELECT creator_id FROM follows WHERE user_id = ? AND ended_ts IS NULL", (VIEWER_ID,))
        }
        comments: dict[int, list] = {}
        for c in db.execute("SELECT * FROM comments ORDER BY id"):
            comments.setdefault(c["reel_id"], []).append(c)
        return render_template(
            "feed.html", items=items, tapped=tapped, following=following, mode=mode, comments=comments
        )

    @app.get("/dms")
    def dms():
        db = database.get_db()
        rows = db.execute(
            """SELECT d.id, d.ts, d.clicked_ts, d.reported_ts, r.id AS reel_id, r.product, r.link_url,
                      r.paused, u.handle, u.id AS creator_id
               FROM dms d JOIN reels r ON r.id = d.reel_id JOIN users u ON u.id = r.creator_id
               WHERE d.user_id = ? ORDER BY d.ts DESC""",
            (VIEWER_ID,),
        ).fetchall()
        return render_template("dms.html", dms=rows)

    @app.get("/dashboard")
    def dashboard():
        db = database.get_db()
        items = metrics.compute_all(db)
        naive_rank = {m["id"]: i for i, m in enumerate(sorted(items, key=lambda m: -m["naive_score"]), start=1)}
        trust_rank = {m["id"]: i for i, m in enumerate(sorted(items, key=lambda m: -m["trust_score"]), start=1)}
        totals = {
            "raw": sum(m["raw_taps"] for m in items),
            "valid": sum(m["valid_taps"] for m in items),
            "fake": sum(m["fake_total"] for m in items),
            "fake_kept": sum(m["fake_kept"] for m in items),
            "organic": sum(m["organic_total"] for m in items),
            "organic_dropped": sum(m["organic_dropped"] for m in items),
        }
        return render_template(
            "dashboard.html",
            items=sorted(items, key=lambda m: trust_rank[m["id"]]),
            naive_rank=naive_rank,
            trust_rank=trust_rank,
            totals=totals,
            r=ranking,
        )

    # -------------------------------------------------------------------- API
    @app.post("/api/reel/<int:reel_id>/tap")
    def tap(reel_id: int):
        db = database.get_db()
        reel = db.execute(
            "SELECT r.*, u.handle FROM reels r JOIN users u ON u.id = r.creator_id WHERE r.id = ?", (reel_id,)
        ).fetchone()
        if reel is None:
            return jsonify(error="not_found", message="Reel not found."), 404
        if not reel["button_enabled"]:
            return jsonify(error="not_available", message="This creator can't use the link button yet."), 403
        if reel["paused"]:
            return jsonify(error="paused", message="This button is paused while reports are reviewed."), 409

        if db.execute("SELECT 1 FROM taps WHERE user_id = ? AND reel_id = ?", (VIEWER_ID, reel_id)).fetchone():
            m = metrics.compute_reel(db, reel_id)
            return jsonify(ok=True, already=True, followed=False, public_count=m["public_count"],
                           message="You already requested this link. Check your DMs.")

        now = iso(now_utc())
        existing = db.execute(
            "SELECT id, ended_ts FROM follows WHERE user_id = ? AND creator_id = ?", (VIEWER_ID, reel["creator_id"])
        ).fetchone()
        following = existing is not None and existing["ended_ts"] is None

        db.execute(
            "INSERT INTO taps (user_id, reel_id, ts, was_following) VALUES (?,?,?,?)",
            (VIEWER_ID, reel_id, now, int(following)),
        )
        followed = False
        if reel["follow_gate"] and not following:
            if existing is None:
                db.execute(
                    "INSERT INTO follows (user_id, creator_id, ts, via_button, via_reel_id) VALUES (?,?,?,?,?)",
                    (VIEWER_ID, reel["creator_id"], now, 1, reel_id),
                )
            else:  # re-follow after an earlier unfollow
                db.execute(
                    "UPDATE follows SET ts = ?, ended_ts = NULL, via_button = 1, via_reel_id = ? WHERE id = ?",
                    (now, reel_id, existing["id"]),
                )
            followed = True
        db.execute("INSERT INTO dms (user_id, reel_id, ts) VALUES (?,?,?)", (VIEWER_ID, reel_id, now))
        db.commit()

        m = metrics.compute_reel(db, reel_id)
        msg = f"Link sent to your DMs. You now follow @{reel['handle']}." if followed else "Link sent to your DMs."
        return jsonify(ok=True, already=False, followed=followed, public_count=m["public_count"], message=msg)

    @app.post("/api/creator/<int:creator_id>/unfollow")
    def unfollow(creator_id: int):
        db = database.get_db()
        cur = db.execute(
            "UPDATE follows SET ended_ts = ? WHERE user_id = ? AND creator_id = ? AND ended_ts IS NULL",
            (iso(now_utc()), VIEWER_ID, creator_id),
        )
        db.commit()
        return jsonify(ok=True, changed=cur.rowcount > 0)

    @app.post("/api/dm/<int:dm_id>/click")
    def click_link(dm_id: int):
        db = database.get_db()
        dm = db.execute(
            "SELECT d.id, d.clicked_ts, r.link_url FROM dms d JOIN reels r ON r.id = d.reel_id "
            "WHERE d.id = ? AND d.user_id = ?",
            (dm_id, VIEWER_ID),
        ).fetchone()
        if dm is None:
            return jsonify(error="not_found"), 404
        if dm["clicked_ts"] is None:
            db.execute("UPDATE dms SET clicked_ts = ? WHERE id = ?", (iso(now_utc()), dm_id))
            db.commit()
        return jsonify(ok=True, url=dm["link_url"])

    @app.post("/api/dm/<int:dm_id>/report")
    def report_link(dm_id: int):
        db = database.get_db()
        dm = db.execute("SELECT id, reel_id, reported_ts FROM dms WHERE id = ? AND user_id = ?",
                        (dm_id, VIEWER_ID)).fetchone()
        if dm is None:
            return jsonify(error="not_found"), 404
        if dm["reported_ts"] is None:
            db.execute("UPDATE dms SET reported_ts = ? WHERE id = ?", (iso(now_utc()), dm_id))
            db.commit()
        m = metrics.compute_reel(db, dm["reel_id"])
        paused = ranking.should_pause(m["reports"], m["dms"])
        if paused and not m["paused"]:
            db.execute("UPDATE reels SET paused = 1 WHERE id = ?", (dm["reel_id"],))
            db.commit()
        return jsonify(ok=True, reports=m["reports"], paused=paused)

    @app.post("/api/reset")
    def reset():
        database.close_db()
        seed.seed(app.config["DB_PATH"])
        return jsonify(ok=True)

    return app


if __name__ == "__main__":
    create_app().run(port=int(os.environ.get("PORT", 5000)), debug=False)
