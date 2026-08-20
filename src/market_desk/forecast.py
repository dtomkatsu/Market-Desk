"""Bridge to Census-Forecaster's damped-drift ticker forecaster.

Why reuse rather than reimplement: ``census_forecaster.markets.trend``
already carries a calibrated apparatus this repo has no business
duplicating — a damped-drift point at the repo-standard φ=0.92/month and
a 90% band whose multiplier is *walk-forward calibrated per ticker*
rather than taken from a normal table. Equity returns are fat-tailed;
the empirical multiplier is typically well above 1.645, and that gap is
the whole point of the exercise.

Cadence is the one real seam. That forecaster is monthly and this repo
is daily, so daily bars are folded to month-end closes before they cross
the boundary. The fold is deliberately last-close-of-month rather than a
monthly average: the upstream panel is built from month-end adjusted
closes, and mixing an averaged series into a calibration fitted on
month-end data would put the bands on the wrong footing.

The dependency is optional at runtime. If the pinned package is missing
or its API has moved, forecasts are skipped and the reason is recorded
in the output — a refresh that drops a panel is far better than one that
dies and leaves the dashboard stale with no explanation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from .fetch import Bar


@dataclass(frozen=True)
class Horizon:
    """One forecast horizon for one symbol."""
    months: int
    target_date: str
    value: float
    lo90: float
    hi90: float


@dataclass
class SymbolForecast:
    symbol: str
    horizons: list[Horizon]
    monthly_vol: Optional[float] = None
    band_multiplier: Optional[float] = None
    calibrated: bool = False
    months_used: int = 0
    error: Optional[str] = None


def _import_trend():
    """Import the upstream forecaster, or return None with a reason."""
    try:
        from census_forecaster.markets import trend  # type: ignore
        from census_forecaster.markets.client import MonthlyBar  # type: ignore
        return trend, MonthlyBar, None
    except Exception as exc:                       # noqa: BLE001
        return None, None, f"{type(exc).__name__}: {exc}"


def to_monthly(bars: Sequence[Bar], drop_partial: bool = True) -> list[tuple[int, int, float, float]]:
    """Fold daily bars to (year, month, last_close, month_volume).

    The current, incomplete calendar month is dropped by default. A
    three-session "month" would understate its own volatility and drag
    the drift estimate toward whatever the last few days did — the same
    reason the upstream client drops it.
    """
    buckets: dict[tuple[int, int], list[Bar]] = {}
    for bar in bars:
        y, m, _ = bar.date.split("-")
        buckets.setdefault((int(y), int(m)), []).append(bar)

    out: list[tuple[int, int, float, float]] = []
    for (y, m) in sorted(buckets):
        days = buckets[(y, m)]
        out.append((y, m, days[-1].close, sum(d.volume for d in days)))

    if drop_partial and out:
        today = date.today()
        if out[-1][0] == today.year and out[-1][1] == today.month:
            out.pop()
    return out


def _add_months(anchor: date, months: int) -> date:
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    return date(year, month, 1)


def forecast_symbol(symbol: str, bars: Sequence[Bar],
                    horizons: tuple[int, ...] = (3, 6, 12),
                    vol_method: str = "ewma") -> SymbolForecast:
    """Damped-drift forecast with a calibrated 90% band, for one symbol.

    ``vol_method="ewma"`` matches the upstream board's default: a 2026-07
    bake-off over 3,806 pooled walk-forward forecasts ranked EWMA(λ=0.97)
    first on interval score at identical coverage.
    """
    trend, MonthlyBar, err = _import_trend()
    if trend is None:
        return SymbolForecast(symbol, [], error=f"census_forecaster unavailable ({err})")

    monthly = to_monthly(bars)
    if len(monthly) < 24:
        return SymbolForecast(
            symbol, [], months_used=len(monthly),
            error=f"only {len(monthly)} complete months; need 24 to forecast",
        )

    mbars = [
        MonthlyBar(year=y, month=m, adj_close=close, volume=vol)
        for (y, m, close, vol) in monthly
    ]

    try:
        multiplier = trend.calibrate_band_multiplier(
            mbars, horizons=horizons, vol_method=vol_method,
        )
    except Exception as exc:                        # noqa: BLE001
        multiplier = None
        print(f"  ! {symbol}: band calibration failed ({exc})")

    anchor = date(mbars[-1].year, mbars[-1].month, 1)
    out: list[Horizon] = []
    monthly_vol: Optional[float] = None
    for h in horizons:
        target = _add_months(anchor, h)
        try:
            fc = trend.forecast_ticker(
                mbars, target, band_multiplier=multiplier, vol_method=vol_method,
            )
        except Exception as exc:                    # noqa: BLE001
            print(f"  ! {symbol}: {h}m forecast failed ({exc})")
            continue
        monthly_vol = fc.monthly_vol
        out.append(Horizon(
            months=h,
            target_date=target.isoformat(),
            value=fc.value,
            lo90=fc.lo90,
            hi90=fc.hi90,
        ))

    return SymbolForecast(
        symbol=symbol,
        horizons=out,
        monthly_vol=monthly_vol,
        band_multiplier=multiplier,
        calibrated=multiplier is not None,
        months_used=len(monthly),
        error=None if out else "no horizon produced a forecast",
    )


def forecast_all(bars_by_symbol: dict[str, list[Bar]],
                 horizons: tuple[int, ...] = (3, 6, 12),
                 vol_method: str = "ewma") -> dict[str, SymbolForecast]:
    out: dict[str, SymbolForecast] = {}
    for symbol, bars in bars_by_symbol.items():
        out[symbol] = forecast_symbol(symbol, bars, horizons=horizons, vol_method=vol_method)
    return out
