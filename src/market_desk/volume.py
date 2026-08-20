"""Volume analytics.

Volume is the participation behind a price move, and it earns its own
module because the useful questions are comparative, not absolute: 40M
shares means nothing until you know the name usually trades 12M, and a
new price high means something different on expanding volume than on
contracting volume.

Same padding convention as ``indicators``: series come back index-aligned
with the bars, ``None`` where a window has not filled.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from .indicators import sma

Num = Optional[float]


def relative_volume(volumes: Sequence[float], window: int = 20) -> list[Num]:
    """Today's volume as a multiple of its trailing average (RVOL).

    The average is computed over the *prior* ``window`` sessions and
    excludes the current bar. Including today would let a single huge
    session inflate its own baseline and mute the very spike the measure
    exists to catch.
    """
    out: list[Num] = [None] * len(volumes)
    for i in range(window, len(volumes)):
        prior = volumes[i - window:i]
        base = sum(prior) / window
        if base > 0:
            out[i] = volumes[i] / base
    return out


def dollar_volume(closes: Sequence[float], volumes: Sequence[float]) -> list[float]:
    """Traded value per session — the honest liquidity measure.

    Share count alone makes a $3 stock look more liquid than a $600 one.
    """
    return [c * v for c, v in zip(closes, volumes)]


def obv(closes: Sequence[float], volumes: Sequence[float]) -> list[float]:
    """On-balance volume: a running signed total of volume.

    Adds the session's volume when the close rose, subtracts it when the
    close fell. The level is arbitrary; only its slope carries meaning.
    """
    out = [0.0] * len(closes)
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            out[i] = out[i - 1] + volumes[i]
        elif closes[i] < closes[i - 1]:
            out[i] = out[i - 1] - volumes[i]
        else:
            out[i] = out[i - 1]
    return out


def rolling_vwap(highs: Sequence[float], lows: Sequence[float],
                 closes: Sequence[float], volumes: Sequence[float],
                 window: int = 20) -> list[Num]:
    """Volume-weighted average price over a trailing window.

    True intraday VWAP needs tick data; from daily bars the standard
    proxy is the typical price (H+L+C)/3 weighted by session volume.
    Called out explicitly because it is a proxy, not the real thing.
    """
    typical = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
    out: list[Num] = [None] * len(closes)
    for i in range(window - 1, len(closes)):
        num = sum(typical[j] * volumes[j] for j in range(i - window + 1, i + 1))
        den = sum(volumes[j] for j in range(i - window + 1, i + 1))
        if den > 0:
            out[i] = num / den
    return out


def accumulation_distribution(highs: Sequence[float], lows: Sequence[float],
                              closes: Sequence[float],
                              volumes: Sequence[float]) -> list[float]:
    """Chaikin A/D line.

    Weights each session's volume by where the close landed inside the
    session's range: a close at the high counts as full accumulation, at
    the low as full distribution, mid-range as neutral. Unlike OBV this
    reads the bar's internals rather than only its direction.
    """
    out = [0.0] * len(closes)
    running = 0.0
    for i in range(len(closes)):
        span = highs[i] - lows[i]
        if span > 0:
            mfm = ((closes[i] - lows[i]) - (highs[i] - closes[i])) / span
            running += mfm * volumes[i]
        out[i] = running
    return out


def up_down_volume_ratio(closes: Sequence[float], volumes: Sequence[float],
                         window: int = 50) -> Optional[float]:
    """Volume on up sessions ÷ volume on down sessions over the window.

    Above 1 means the name trades heavier when it rises. Returns None
    when the window holds no down sessions — a ratio against zero is
    infinite, not informative.
    """
    if len(closes) < window + 1:
        return None
    up = down = 0.0
    for i in range(len(closes) - window, len(closes)):
        if closes[i] > closes[i - 1]:
            up += volumes[i]
        elif closes[i] < closes[i - 1]:
            down += volumes[i]
    if down <= 0:
        return None
    return up / down


def volume_trend(volumes: Sequence[float], short: int = 20,
                 long: int = 60) -> Optional[float]:
    """Short-window average volume ÷ long-window average.

    Above 1 means participation is expanding. This is the measure that
    separates a real breakout from a drift on thin tape.
    """
    if len(volumes) < long:
        return None
    short_avg = sum(volumes[-short:]) / short
    long_avg = sum(volumes[-long:]) / long
    if long_avg <= 0:
        return None
    return short_avg / long_avg


@dataclass(frozen=True)
class Divergence:
    """Whether price and participation are telling the same story."""
    verdict: str        # confirmed | weak | washout | quiet | mixed
    detail: str
    price_change: Optional[float] = None
    volume_ratio: Optional[float] = None


def price_volume_divergence(closes: Sequence[float], volumes: Sequence[float],
                            window: int = 20, long: int = 60) -> Divergence:
    """Classify the last ``window`` sessions by move-vs-participation.

    The classic read: a rally on expanding volume is confirmed, the same
    rally on contracting volume is suspect, and a decline on heavy volume
    is capitulation rather than drift. This is a heuristic label to give
    the analysis note a starting point — not a signal, and it is not
    backtested. Treated as such everywhere downstream.
    """
    if len(closes) < long + 1:
        return Divergence("quiet", "not enough history to judge participation")

    start = closes[-1 - window]
    if start <= 0:
        return Divergence("quiet", "unusable price history")
    change = closes[-1] / start - 1.0
    ratio = volume_trend(volumes, short=window, long=long)
    if ratio is None:
        return Divergence("quiet", "no usable volume baseline", change, None)

    rising = change > 0.02
    falling = change < -0.02
    heavy = ratio > 1.15
    light = ratio < 0.85

    if rising and heavy:
        verdict, detail = "confirmed", "advance on expanding volume — participation backs the move"
    elif rising and light:
        verdict, detail = "weak", "advance on contracting volume — the move is thinly supported"
    elif falling and heavy:
        verdict, detail = "washout", "decline on heavy volume — active selling, not drift"
    elif falling and light:
        verdict, detail = "quiet", "decline on light volume — drift rather than distribution"
    elif rising or falling:
        # A real move, but participation is in line with its own baseline —
        # volume neither confirms nor contradicts it.
        direction = "advance" if rising else "decline"
        verdict, detail = "mixed", f"{direction} on ordinary volume — participation is unremarkable"
    else:
        verdict, detail = "quiet", "no clear price move over the window"

    return Divergence(verdict, detail, change, ratio)
