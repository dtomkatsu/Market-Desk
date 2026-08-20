"""Momentum crash risk.

The behaviour that matters most is epistemic: a conditional result
computed from too few observations must be reported as inconclusive, not
as a finding. The repo's own deep-drawdown bucket holds 6.
"""
import pytest

from market_desk.crashrisk import (
    CORRECTION_DRAWDOWN, MIN_BUCKET, STRESS_DRAWDOWN, assess,
    market_state, measure_local_evidence,
)


def rising(n=800, rate=0.0008):
    closes, dates = [100.0], []
    for i in range(n):
        closes.append(closes[-1] * (1 + rate))
    for i in range(len(closes)):
        y, m, d = 2022 + i // 336, 1 + (i // 28) % 12, 1 + i % 28
        dates.append(f"{y}-{m:02d}-{d:02d}")
    return dates, closes


def test_calm_market_when_near_highs():
    dates, closes = rising()
    st = market_state(dates, closes)
    assert st.label == "calm"
    assert st.panic is False
    assert st.drawdown > CORRECTION_DRAWDOWN


def test_drawdown_is_measured_from_the_running_peak():
    dates, closes = rising(n=700)
    closes = closes + [closes[-1] * 0.75]          # -25% from the peak
    dates = dates + ["2026-12-01"]
    st = market_state(dates, closes)
    assert st.drawdown == pytest.approx(-0.25, abs=0.01)
    assert st.stressed is True


def test_panic_needs_both_stress_and_volatility():
    """A quiet grind lower is not the state the crash literature describes."""
    import math
    import random
    random.seed(2)
    # Stressed but calm: a slide with realistic-but-small daily noise, and
    # crucially a volatility that DECLINES into the present, so the current
    # reading sits low in its own distribution.
    closes = [100.0]
    for i in range(700):
        noise = 0.020 if i < 400 else 0.004
        closes.append(closes[-1] * 0.9994 * (1 + random.gauss(0, noise)))
    dates = [f"2022-{1 + i % 12:02d}-{1 + i % 28:02d}" for i in range(len(closes))]
    st = market_state(dates, closes)
    assert st.stressed is True
    assert st.vol_regime != "turbulent"
    assert st.panic is False, "stress alone must not trigger the panic flag"


def test_exposure_is_low_without_either_leg():
    dates, closes = rising()
    risk = assess(market_state(dates, closes), None, momentum_tilt=0.45)
    assert risk.exposure == "low"
    assert any("Neither leg" in n for n in risk.notes)


def test_exposure_is_latent_when_tilted_but_calm():
    dates, closes = rising()
    risk = assess(market_state(dates, closes), None, momentum_tilt=0.85)
    assert risk.exposure == "moderate"
    assert any("latent" in n for n in risk.notes)


def test_a_neutral_book_in_panic_is_only_moderate():
    """Exposure is the product of market state AND the book's own tilt."""
    from market_desk.crashrisk import MarketState
    panic = MarketState(date="2026-01-01", drawdown=-0.30, trailing_return=-0.10,
                        vol_regime="turbulent", bear=True, stressed=True,
                        panic=True, label="panic", detail="")
    tilted = assess(panic, None, momentum_tilt=0.90)
    neutral = assess(panic, None, momentum_tilt=0.45)
    assert tilted.exposure == "elevated"
    assert neutral.exposure == "moderate", "no momentum tilt means little to reverse"


def test_long_only_caveat_always_present():
    dates, closes = rising()
    risk = assess(market_state(dates, closes), None, 0.5)
    assert any("long-only" in n for n in risk.notes)


# ---------------- local evidence ----------------

def _evidence_fixture(n_dates, drawdown_value):
    """Build a synthetic panel with a genuine winners-minus-losers spread."""
    symbols = [f"S{i:02d}" for i in range(15)]
    dates = [f"2026-{1 + i % 12:02d}-{1 + i % 28:02d}" for i in range(n_dates + 40)]
    closes, series, hist, dd = {}, {}, {}, {}
    for si, sym in enumerate(symbols):
        prices, ds = {}, []
        price = 100.0
        for d in dates:
            prices[d] = price
            price *= 1 + (0.002 if si >= 10 else -0.001)   # top names drift up
            ds.append(d)
        closes[sym] = prices
        series[sym] = ds
    for i, d in enumerate(dates[:n_dates]):
        hist[d] = {sym: (si / (len(symbols) - 1)) for si, sym in enumerate(symbols)}
        dd[d] = drawdown_value
    return hist, closes, series, dd


def test_thin_bucket_is_reported_as_inconclusive():
    hist, closes, series, dd = _evidence_fixture(5, drawdown_value=-0.30)
    ev = measure_local_evidence(hist, closes, series, dd, horizon=21)
    deep = next(b for b in ev.buckets if b.label == "deep drawdown")
    assert deep.n < MIN_BUCKET
    assert deep.conclusive is False
    assert ev.verdict == "insufficient sample"
    assert "decade-scale" in ev.caveat


def test_large_bucket_is_reported_as_measured():
    hist, closes, series, dd = _evidence_fixture(MIN_BUCKET + 10, drawdown_value=-0.30)
    ev = measure_local_evidence(hist, closes, series, dd, horizon=21)
    deep = next(b for b in ev.buckets if b.label == "deep drawdown")
    assert deep.conclusive is True
    assert ev.verdict == "measured"
    # Top-tercile names drift up and bottom-tercile down, so the spread is positive.
    assert deep.mean_spread > 0


def test_evidence_handles_no_overlap():
    ev = measure_local_evidence({}, {}, {}, {}, horizon=21)
    assert ev.n_observations == 0
    assert "No overlapping history" in ev.caveat
