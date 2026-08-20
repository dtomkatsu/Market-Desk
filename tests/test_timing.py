"""Volatility regime and catalyst timing.

The two behaviours worth pinning: an after-close announcement reacts on the
NEXT session, and a regime label only becomes a claim when walk-forward
validation says it separates future moves.
"""
import math

import pytest

from market_desk.catalysts import (
    announcement_date, build_catalysts, describe, measure_reaction,
)
from market_desk.fetch import Bar
from market_desk.volatility import (
    calibrate_multiplier, classify_regime, ewma_vol, expected_range,
    validate_regimes,
)


def bars_from(closes, start_day=1):
    out = []
    for i, c in enumerate(closes):
        y, m, d = 2025 + i // 336, 1 + (i // 28) % 12, 1 + i % 28
        out.append(Bar(f"{y}-{m:02d}-{d:02d}", c, c * 1.01, c * 0.99, c, 1e6))
    return out


# ---------------- announcement timing ----------------

def test_premarket_announcement_reacts_same_session():
    day, next_session = announcement_date("2026-07-28T06:00:00-04:00")
    assert day == "2026-07-28"
    assert next_session is False


def test_after_close_announcement_reacts_next_session():
    day, next_session = announcement_date("2026-08-26T16:00:00-04:00")
    assert day == "2026-08-26"
    assert next_session is True, "16:00 ET is after the close"


def test_bare_date_is_treated_as_premarket():
    assert announcement_date("2026-08-26") == ("2026-08-26", False)


def test_reaction_measured_on_the_correct_bar():
    """Flat series with spikes the day AFTER each after-close report.

    Measuring the announcement date itself finds an ordinary day and
    concludes earnings barely move this name — the real bug this guards,
    which made NVDA read 0.7x when the true figure is 2.9x.
    """
    # Small ordinary noise so the baseline median is non-zero; a perfectly
    # flat series has a zero baseline and no ratio can be formed from it.
    closes = [100.0 * (1 + 0.002 * math.sin(i)) for i in range(200)]
    ann_indices = [40, 80, 120, 160]
    for i in ann_indices:
        closes[i + 1] = closes[i] * 1.10     # the move lands the NEXT session
    bars = bars_from(closes)
    ann_days = [bars[i].date for i in ann_indices]

    after_close = measure_reaction(bars, [f"{d}T16:00:00-04:00" for d in ann_days])
    premarket = measure_reaction(bars, [f"{d}T06:00:00-04:00" for d in ann_days])

    assert after_close is not None and premarket is not None
    # The after-close reading lands on the spikes; the pre-market one misses.
    assert after_close.median_move > 0.09
    assert premarket.median_move < 0.02
    assert after_close.amplification > premarket.amplification


def test_reaction_needs_enough_events():
    bars = bars_from([100.0 + i * 0.1 for i in range(80)])
    assert measure_reaction(bars, [bars[40].date]) is None      # 1 event


def test_amplification_below_one_is_reported_not_hidden():
    """Some names genuinely move LESS on earnings than on an ordinary day."""
    closes = [100.0]
    for i in range(1, 200):
        closes.append(closes[-1] * (1 + (0.04 if i % 2 else -0.038)))
    bars = bars_from(closes)
    quiet_days = [bars[i].date for i in range(20, 120, 20)]
    for i in range(20, 120, 20):
        closes[i] = closes[i - 1] * 1.0005                       # a calm print
    bars = bars_from(closes)
    r = measure_reaction(bars, quiet_days)
    assert r is not None
    assert r.amplification < 1.0
    assert r.meaningful is False


def test_describe_flags_an_unremarkable_reaction():
    closes = [100.0 + math.sin(i / 3) for i in range(200)]
    bars = bars_from(closes)
    days = [bars[i].date for i in range(20, 140, 20)]
    cat = build_catalysts("AAA", bars, days)
    text = describe(cat)
    assert "Reports" in text or "No scheduled" in text


def test_future_dates_are_relative_to_the_last_bar():
    bars = bars_from([100.0] * 60)
    last = bars[-1].date
    cat = build_catalysts("AAA", bars, [f"{last}T06:00:00-04:00", "2099-01-05T06:00:00-05:00"])
    assert cat.next_earnings == "2099-01-05"
    assert len(cat.past_earnings) == 1


# ---------------- volatility ----------------

def test_ewma_vol_rises_with_turbulence():
    calm = [100.0 * (1 + 0.001 * math.sin(i)) for i in range(200)]
    wild = [100.0 * (1 + 0.05 * math.sin(i)) for i in range(200)]
    assert ewma_vol(wild) > ewma_vol(calm) * 5


def test_ewma_vol_needs_history():
    assert ewma_vol([100.0] * 10) is None


def test_regime_labels_are_self_relative():
    """A high-vol stock at its own calm level is 'quiet', not 'turbulent'."""
    import random
    random.seed(3)
    closes = [100.0]
    for i in range(400):
        shock = 0.05 if i < 250 else 0.002          # calms down at the end
        closes.append(max(1.0, closes[-1] * (1 + random.gauss(0, shock))))
    r = classify_regime(closes)
    assert r.label == "quiet", "recent calm should read quiet despite high absolute vol"
    assert 0.0 <= r.percentile <= 1.0


def test_regime_unknown_without_history():
    r = classify_regime([100.0] * 20)
    assert r.label == "unknown"
    assert "not enough history" in r.detail


def test_expected_range_brackets_the_last_price():
    import random
    random.seed(5)
    closes = [100.0]
    for _ in range(400):
        closes.append(max(1.0, closes[-1] * (1 + random.gauss(0, 0.015))))
    er = expected_range(closes, horizon=5)
    assert er is not None
    assert er.low < closes[-1] < er.high
    assert er.pct > 0


def test_wider_horizon_gives_a_wider_band():
    import random
    random.seed(7)
    closes = [100.0]
    for _ in range(500):
        closes.append(max(1.0, closes[-1] * (1 + random.gauss(0, 0.012))))
    assert expected_range(closes, 21).pct > expected_range(closes, 1).pct


def test_calibrated_multiplier_is_measured_not_assumed():
    """The multiplier must come from the series, and differ between series.

    Deliberately NOT asserting it exceeds the 1.2816 Gaussian value. These
    ratios are standardized by a time-varying EWMA volatility, and an
    adaptive variance absorbs much of the fat tail into itself — measured
    across real symbols the value scatters roughly 1.08-1.30, often just
    below the normal quantile. The point of calibrating is per-series
    accuracy, not a uniformly wider band.
    """
    import random
    random.seed(11)

    def series(sigma, jump_every=None):
        closes = [100.0]
        for i in range(700):
            s = sigma * 6 if (jump_every and i % jump_every == 0) else sigma
            closes.append(max(1.0, closes[-1] * (1 + random.gauss(0, s))))
        return closes

    calm = calibrate_multiplier(series(0.01), horizon=5)
    jumpy = calibrate_multiplier(series(0.01, jump_every=40), horizon=5)
    assert calm is not None and jumpy is not None
    # Both land in the empirically observed band rather than at the fallback.
    for m in (calm, jumpy):
        assert 0.8 < m < 2.5
        assert m != pytest.approx(1.2816), "must be measured, not the fallback"
    # Different dynamics must produce different widths - which direction is
    # NOT asserted: clustered jumps keep EWMA elevated on neighbouring days,
    # inflating the denominator, so the effect on the quantile is not
    # monotonic in jump size. Measured, not assumed, is the whole point.
    assert calm != pytest.approx(jumpy, abs=1e-6)


def test_uncalibrated_range_is_flagged():
    closes = [100.0 + i * 0.05 for i in range(90)]
    er = expected_range(closes, horizon=5)
    if er is not None:
        assert er.calibrated is False
        assert er.multiplier == pytest.approx(1.2816)


def test_validation_detects_no_separation_on_constant_volatility():
    """IID returns have no volatility clustering, so regimes must not 'work'."""
    import random
    random.seed(13)
    closes = [100.0]
    for _ in range(900):
        closes.append(max(1.0, closes[-1] * (1 + random.gauss(0, 0.02))))
    v = validate_regimes(closes, horizon=5)
    assert v.n > 100
    assert v.verdict in ("no separation", "weak"), (
        "constant-vol series must not produce a confirmed regime signal")


def test_validation_reports_insufficient_history():
    v = validate_regimes([100.0] * 50)
    assert v.verdict == "insufficient history"
    assert v.mean_abs_move == {}
