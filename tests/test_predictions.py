"""The registry core: band arithmetic, claim self-containment, idempotency."""
import math

import pytest

from market_desk.predictions import (
    GAUSSIAN_80, book_claim, claim_id, dedupe_new, event_aware_half_width,
    event_claim, ewma_cov, grade_book, grade_event, grade_range,
    grade_regime, horizon_variance_factor, portfolio_band_pct, range_claim,
    regime_claim, walkforward_amplification,
)


# ---- variance inflation ----------------------------------------------------

def test_variance_factor_is_quadratic_not_linear():
    # 5-session week, one 3x event: 4 ordinary units + 9, never 4 + 3.
    assert horizon_variance_factor(5, [3.0]) == pytest.approx(13.0)


def test_variance_factor_amp_one_matches_flat():
    assert horizon_variance_factor(5, [1.0]) == pytest.approx(5.0)
    assert horizon_variance_factor(5, []) == pytest.approx(5.0)


def test_variance_factor_rejects_more_events_than_sessions():
    with pytest.raises(ValueError):
        horizon_variance_factor(2, [2.0, 2.0, 2.0])


def test_event_aware_width_reduces_to_flat_without_events():
    flat = event_aware_half_width(0.01, 5, GAUSSIAN_80)
    assert flat == pytest.approx(GAUSSIAN_80 * 0.01 * math.sqrt(5))
    aware = event_aware_half_width(0.01, 5, GAUSSIAN_80, [3.0])
    assert aware / flat == pytest.approx(math.sqrt(13 / 5))


# ---- walk-forward amplification -------------------------------------------

def _moves(n=300, event_every=25, event_size=0.06, base=0.01):
    moves, events = {}, set()
    for i in range(n):
        d = f"d{i:04d}"
        if i and i % event_every == 0:
            moves[d] = event_size
            events.add(d)
        else:
            moves[d] = base
    return moves, events


def test_walkforward_amplification_recovers_planted_ratio():
    moves, events = _moves()
    amp = walkforward_amplification(moves, events, before="d0299")
    assert amp == pytest.approx(6.0)


def test_walkforward_amplification_withholds_below_min_events():
    moves, events = _moves()
    # before d0100 there are only 3 past events (d0025/50/75)
    assert walkforward_amplification(moves, events, before="d0100") is None


def test_walkforward_amplification_never_peeks():
    moves, events = _moves()
    moves["d0299"] = 5.0          # absurd future event
    events.add("d0299")
    amp = walkforward_amplification(moves, events, before="d0299")
    assert amp == pytest.approx(6.0)


# ---- claims are self-contained and grades use only the claim ---------------

def test_range_claim_and_grade():
    c = range_claim("XYL", "2026-08-21", 5, 100.0, half_width=0.05,
                    coverage=0.80, calibrated=True, amps=[2.0])
    assert c["low"] < 100.0 < c["high"]
    assert c["n_events"] == 1
    assert grade_range(c, 101.0)["hit"] is True
    assert grade_range(c, 100.0 * math.exp(0.06))["hit"] is False


def test_event_grade_ratio_and_baseline():
    c = event_claim("TT", "2026-08-21", "2026-10-29", "2026-10-29",
                    typical_move=0.038, amplification=4.1,
                    baseline_move=0.009, n_events=19)
    g = grade_event(c, 0.019)
    assert g["ratio"] == pytest.approx(0.5)
    assert g["beat_baseline"] is True


def test_regime_grade_scores_the_direction_of_the_magnitude_claim():
    c = regime_claim("MU", "2026-08-21", 5, "turbulent",
                     separation=1.72, baseline_abs=0.04)
    assert grade_regime(c, 0.09)["correct"] is True
    assert grade_regime(c, 0.01)["correct"] is False


def test_book_grade_weights_cash_free():
    c = book_claim("2026-08-21", 5, {"XYL": 0.5, "TT": 0.3},
                   band_pct=0.03, amps={})
    g = grade_book(c, {"XYL": 0.02, "TT": -0.01})
    assert g["realized"] == pytest.approx(0.007)
    assert g["hit"] is True


def test_dedupe_is_idempotent():
    c = range_claim("XYL", "2026-08-21", 5, 100.0, 0.05, 0.80, True)
    assert dedupe_new(set(), [c]) == [c]
    assert dedupe_new({c["id"]}, [c]) == []
    assert claim_id("range", "XYL", "2026-08-21", 5) == c["id"]


# ---- EWMA covariance -------------------------------------------------------

def test_ewma_cov_symmetric_with_sane_diagonal():
    import random
    rnd = random.Random(3)
    dates = [f"d{i:04d}" for i in range(300)]
    a = {d: rnd.gauss(0, 0.01) for d in dates}
    b = {d: 0.5 * a[d] + rnd.gauss(0, 0.008) for d in dates}
    symbols, cov = ewma_cov({"A": a, "B": b})
    assert symbols == ["A", "B"]
    assert cov[0][1] == pytest.approx(cov[1][0])
    assert cov[0][0] > 0 and cov[1][1] > 0
    corr = cov[0][1] / math.sqrt(cov[0][0] * cov[1][1])
    assert 0.2 < corr < 0.8          # planted at ~0.5


def test_portfolio_band_inflates_only_the_event_name():
    import random
    rnd = random.Random(4)
    dates = [f"d{i:04d}" for i in range(300)]
    rets = {s: {d: rnd.gauss(0, 0.01) for d in dates} for s in ("A", "B")}
    cov = ewma_cov(rets)
    w = {"A": 0.5, "B": 0.5}
    flat = portfolio_band_pct(w, cov, 5, {})
    aware = portfolio_band_pct(w, cov, 5, {"A": 3.0})
    assert aware > flat
