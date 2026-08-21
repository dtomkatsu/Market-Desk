"""The prediction registry's pure core: bands, claims, grades.

The registry exists because a prediction that is never graded is a
decoration. Every claim built here is written to ``history/predictions.jsonl``
BEFORE its window opens, carries everything needed to grade it later inside
the claim itself, and is scored mechanically once the window closes. That
turns "calibrated by walk-forward backtest" into "calibrated on record,
out of sample, accumulating" — a different epistemic category, and the
reason PREDICTIONS.md can carry a live scoreboard instead of only history.

Design rules, enforced here rather than remembered:

* **Claims are self-contained.** A range claim stores its absolute band;
  an event claim stores the typical move it predicts; a regime claim
  stores the baseline it must beat. The grader never recomputes an input —
  recomputing would let a changed estimator silently regrade the past.
* **Every claim has a deterministic id** (type:symbol:as_of:horizon), so a
  re-run of the same day's logger is idempotent rather than duplicative.
* **Nothing in a claim names a direction.** Ranges are two-sided, event
  claims are absolute sizes, regime claims are about magnitude. This is
  the same line the whole repo holds.
* **Event variance inflation is quadratic.** Amplification is measured as
  a median |move| ratio, so a reaction session contributes a^2 * sigma^2
  of variance, not a * sigma^2 — mixing those up understates every
  event-aware band. ``horizon_variance_factor`` owns the arithmetic.
* **The EWMA covariance shares the volatility module's conventions**:
  lambda=0.94 daily (never copied across cadences), seeded on an initial
  sample rather than a single product, index-aligned by date.
"""
from __future__ import annotations

import math
import statistics
from typing import Optional, Sequence

from .volatility import EWMA_LAMBDA, MIN_OBS

GAUSSIAN_80 = 1.2816          # fallback multiplier, always flagged as such


# ---------------------------------------------------------------------------
# Event-aware bands
# ---------------------------------------------------------------------------

def horizon_variance_factor(horizon: int, amps: Sequence[float]) -> float:
    """Sessions-worth of variance in a horizon containing amplified events.

    An ordinary session contributes 1 unit (sigma^2); a reaction session
    with amplification a contributes a^2. A 5-session week holding one
    3x earnings day therefore carries 4 + 9 = 13 units, not 5 — and not
    4 + 3 = 7, which is the linear mistake this function exists to avoid.
    """
    k = len(amps)
    if k > horizon:
        raise ValueError(f"{k} events cannot fit in {horizon} sessions")
    return (horizon - k) + sum(a * a for a in amps)


def event_aware_half_width(daily_vol: float, horizon: int,
                           multiplier: float,
                           amps: Sequence[float] = ()) -> float:
    """Half-width (log scale) of a band spanning ``horizon`` sessions."""
    return multiplier * daily_vol * math.sqrt(
        horizon_variance_factor(horizon, amps))


def walkforward_amplification(moves: dict[str, float],
                              reaction_dates: set[str],
                              before: str,
                              min_events: int = 4) -> Optional[float]:
    """Median event |move| over median ordinary |move|, using only history
    strictly before ``before``. None below ``min_events`` — a two-event
    median is a sample, not a measurement."""
    ev = [v for d, v in moves.items() if d in reaction_dates and d < before]
    other = [v for d, v in moves.items()
             if d not in reaction_dates and d < before]
    if len(ev) < min_events or len(other) < 60:
        return None
    base = statistics.median(other)
    if base <= 0:
        return None
    return statistics.median(ev) / base


# ---------------------------------------------------------------------------
# EWMA covariance (for the book claim)
# ---------------------------------------------------------------------------

def ewma_cov(returns_by_symbol: dict[str, dict[str, float]],
             lam: float = EWMA_LAMBDA) -> tuple[list[str], list[list[float]]]:
    """Daily EWMA covariance over the symbols' common dates.

    Seeded on the sample covariance of the first ``MIN_OBS`` common
    sessions, mirroring ``ewma_vol_series`` — a single day's outer product
    is not a covariance estimate.
    """
    symbols = sorted(returns_by_symbol)
    common = None
    for s in symbols:
        ds = set(returns_by_symbol[s])
        common = ds if common is None else common & ds
    dates = sorted(common or ())
    if len(dates) < MIN_OBS + 1:
        raise ValueError(f"only {len(dates)} common sessions; "
                         f"need > {MIN_OBS}")

    rows = [[returns_by_symbol[s][d] for s in symbols] for d in dates]
    n = len(symbols)
    seed = rows[:MIN_OBS]
    means = [sum(r[i] for r in seed) / len(seed) for i in range(n)]
    cov = [[sum((r[i] - means[i]) * (r[j] - means[j]) for r in seed)
            / (len(seed) - 1) for j in range(n)] for i in range(n)]
    for r in rows[MIN_OBS:]:
        for i in range(n):
            for j in range(n):
                cov[i][j] = lam * cov[i][j] + (1 - lam) * r[i] * r[j]
    return symbols, cov


def portfolio_band_pct(weights: dict[str, float],
                       cov: tuple[list[str], list[list[float]]],
                       horizon: int,
                       amps: dict[str, float],
                       multiplier: float = GAUSSIAN_80) -> float:
    """Half-width of the book band, as a fraction of book value.

    Diagonal terms carry each name's event inflation; cross terms use base
    (uninflated) covariance — event surprises are idiosyncratic, and
    propagating one name's earnings variance into its neighbours' cross
    terms would double-count what the correlation already carries.
    """
    symbols, matrix = cov
    idx = {s: i for i, s in enumerate(symbols)}
    var = 0.0
    for a, wa in weights.items():
        for b, wb in weights.items():
            if a not in idx or b not in idx:
                continue
            c = matrix[idx[a]][idx[b]]
            if a == b:
                c *= horizon_variance_factor(
                    horizon, [amps[a]] if a in amps else [])
            else:
                c *= horizon
            var += wa * wb * c
    return multiplier * math.sqrt(max(var, 0.0))


# ---------------------------------------------------------------------------
# Claims and grades — plain dicts, deterministic ids, self-contained
# ---------------------------------------------------------------------------

def claim_id(kind: str, symbol: str, as_of: str, horizon: int) -> str:
    return f"{kind}:{symbol}:{as_of}:{horizon}"


def range_claim(symbol: str, as_of: str, horizon: int, last_close: float,
                half_width: float, coverage: float, calibrated: bool,
                amps: Sequence[float] = (), method: str = "event_aware") -> dict:
    return {
        "id": claim_id("range", symbol, as_of, horizon),
        "kind": "range", "symbol": symbol, "as_of": as_of,
        "horizon_sessions": horizon, "coverage": coverage,
        "low": last_close * math.exp(-half_width),
        "high": last_close * math.exp(half_width),
        "pct": math.expm1(half_width),
        "calibrated": calibrated, "n_events": len(amps),
        "amps": list(amps), "method": method,
    }


def grade_range(claim: dict, close_at_expiry: float) -> dict:
    return {
        "id": claim["id"], "kind": "range",
        "realized": close_at_expiry,
        "hit": bool(claim["low"] <= close_at_expiry <= claim["high"]),
        "coverage": claim["coverage"],
    }


def event_claim(symbol: str, as_of: str, announce: str, session: str,
                typical_move: float, amplification: float,
                baseline_move: float, n_events: int) -> dict:
    return {
        "id": claim_id("event", symbol, as_of, 0) + f":{announce}",
        "kind": "event", "symbol": symbol, "as_of": as_of,
        "announce": announce, "session": session,
        "typical_move": typical_move, "amplification": amplification,
        "baseline_move": baseline_move, "n_events": n_events,
    }


def grade_event(claim: dict, realized_abs_move: float) -> dict:
    typical = claim["typical_move"]
    return {
        "id": claim["id"], "kind": "event",
        "realized": realized_abs_move,
        "ratio": (realized_abs_move / typical) if typical > 0 else None,
        "beat_baseline": bool(realized_abs_move > claim["baseline_move"]),
    }


def regime_claim(symbol: str, as_of: str, horizon: int, label: str,
                 separation: float, baseline_abs: float) -> dict:
    """Only issued for names whose regime verdict is CONFIRMED — an
    unvalidated label is a description and does not get to make claims."""
    return {
        "id": claim_id("regime", symbol, as_of, horizon),
        "kind": "regime", "symbol": symbol, "as_of": as_of,
        "horizon_sessions": horizon, "label": label,
        "separation": separation, "baseline_abs": baseline_abs,
        "predict_exceed": label == "turbulent",
    }


def grade_regime(claim: dict, realized_abs: float) -> dict:
    exceeded = realized_abs > claim["baseline_abs"]
    return {
        "id": claim["id"], "kind": "regime",
        "realized": realized_abs, "exceeded": bool(exceeded),
        "correct": bool(exceeded == claim["predict_exceed"]),
    }


def book_claim(as_of: str, horizon: int, weights: dict[str, float],
               band_pct: float, amps: dict[str, float],
               calibrated: bool = False) -> dict:
    return {
        "id": claim_id("book", "BOOK", as_of, horizon),
        "kind": "book", "as_of": as_of, "horizon_sessions": horizon,
        "weights": dict(weights), "pct": band_pct,
        "event_names": sorted(amps), "coverage": 0.80,
        "calibrated": calibrated,
    }


def grade_book(claim: dict, simple_returns: dict[str, float]) -> dict:
    """Realized book return over the window: sum of weight x simple return
    for the names still gradeable, cash contributing zero."""
    realized = sum(w * simple_returns.get(s, 0.0)
                   for s, w in claim["weights"].items())
    return {
        "id": claim["id"], "kind": "book",
        "realized": realized,
        "hit": bool(abs(realized) <= claim["pct"]),
        "coverage": claim["coverage"],
    }


def dedupe_new(existing_ids: set[str], claims: list[dict]) -> list[dict]:
    """The idempotency gate: a re-run of the same day's logger adds nothing."""
    return [c for c in claims if c["id"] not in existing_ids]
