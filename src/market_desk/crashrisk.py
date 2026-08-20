"""Momentum crash risk — market state, and what it does to a momentum tilt.

Momentum's failure mode is not gradual decay; it is rare, violent
reversal. Daniel & Moskowitz (2016) document that momentum strategies
crash in an identifiable state: after sustained market declines, with
elevated volatility, and most severely when the market rebounds. The
mechanism is that prolonged losers become distressed and highly
market-sensitive, so a sharp rebound lifts exactly the names a momentum
tilt is underweight.

**Provenance matters here more than anywhere else in this repo.** The
crash condition below is imported from published research, not
discovered in this data — and it deliberately is not presented as
validated locally, because it cannot be. The events that define the
phenomenon (1932, 2009) are decades apart; a five-year watchlist
contains none of them. What this module measures on local history is
reported separately, with its sample size attached, and at these counts
it settles nothing:

    market near highs      n=30   mean 1-month winners-minus-losers  +0.27%
    moderate drawdown      n=10                                      +3.14%
    deep drawdown (<-15%)  n= 6                                      -0.43%

The direction of that last bucket is what the literature predicts. Six
observations is not evidence, and ``local_evidence`` says so rather than
letting a suggestive sign masquerade as confirmation.

For a long-only book the exposure is also softer than the published
result. Daniel & Moskowitz measure a long-short strategy, and the crash
lands mostly on the *short* leg. Holding high-momentum names without
shorting low-momentum ones carries a muted version: relative
underperformance in a rebound, not the catastrophic drawdown of the
academic factor.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Optional, Sequence

from .volatility import classify_regime

# Trailing window for the "has the market gone nowhere or down" test.
# Daniel & Moskowitz use 24 months; 504 sessions is the daily equivalent.
BEAR_LOOKBACK = 504

# Drawdown depth that marks a market genuinely under stress. -15% is a
# deliberate middle ground: -10% is an ordinary correction, -20% is rare
# enough that a five-year sample would contain almost no observations.
STRESS_DRAWDOWN = -0.15
CORRECTION_DRAWDOWN = -0.05

# Below this, a conditional bucket is reported but must not be read as a
# finding. The deep-drawdown bucket in this repo's own data sits at 6.
MIN_BUCKET = 25


@dataclass(frozen=True)
class MarketState:
    """Where the benchmark stands, on the axes the crash literature uses."""
    date: str
    drawdown: float                    # from running peak, negative
    trailing_return: Optional[float]   # over BEAR_LOOKBACK sessions
    vol_regime: str                    # quiet | normal | turbulent | unknown
    bear: bool                         # trailing return negative
    stressed: bool                     # drawdown past STRESS_DRAWDOWN
    panic: bool                        # the Daniel & Moskowitz condition
    label: str                         # calm | correction | stressed | panic
    detail: str


def market_state(dates: Sequence[str], closes: Sequence[float]) -> Optional[MarketState]:
    """Classify the benchmark's current state."""
    if len(closes) < 60:
        return None

    peak = max(closes)
    running_peak = closes[0]
    for c in closes:
        running_peak = max(running_peak, c)
    drawdown = closes[-1] / running_peak - 1.0

    trailing = None
    if len(closes) > BEAR_LOOKBACK and closes[-1 - BEAR_LOOKBACK] > 0:
        trailing = closes[-1] / closes[-1 - BEAR_LOOKBACK] - 1.0

    regime = classify_regime(closes)
    bear = trailing is not None and trailing < 0
    stressed = drawdown <= STRESS_DRAWDOWN
    # Both legs required: a quiet grind lower is not the state the crash
    # literature describes — the reversals happen when it is also volatile.
    panic = (bear or stressed) and regime.label == "turbulent"

    if panic:
        label = "panic"
        detail = ("The market is both under stress and volatile — the state in "
                  "which momentum has historically reversed hardest, especially "
                  "on a sharp rebound.")
    elif stressed:
        label = "stressed"
        detail = (f"The market is {drawdown * 100:.0f}% off its peak but not "
                  "currently volatile by its own standards.")
    elif drawdown <= CORRECTION_DRAWDOWN:
        label = "correction"
        detail = f"An ordinary pullback: {drawdown * 100:.0f}% off the peak."
    else:
        label = "calm"
        detail = "The market is near its highs."

    return MarketState(
        date=dates[-1], drawdown=drawdown, trailing_return=trailing,
        vol_regime=regime.label, bear=bear, stressed=stressed,
        panic=panic, label=label, detail=detail,
    )


@dataclass
class Bucket:
    label: str
    n: int
    mean_spread: Optional[float] = None
    median_spread: Optional[float] = None
    positive: int = 0

    @property
    def conclusive(self) -> bool:
        return self.n >= MIN_BUCKET


@dataclass
class LocalEvidence:
    """What this repo's own history says — with its sample size attached."""
    horizon_days: int
    n_observations: int = 0
    overall_mean: Optional[float] = None
    overall_positive: int = 0
    buckets: list[Bucket] = field(default_factory=list)
    verdict: str = "insufficient sample"
    caveat: str = ""


def measure_local_evidence(history_by_date: dict[str, dict[str, float]],
                           closes_by_symbol: dict[str, dict[str, float]],
                           dates_by_symbol: dict[str, list[str]],
                           benchmark_drawdown: dict[str, float],
                           horizon: int = 21) -> LocalEvidence:
    """Forward winners-minus-losers spread, grouped by market drawdown.

    At each historical date the tracked names are sorted by their momentum
    rank *as of that date*, split into terciles, and the forward return of
    the top tercile is compared with the bottom. Everything uses data that
    existed at the sample date; the forward window is what came after.
    """
    out = LocalEvidence(horizon_days=horizon)
    rows: list[tuple[float, float]] = []      # (drawdown, spread)

    for date, ranks in sorted(history_by_date.items()):
        if len(ranks) < 12:
            continue
        forward: dict[str, float] = {}
        for symbol in ranks:
            series = dates_by_symbol.get(symbol)
            prices = closes_by_symbol.get(symbol)
            if not series or not prices or date not in prices:
                continue
            i = series.index(date)
            if i + horizon >= len(series):
                continue
            start, end = prices[date], prices[series[i + horizon]]
            if start > 0:
                forward[symbol] = end / start - 1.0

        common = [s for s in ranks if s in forward]
        if len(common) < 12:
            continue
        ordered = sorted(common, key=lambda s: ranks[s])
        k = max(3, len(ordered) // 3)
        losers = [forward[s] for s in ordered[:k]]
        winners = [forward[s] for s in ordered[-k:]]
        rows.append((benchmark_drawdown.get(date, 0.0),
                     statistics.mean(winners) - statistics.mean(losers)))

    if not rows:
        out.caveat = "No overlapping history and forward window."
        return out

    spreads = [s for _, s in rows]
    out.n_observations = len(rows)
    out.overall_mean = statistics.mean(spreads)
    out.overall_positive = sum(1 for s in spreads if s > 0)

    definitions = [
        ("market near highs", lambda dd: dd > CORRECTION_DRAWDOWN),
        ("moderate drawdown", lambda dd: STRESS_DRAWDOWN <= dd <= CORRECTION_DRAWDOWN),
        ("deep drawdown", lambda dd: dd < STRESS_DRAWDOWN),
    ]
    for label, test in definitions:
        sub = [s for dd, s in rows if test(dd)]
        bucket = Bucket(label=label, n=len(sub))
        if sub:
            bucket.mean_spread = statistics.mean(sub)
            bucket.median_spread = statistics.median(sub)
            bucket.positive = sum(1 for s in sub if s > 0)
        out.buckets.append(bucket)

    stressed_bucket = next((b for b in out.buckets if b.label == "deep drawdown"), None)
    if stressed_bucket is None or not stressed_bucket.conclusive:
        n = stressed_bucket.n if stressed_bucket else 0
        out.verdict = "insufficient sample"
        out.caveat = (
            f"The stressed-market bucket holds {n} observations, below the {MIN_BUCKET} "
            "this repo requires before reading a conditional result as a finding. "
            "Momentum crashes are decade-scale events; a watchlist spanning a few "
            "years cannot test for them. The crash condition flagged above comes "
            "from published research, not from this data."
        )
    else:
        out.verdict = "measured"
        out.caveat = ("Measured on this universe only — a few dozen names over a few "
                      "years, not a market-wide factor study.")
    return out


@dataclass
class CrashRisk:
    state: Optional[MarketState] = None
    evidence: Optional[LocalEvidence] = None
    portfolio_momentum_tilt: Optional[float] = None
    exposure: str = "unknown"          # low | moderate | elevated | unknown
    notes: list[str] = field(default_factory=list)


def assess(state: Optional[MarketState], evidence: Optional[LocalEvidence],
           momentum_tilt: Optional[float]) -> CrashRisk:
    """Combine market state with the book's own momentum tilt.

    Exposure is the product of two things: whether the market is in the
    state where momentum reverses, and whether the book is actually tilted
    toward momentum. A neutral book in a panic has little to reverse.
    """
    risk = CrashRisk(state=state, evidence=evidence,
                     portfolio_momentum_tilt=momentum_tilt)
    if state is None:
        risk.notes.append("No benchmark history available to classify market state.")
        return risk

    tilted = momentum_tilt is not None and momentum_tilt >= 0.60
    contrarian = momentum_tilt is not None and momentum_tilt <= 0.40

    if state.panic and tilted:
        risk.exposure = "elevated"
        risk.notes.append(
            "The market is in the stressed-and-volatile state associated with "
            "momentum reversals, and this book is tilted toward momentum "
            f"({momentum_tilt:.2f}). That is the combination the literature "
            "warns about.")
    elif state.panic:
        risk.exposure = "moderate"
        risk.notes.append(
            "The market is in the state associated with momentum reversals, but "
            f"this book's momentum tilt is {momentum_tilt:.2f} — near neutral or "
            "contrarian, so there is little momentum exposure to reverse.")
    elif tilted:
        risk.exposure = "moderate"
        risk.notes.append(
            f"The book is tilted toward momentum ({momentum_tilt:.2f}), but the "
            "market is not in a stressed state. Crash risk is latent rather than "
            "active — the tilt matters if conditions change.")
    else:
        risk.exposure = "low"
        detail = (f"a near-neutral momentum tilt ({momentum_tilt:.2f})"
                  if momentum_tilt is not None else "no measured momentum tilt")
        risk.notes.append(
            f"The market is not in a stressed state and the book carries {detail}. "
            "Neither leg of the crash condition is present.")
    if contrarian and state.panic:
        risk.notes.append(
            "A contrarian tilt has historically benefited from the rebounds that "
            "hurt momentum — the mirror of the same effect, and equally unproven "
            "on a sample this size.")

    risk.notes.append(
        "This condition is imported from published research on long-short "
        "momentum strategies. A long-only book carries a muted version: relative "
        "underperformance in a rebound, not the academic factor's drawdown. "
        "Descriptive context, not a forecast and not advice.")
    return risk
