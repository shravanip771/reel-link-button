from datetime import datetime, timedelta

import pytest

import ranking

T0 = datetime(2026, 11, 1, 12, 0, 0)


@pytest.mark.parametrize(
    "n, expected",
    [(0, "0"), (99, "99"), (100, "100+"), (234, "230+"), (999, "990+"), (1000, "1K+"),
     (8240, "8.2K+"), (15300, "15.3K+"), (1_250_000, "1.2M+")],
)
def test_format_count_rounds_down(n, expected):
    assert ranking.format_count(n) == expected


def test_trusted_account_needs_age_and_activity():
    assert ranking.is_trusted_account(200, 0.6)
    assert not ranking.is_trusted_account(5, 0.9)      # brand-new account
    assert not ranking.is_trusted_account(500, 0.05)   # old but dormant


def test_filter_drops_untrusted_accounts():
    taps = [(T0, 5, 0.9), (T0 + timedelta(hours=1), 500, 0.05), (T0 + timedelta(hours=2), 300, 0.7)]
    assert ranking.filter_taps(taps) == [False, False, True]


def test_burst_cap_limits_a_single_window():
    burst = [(T0 + timedelta(seconds=i), 300, 0.8) for i in range(100)]          # 100 taps in ~1.5 min
    spread = [(T0 + timedelta(hours=2 + i), 300, 0.8) for i in range(30)]        # 30 taps, one per hour
    kept = ranking.filter_taps(burst + spread)
    assert sum(kept[:100]) == ranking.BURST_FLOOR
    assert sum(kept[100:]) == 30


def test_burst_cap_does_not_touch_small_reels():
    taps = [(T0 + timedelta(minutes=i), 300, 0.8) for i in range(10)]
    assert all(ranking.filter_taps(taps))


def test_filter_handles_empty_input():
    assert ranking.filter_taps([]) == []


def test_smoothing_pulls_small_samples_toward_prior():
    assert ranking.smoothed(0, 0) == 0.5
    assert ranking.smoothed(3, 3) < 0.6               # 3 for 3 is not proof of quality
    assert ranking.smoothed(900, 1000) > 0.85         # lots of evidence moves it


def test_should_pause_needs_count_and_rate():
    assert not ranking.should_pause(4, 100)           # too few reports
    assert not ranking.should_pause(5, 1000)          # many DMs, tiny rate
    assert ranking.should_pause(5, 120)
    assert not ranking.should_pause(0, 0)


def test_creator_eligibility():
    assert ranking.creator_eligible(120, True)
    assert not ranking.creator_eligible(15, True)
    assert not ranking.creator_eligible(800, False)


def _score(**over):
    base = dict(views=10_000, likes=700, raw_taps=300, valid_taps=300, dms=300, clicks=240, reports=0,
                mature_follows=200, retained=160, follow_gate=True, paused=False)
    base.update(over)
    return ranking.score_reel(**base)


def test_quality_beats_volume():
    good = _score()
    clickbait = _score(raw_taps=900, valid_taps=900, dms=900, clicks=135, mature_follows=600, retained=180)
    assert clickbait["naive_score"] > good["naive_score"]   # raw taps reward clickbait
    assert good["trust_score"] > clickbait["trust_score"]   # quality-weighted ranking does not


def test_reports_reduce_and_pause_removes_tap_credit():
    clean = _score()
    reported = _score(reports=15)
    paused = _score(reports=15, paused=True)
    assert reported["trust_score"] < clean["trust_score"]
    assert paused["trust_score"] == pytest.approx(paused["like_rate"])


def test_no_follow_gate_means_no_retention_term():
    s = _score(follow_gate=False, mature_follows=0, retained=0)
    assert s["retention"] is None
    assert s["quality"] == pytest.approx(s["click_rate"])
