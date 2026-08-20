"""Volatility regime and expected range — the "how much might it swing" half.

The premise, and its limit: volatility **clusters**. Large moves follow
large moves, and realized volatility is strongly autocorrelated — one of
the most robust findings in empirical finance (Engle's ARCH work). Return
*direction* over days-to-weeks is not similarly forecastable, so nothing
here predicts which way a stock goes. It estimates how far, and how
unsettled the current state is.

Three pieces:

* ``ewma_vol`` — exponentially-weighted daily volatility, λ=0.94. That is
  the RiskMetrics **daily** constant and is deliberately NOT the λ=0.97
  the upstream forecaster uses for monthly bars: a decay constant encodes
  a half-life in *bars*, and reusing one across cadences silently changes
  the memory of the estimator. (Census-Forecaster's own rule: never copy a
  damping constant from one cadence to another.)
* ``classify_regime`` — where current volatility sits in its own trailing
  history. Self-relative on purpose: 2% daily vol is turbulent for a
  utility and ordinary for a biotech, so an absolute threshold would just
  re-discover which names are volatile.
* ``expected_range`` — a band around the current price, with the
  multiplier **empirically calibrated** by walk-forward coverage rather
  than taken from a normal table.

  A note on what that calibration actually finds, because the obvious
  expectation is wrong: raw daily equity returns are famously fat-tailed,
  but these ratios are standardized by a *time-varying* EWMA volatility,
  and an adaptive variance absorbs much of that excess into itself. Across
  this universe the measured 80% multiplier lands roughly 1.08-1.30 —
  scattered around, and often slightly below, the 1.2816 Gaussian value
  rather than far above it. The case for calibrating is therefore not "the
  true number is much larger" but "the true number varies per series and
  is worth measuring instead of assuming"; GHRS at 1.08 and DIA at 1.30
  should not share a band width.

``validate_regimes`` is what keeps this from being decoration: it walks
forward through history, labels each day using only prior data, and
measures the realized move that followed. If turbulent days are not
actually followed by larger moves than quiet days, the classifier has no
predictive content on that series and the payload says so.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

# RiskMetrics daily decay. Half-life ≈ ln(0.5)/ln(0.94) ≈ 11 sessions.
EWMA_LAMBDA = 0.94

# Trailing window used to place today's volatility in its own distribution.
REGIME_WINDOW = 252

# Regime cut points, as percentiles of the series' own trailing volatility.
QUIET_PCTL = 0.33
TURBULENT_PCTL = 0.67

# Absolute floor below which "turbulent" is meaningless. A purely relative
# classifier will happily call a cash-like instrument turbulent at 0.06%
# daily volatility, because that is still the top of its own distribution —
# technically true and practically nonsense. Nothing real in an equity
# universe sits below 3% annualized (the calmest bond ETF here is ~11%), so
# this only catches degenerate series such as a stable-value or money-market
# holding.
MIN_TURBULENT_ANNUAL_VOL = 0.03

# Minimum history before any of this is meaningful.
MIN_OBS = 60

TRADING_DAYS = 252


def log_returns(closes: Sequence[float]) -> list[float]:
    out = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            out.append(math.log(closes[i] / closes[i - 1]))
    return out


def ewma_vol_series(closes: Sequence[float],
                    lam: float = EWMA_LAMBDA) -> list[Optional[float]]:
    """EWMA daily volatility, index-aligned with ``closes``.

    Seeded on the sample standard deviation of the first ``MIN_OBS``
    returns rather than on a single squared return, which would make the
    first dozen values hostage to one session.
    """
    out: list[Optional[float]] = [None] * len(closes)
    rets = log_returns(closes)
    if len(rets) < MIN_OBS:
        return out

    seed = rets[:MIN_OBS]
    mean = sum(seed) / len(seed)
    var = sum((r - mean) ** 2 for r in seed) / (len(seed) - 1)
    out[MIN_OBS] = math.sqrt(var)

    for i in range(MIN_OBS, len(rets)):
        var = lam * var + (1 - lam) * rets[i] ** 2
        # rets[i] is the return INTO closes[i + 1]
        out[i + 1] = math.sqrt(var)
    return out


def ewma_vol(closes: Sequence[float], lam: float = EWMA_LAMBDA) -> Optional[float]:
    series = ewma_vol_series(closes, lam)
    return next((v for v in reversed(series) if v is not None), None)


def _percentile_of(value: float, population: Sequence[float]) -> Optional[float]:
    vals = [v for v in population if v is not None]
    if len(vals) < 20:
        return None
    return sum(1 for v in vals if v <= value) / len(vals)


@dataclass(frozen=True)
class Regime:
    label: str                    # quiet | normal | turbulent | unknown
    percentile: Optional[float]   # where current vol sits in its own history
    daily_vol: Optional[float]
    annualized_vol: Optional[float]
    detail: str = ""


def classify_regime(closes: Sequence[float],
                    window: int = REGIME_WINDOW) -> Regime:
    """Label the current volatility state against the series' own history."""
    series = ewma_vol_series(closes)
    current = next((v for v in reversed(series) if v is not None), None)
    if current is None:
        return Regime("unknown", None, None, None,
                      "not enough history to estimate volatility")

    history = [v for v in series[-window:] if v is not None]
    pctl = _percentile_of(current, history)
    annual = current * math.sqrt(TRADING_DAYS)

    if pctl is None:
        return Regime("unknown", None, current, annual,
                      "not enough history to place volatility in its own distribution")
    if annual < MIN_TURBULENT_ANNUAL_VOL:
        return Regime("quiet", pctl, current, annual,
                      f"Annualized volatility is {annual * 100:.1f}% — cash-like. "
                      "Too small for the regime distinction to mean anything, "
                      "whatever its percentile.")
    if pctl <= QUIET_PCTL:
        label = "quiet"
        detail = ("Volatility is in the calmest third of this stock's own "
                  "trailing year — smaller moves than usual, in either direction.")
    elif pctl >= TURBULENT_PCTL:
        label = "turbulent"
        detail = ("Volatility is in the most active third of this stock's own "
                  "trailing year — larger moves than usual. This says nothing "
                  "about which way.")
    else:
        label = "normal"
        detail = "Volatility is near this stock's own typical level."
    return Regime(label, pctl, current, annual, detail)


@dataclass(frozen=True)
class ExpectedRange:
    horizon_days: int
    low: float
    high: float
    pct: float                    # half-width as a fraction of price
    multiplier: float
    calibrated: bool
    coverage_target: float = 0.80


def calibrate_multiplier(closes: Sequence[float], horizon: int,
                         coverage: float = 0.80,
                         min_train: int = 120) -> Optional[float]:
    """Walk-forward multiplier hitting ``coverage`` of realized moves.

    For each historical anchor: estimate volatility from prior bars only,
    standardize the realized ``horizon``-day absolute log move by
    ``σ·√horizon``, and take the empirical ``coverage`` quantile of those
    ratios. A band built with it would have contained that share of past
    moves — the direct empirical analogue of a z-score, and robust to the
    fat tails a normal table gets wrong.
    """
    series = ewma_vol_series(closes)
    ratios: list[float] = []
    for t in range(min_train, len(closes) - horizon):
        vol = series[t]
        if vol is None or vol <= 0:
            continue
        if closes[t] <= 0 or closes[t + horizon] <= 0:
            continue
        realized = abs(math.log(closes[t + horizon] / closes[t]))
        ratios.append(realized / (vol * math.sqrt(horizon)))
    if len(ratios) < 30:
        return None
    ratios.sort()
    idx = min(int(coverage * len(ratios)), len(ratios) - 1)
    return ratios[idx]


def expected_range(closes: Sequence[float], horizon: int,
                   coverage: float = 0.80) -> Optional[ExpectedRange]:
    """Band the price is expected to stay inside over ``horizon`` sessions."""
    if not closes:
        return None
    vol = ewma_vol(closes)
    if vol is None or vol <= 0:
        return None

    multiplier = calibrate_multiplier(closes, horizon, coverage)
    calibrated = multiplier is not None
    if multiplier is None:
        # 80% two-sided normal quantile, used only when the series is too
        # short to calibrate. Flagged as uncalibrated in the payload: it is
        # a reasonable central guess, not this series' measured value.
        multiplier = 1.2816

    half = multiplier * vol * math.sqrt(horizon)
    last = closes[-1]
    return ExpectedRange(
        horizon_days=horizon,
        low=last * math.exp(-half),
        high=last * math.exp(half),
        pct=math.expm1(half),
        multiplier=multiplier,
        calibrated=calibrated,
        coverage_target=coverage,
    )


@dataclass
class RegimeValidation:
    """Does the regime label actually precede larger moves, out of sample?"""
    horizon_days: int
    n: int = 0
    mean_abs_move: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    separation: Optional[float] = None    # turbulent ÷ quiet
    monotonic: bool = False               # turbulent > normal > quiet
    verdict: str = "insufficient history"


def validate_regimes(closes: Sequence[float], horizon: int = 5,
                     window: int = REGIME_WINDOW,
                     min_train: int = 150) -> RegimeValidation:
    """Walk forward, label each day from prior data only, measure what followed.

    This is the gate between a measurement and a decoration. The label at
    index ``t`` uses ``closes[:t + 1]``; the move measured is from ``t`` to
    ``t + horizon``, which the label could not have seen.
    """
    out = RegimeValidation(horizon_days=horizon)
    if len(closes) < min_train + horizon + 10:
        return out

    series = ewma_vol_series(closes)
    buckets: dict[str, list[float]] = {"quiet": [], "normal": [], "turbulent": []}

    for t in range(min_train, len(closes) - horizon):
        vol = series[t]
        if vol is None:
            continue
        history = [v for v in series[max(0, t - window):t + 1] if v is not None]
        pctl = _percentile_of(vol, history)
        if pctl is None:
            continue
        label = ("quiet" if pctl <= QUIET_PCTL
                 else "turbulent" if pctl >= TURBULENT_PCTL else "normal")
        if closes[t] <= 0 or closes[t + horizon] <= 0:
            continue
        buckets[label].append(abs(math.log(closes[t + horizon] / closes[t])))

    out.counts = {k: len(v) for k, v in buckets.items()}
    out.n = sum(out.counts.values())
    if min(out.counts.values()) < 20:
        return out

    out.mean_abs_move = {k: sum(v) / len(v) for k, v in buckets.items()}
    quiet, normal, turbulent = (out.mean_abs_move["quiet"],
                                out.mean_abs_move["normal"],
                                out.mean_abs_move["turbulent"])
    out.separation = turbulent / quiet if quiet > 0 else None
    out.monotonic = turbulent > normal > quiet

    if out.monotonic and out.separation and out.separation >= 1.3:
        out.verdict = "confirmed"
    elif out.separation and out.separation >= 1.1:
        out.verdict = "weak"
    else:
        out.verdict = "no separation"
    return out
