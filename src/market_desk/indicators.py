"""Price technicals. Pure functions over lists of floats — no pandas.

Every function returns a list the same length as its input, padded with
``None`` for the leading positions where the window has not filled yet.
That convention matters: it keeps every series index-aligned with the
bars, so the front end can zip them together without offset arithmetic,
and an un-warmed indicator is visibly absent rather than quietly wrong.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

Num = Optional[float]


def sma(values: Sequence[float], window: int) -> list[Num]:
    """Simple moving average."""
    out: list[Num] = [None] * len(values)
    if window <= 0:
        return out
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= window:
            running -= values[i - window]
        if i >= window - 1:
            out[i] = running / window
    return out


def ema(values: Sequence[float], window: int) -> list[Num]:
    """Exponential moving average, seeded with the first full SMA.

    Seeding on the SMA rather than the first observation keeps the early
    values from being dominated by a single print.
    """
    out: list[Num] = [None] * len(values)
    if window <= 0 or len(values) < window:
        return out
    k = 2.0 / (window + 1)
    prev = sum(values[:window]) / window
    out[window - 1] = prev
    for i in range(window, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values: Sequence[float], window: int = 14) -> list[Num]:
    """Wilder's RSI.

    Wilder smoothing (not a plain average of the last n changes) is what
    charting platforms draw; using the simple version puts our line
    visibly off theirs on the same data.
    """
    out: list[Num] = [None] * len(values)
    if len(values) <= window:
        return out

    gains = losses = 0.0
    for i in range(1, window + 1):
        change = values[i] - values[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain = gains / window
    avg_loss = losses / window
    out[window] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)

    for i in range(window + 1, len(values)):
        change = values[i] - values[i - 1]
        avg_gain = (avg_gain * (window - 1) + max(change, 0.0)) / window
        avg_loss = (avg_loss * (window - 1) + max(-change, 0.0)) / window
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def macd(values: Sequence[float], fast: int = 12, slow: int = 26,
         signal: int = 9) -> tuple[list[Num], list[Num], list[Num]]:
    """MACD line, signal line, histogram."""
    fast_ema = ema(values, fast)
    slow_ema = ema(values, slow)
    line: list[Num] = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(fast_ema, slow_ema)
    ]

    # The signal EMA runs over the MACD line's defined region only.
    defined = [(i, v) for i, v in enumerate(line) if v is not None]
    sig: list[Num] = [None] * len(values)
    hist: list[Num] = [None] * len(values)
    if len(defined) >= signal:
        sig_vals = ema([v for _, v in defined], signal)
        for (idx, _), s in zip(defined, sig_vals):
            sig[idx] = s
    for i in range(len(values)):
        if line[i] is not None and sig[i] is not None:
            hist[i] = line[i] - sig[i]
    return line, sig, hist


def atr(highs: Sequence[float], lows: Sequence[float],
        closes: Sequence[float], window: int = 14) -> list[Num]:
    """Average true range, Wilder-smoothed."""
    n = len(closes)
    out: list[Num] = [None] * n
    if n <= window:
        return out

    trs: list[float] = [highs[0] - lows[0]]
    for i in range(1, n):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    prev = sum(trs[1:window + 1]) / window
    out[window] = prev
    for i in range(window + 1, n):
        prev = (prev * (window - 1) + trs[i]) / window
        out[i] = prev
    return out


def bollinger(values: Sequence[float], window: int = 20,
              num_std: float = 2.0) -> tuple[list[Num], list[Num], list[Num]]:
    """Bollinger bands: (lower, mid, upper)."""
    mid = sma(values, window)
    lower: list[Num] = [None] * len(values)
    upper: list[Num] = [None] * len(values)
    for i in range(window - 1, len(values)):
        chunk = values[i - window + 1:i + 1]
        m = mid[i]
        if m is None:
            continue
        var = sum((v - m) ** 2 for v in chunk) / window
        sd = math.sqrt(var)
        lower[i] = m - num_std * sd
        upper[i] = m + num_std * sd
    return lower, mid, upper


def pct_change(values: Sequence[float], periods: int) -> Optional[float]:
    """Total return over the trailing ``periods`` sessions, as a fraction."""
    if len(values) <= periods or periods <= 0:
        return None
    start = values[-1 - periods]
    if start == 0:
        return None
    return values[-1] / start - 1.0


def annualized_vol(values: Sequence[float], window: int = 60) -> Optional[float]:
    """Annualized stdev of daily log returns over the trailing window."""
    if len(values) < window + 1:
        return None
    rets = [
        math.log(values[i] / values[i - 1])
        for i in range(len(values) - window, len(values))
        if values[i - 1] > 0 and values[i] > 0
    ]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


def max_drawdown(values: Sequence[float]) -> Optional[float]:
    """Worst peak-to-trough decline in the series, as a negative fraction."""
    if not values:
        return None
    peak = values[0]
    worst = 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            worst = min(worst, v / peak - 1.0)
    return worst


def range_position(value: float, low: float, high: float) -> Optional[float]:
    """Where ``value`` sits in [low, high] as a 0-1 fraction."""
    if high <= low:
        return None
    return max(0.0, min(1.0, (value - low) / (high - low)))
