import metrics
import database


def test_pages_render(client):
    for url in ("/", "/?rank=naive", "/dms", "/dashboard"):
        assert client.get(url).status_code == 200


def test_tap_follows_and_delivers_link(client, reel_id, query):
    rid = reel_id("meera.bangles")
    before = client.get("/").data
    res = client.post(f"/api/reel/{rid}/tap")
    data = res.get_json()
    assert res.status_code == 200 and data["ok"] and data["followed"] and not data["already"]
    assert "follow" in data["message"].lower()

    follow = query("SELECT via_button, via_reel_id FROM follows WHERE user_id = 1 AND creator_id = "
                   "(SELECT creator_id FROM reels WHERE id = ?)", (rid,))
    assert len(follow) == 1 and follow[0]["via_button"] == 1 and follow[0]["via_reel_id"] == rid
    assert len(query("SELECT 1 FROM dms WHERE user_id = 1 AND reel_id = ?", (rid,))) == 1
    assert client.get("/").data != before


def test_second_tap_is_idempotent(client, reel_id, query):
    rid = reel_id("meera.bangles")
    first = client.post(f"/api/reel/{rid}/tap").get_json()
    second = client.post(f"/api/reel/{rid}/tap").get_json()
    assert second["already"] and not second["followed"]
    assert second["public_count"] == first["public_count"]
    assert len(query("SELECT 1 FROM taps WHERE user_id = 1 AND reel_id = ?", (rid,))) == 1
    assert len(query("SELECT 1 FROM dms WHERE user_id = 1 AND reel_id = ?", (rid,))) == 1


def test_already_following_gets_link_without_new_follow(client, reel_id, query):
    rid = reel_id("tops_by_priya")  # the seed has the viewer following this creator
    data = client.post(f"/api/reel/{rid}/tap").get_json()
    assert data["ok"] and not data["followed"]
    follow = query("SELECT via_button FROM follows WHERE user_id = 1 AND creator_id = "
                   "(SELECT creator_id FROM reels WHERE id = ?)", (rid,))
    assert len(follow) == 1 and follow[0]["via_button"] == 0   # original follow untouched


def test_follow_gate_off_never_follows(client, reel_id, query):
    rid = reel_id("ankita.crafts")
    data = client.post(f"/api/reel/{rid}/tap").get_json()
    assert data["ok"] and not data["followed"]
    assert not query("SELECT 1 FROM follows WHERE user_id = 1 AND via_reel_id = ?", (rid,))


def test_ineligible_creator_cannot_use_button(client, reel_id, query):
    rid = reel_id("fresh.finds")
    res = client.post(f"/api/reel/{rid}/tap")
    assert res.status_code == 403
    assert not query("SELECT 1 FROM taps WHERE reel_id = ?", (rid,))


def test_unknown_reel_is_404(client):
    assert client.post("/api/reel/9999/tap").status_code == 404


def test_unfollow_then_tap_again_reactivates_follow(client, reel_id, query):
    rid = reel_id("meera.bangles")
    cid = query("SELECT creator_id FROM reels WHERE id = ?", (rid,))[0][0]
    client.post(f"/api/reel/{rid}/tap")
    assert client.post(f"/api/creator/{cid}/unfollow").get_json()["changed"]
    assert query("SELECT ended_ts FROM follows WHERE user_id = 1 AND creator_id = ?", (cid,))[0][0] is not None

    # Simulate "a different Reel from the same creator": clear this viewer's tap + DM, then tap again.
    with client.application.app_context():
        db = database.get_db()
        db.execute("DELETE FROM taps WHERE user_id = 1 AND reel_id = ?", (rid,))
        db.execute("DELETE FROM dms WHERE user_id = 1 AND reel_id = ?", (rid,))
        db.commit()
    data = client.post(f"/api/reel/{rid}/tap").get_json()
    assert data["followed"]
    rows = query("SELECT ended_ts FROM follows WHERE user_id = 1 AND creator_id = ?", (cid,))
    assert len(rows) == 1 and rows[0][0] is None   # same row, active again


def test_report_flow_auto_pauses_borderline_reel(client, reel_id, query):
    rid = reel_id("quickdeals.store")   # seeded with 4 reports out of 120 DMs: just under the threshold
    assert query("SELECT paused FROM reels WHERE id = ?", (rid,))[0][0] == 0
    client.post(f"/api/reel/{rid}/tap")
    dm_id = query("SELECT id FROM dms WHERE user_id = 1 AND reel_id = ?", (rid,))[0][0]

    data = client.post(f"/api/dm/{dm_id}/report").get_json()
    assert data["ok"] and data["paused"] and data["reports"] == 5
    assert query("SELECT paused FROM reels WHERE id = ?", (rid,))[0][0] == 1

    # Once paused, the button refuses further taps...
    assert client.post(f"/api/reel/{rid}/tap").status_code == 409
    # ...and reporting the same DM again must not inflate the count.
    again = client.post(f"/api/dm/{dm_id}/report").get_json()
    assert again["reports"] == 5


def test_paused_reel_loses_its_tap_ranking_credit(client, reel_id, query, app):
    rid = reel_id("quickdeals.store")
    with app.app_context():
        before = metrics.compute_reel(database.get_db(), rid)["trust_score"]
    client.post(f"/api/reel/{rid}/tap")
    dm_id = query("SELECT id FROM dms WHERE user_id = 1 AND reel_id = ?", (rid,))[0][0]
    client.post(f"/api/dm/{dm_id}/report")
    with app.app_context():
        after = metrics.compute_reel(database.get_db(), rid)
    assert after["trust_score"] < before
    assert after["trust_score"] == after["like_rate"]


def test_link_click_is_recorded_once(client, reel_id, query):
    rid = reel_id("meera.bangles")
    client.post(f"/api/reel/{rid}/tap")
    dm_id = query("SELECT id FROM dms WHERE user_id = 1 AND reel_id = ?", (rid,))[0][0]
    assert client.post(f"/api/dm/{dm_id}/click").get_json()["url"].startswith("https://")
    first = query("SELECT clicked_ts FROM dms WHERE id = ?", (dm_id,))[0][0]
    client.post(f"/api/dm/{dm_id}/click")
    assert query("SELECT clicked_ts FROM dms WHERE id = ?", (dm_id,))[0][0] == first


def test_farmed_reel_drops_under_trust_ranking(app):
    with app.app_context():
        items = metrics.compute_all(database.get_db())
    naive = [m["handle"] for m in sorted(items, key=lambda m: -m["naive_score"])]
    trust = [m["handle"] for m in sorted(items, key=lambda m: -m["trust_score"])]
    assert naive[0] == "trend.hub99"                      # raw taps reward the farmed reel
    assert trust.index("trend.hub99") >= len(trust) - 2   # trust-aware ranking buries it
    assert trust.index("meera.bangles") < trust.index("giveaway.king")


def test_filters_remove_nearly_all_fake_taps_and_no_genuine_ones(app):
    with app.app_context():
        items = metrics.compute_all(database.get_db())
    fake = sum(m["fake_total"] for m in items)
    fake_kept = sum(m["fake_kept"] for m in items)
    organic_dropped = sum(m["organic_dropped"] for m in items)
    assert fake > 0
    assert fake_kept / fake < 0.02
    assert organic_dropped == 0


def test_public_count_excludes_discounted_taps(app, reel_id):
    rid = reel_id("trend.hub99")
    with app.app_context():
        m = metrics.compute_reel(database.get_db(), rid)
    assert m["raw_taps"] > 3000 and m["valid_taps"] < 100
    assert m["public_count"] == str(m["valid_taps"])
