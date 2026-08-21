#!/usr/bin/env python3
"""Does scaling exposure by inverse variance beat holding? (Moreira-Muir)

    python scripts/volmanaged_study.py                # SPY, full history
    python scripts/volmanaged_study.py --symbol QQQ
    python scripts/volmanaged_study.py --json out.json

The other studies in this repo ask whether anything predicts the DIRECTION
of returns and keep finding nothing — which is the market-efficiency prior
doing its job. This one asks the question the repo's validated machinery
says is answerable: volatility is forecastable even though direction is not
(``validate_regimes`` confirms it on the index ETFs). Moreira & Muir (2017)
show that this alone is worth something: scale exposure by c / sigma^2 each
month and the managed series earns positive alpha against the unmanaged
one, because expected returns do not rise one-for-one with variance.
Volatility timing is not direction timing — the weight never goes negative;
it only decides HOW MUCH of the position to hold, which is why this is the
one timing idea compatible with this repo's no-directional-calls rule.

Constructions measured:

* **Monthly, matched-vol (the paper's).** Weight c / RV over the prior 21
  sessions, rebalanced at month boundaries, c set so the managed series
  matches buy-and-hold's full-sample volatility. That c is a display
  convention, not information — t(alpha) is invariant to scaling the
  weight by a constant. The weight is UNCAPPED and routinely above 2x in
  quiet markets: this cell is the academic claim, not an implementable
  book.
* **Monthly, no leverage.** The same signal with the weight capped into
  [0,1] and the unheld fraction earning the T-bill rate. The only version
  a long-only account can run — and the cap is not cosmetic, because much
  of the published alpha comes from levering UP in quiet markets.
  Measuring what survives the cap is the point of this cell. The scale
  constant is the EXPANDING median of past RV (no look-ahead): the median
  month runs fully invested, high-vol months scale down.
* **Daily EWMA variant.** lambda=0.94 (the repo's daily constant, imported
  from ``volatility.py``), daily rebalance, capped [0,1]. More responsive
  and more turnover, so it is reported with annualized turnover and the
  breakeven one-way cost that would erase its alpha. Its weights change
  daily but its alpha is evaluated at the same monthly granularity as the
  other cells, so the four t-statistics are comparable like for like.

Honesty constraints:

* **The spanning alpha is the statistic**, as in the paper: managed excess
  return regressed on unmanaged excess return. Positive alpha means no
  static holding replicates the managed series. Sharpe ratios are shown
  but not tested — Sharpe-difference tests on one path add noise, not
  rigor. Newey-West errors at the rebalance horizon; beta is treated as
  known, which slightly understates the SE, said here rather than hidden.
* **Cederburg et al. (2020) is the standing caveat**: vol-managed alphas
  are fragile out-of-sample across most factors. The market factor is
  where the effect is strongest — and this test IS the market factor —
  but one path cannot settle it. The 5y sub-sample cell shows the recent
  regime rather than claiming significance on 60 months.
* **Cash earns the bill rate.** ^IRX supplies it; if that fetch fails the
  run says so and uses zero, which UNDERSTATES the no-leverage version in
  high-rate eras rather than flattering it.
* **Volatility timing rides on the one regularity this repo has actually
  validated** (vol clustering), which is why it gets tested at all. A
  null here would still be a finding: clustering alone does not guarantee
  the risk-return tradeoff is flat enough to profit from.

**Multiple testing.** Four alpha cells. At t=2 a fifth of a false positive
is expected; Harvey, Liu & Zhu (2016) put the hurdle for a new claim at
t>3.
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import statistics
import sys
import warnings
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
warnings.filterwarnings("ignore")

from market_desk.fetch import fetch_history            # noqa: E402
from market_desk.indicators import max_drawdown        # noqa: E402
from market_desk.volatility import ewma_vol_series     # noqa: E402

from shock_study import newey_west                     # noqa: E402

RV_WINDOW = 21          # prior-month realized variance, the paper's signal
BURN_IN = 252           # sessions of history required before the strategy starts
SESSIONS_5Y = 5 * 252
BILL = "^IRX"           # 13-week T-bill discount yield, percent


# ---------------------------------------------------------------------------
# Series
# ---------------------------------------------------------------------------

def simple_returns(bars) -> tuple[list[str], list[float], list[float]]:
    dates, rets, closes = [], [], []
    for i in range(1, len(bars)):
        a, b = bars[i - 1].close, bars[i].close
        if a > 0 and b > 0:
            dates.append(bars[i].date)
            rets.append(b / a - 1.0)
            closes.append(b)
    return dates, rets, closes


def bill_rate(bars, dates: list[str]) -> tuple[list[float], bool]:
    """Daily cash return for each session, from the prior ^IRX close.

    The yield known at yesterday's close is what cash earns today; using
    the same day's print would be a (tiny) peek.
    """
    if not bars:
        return [0.0] * len(dates), False
    ydates = [b.date for b in bars]
    ylevels = [b.close for b in bars]
    out = []
    for d in dates:
        j = bisect.bisect_left(ydates, d) - 1
        out.append(max(ylevels[j], 0.0) / 100.0 / 252.0 if j >= 0 else 0.0)
    return out, True


def trailing_rv(rets: list[float]) -> list[float | None]:
    """Mean squared return over the prior RV_WINDOW sessions, ending t-1."""
    out: list[float | None] = [None] * len(rets)
    for i in range(RV_WINDOW, len(rets)):
        window = rets[i - RV_WINDOW:i]
        out[i] = sum(r * r for r in window) / RV_WINDOW
    return out


def month_starts(dates: list[str]) -> list[int]:
    return [i for i in range(len(dates))
            if i == 0 or dates[i][:7] != dates[i - 1][:7]]


def expanding_median_weights(signal: list[float | None],
                             starts: list[int]) -> list[float]:
    """w_t = clamp(median(past RV) / RV, 0, 1), median expanding — the
    no-look-ahead scale: the historically typical month runs fully
    invested, only above-typical variance scales down."""
    weights = [1.0] * len(signal)
    seen: list[float] = []
    for k, i in enumerate(starts):
        if signal[i] is not None:
            if len(seen) >= 12:
                c = statistics.median(seen)
                w = max(0.0, min(1.0, c / signal[i]))
            else:
                w = 1.0
            seen.append(signal[i])
        else:
            w = 1.0
        end = starts[k + 1] if k + 1 < len(starts) else len(signal)
        for j in range(i, end):
            weights[j] = w
    return weights


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def monthly_compound(dates: list[str], rets: list[float]) -> list[float]:
    out, cur, month = [], 1.0, None
    for d, r in zip(dates, rets):
        m = d[:7]
        if month is not None and m != month:
            out.append(cur - 1.0)
            cur = 1.0
        month = m
        cur *= 1.0 + r
    out.append(cur - 1.0)
    return out


def spanning_alpha(managed_ex: list[float], market_ex: list[float],
                   lag: int, per_year: int) -> dict:
    """alpha of managed on market excess returns, Newey-West errors."""
    n = len(managed_ex)
    mx, my = statistics.mean(market_ex), statistics.mean(managed_ex)
    varx = sum((x - mx) ** 2 for x in market_ex)
    if varx <= 0 or n < 24:
        return {"n": n}
    beta = sum((x - mx) * (y - my) for x, y in zip(market_ex, managed_ex)) / varx
    z = [y - beta * x for x, y in zip(market_ex, managed_ex)]
    alpha, se, t = newey_west(z, lag)
    return {"n": n, "beta": beta, "alpha_ann": alpha * per_year,
            "se_ann": se * per_year, "t": t, "mde_ann_t2": 2 * se * per_year}


def describe(total: list[float], excess: list[float], per_year: int) -> dict:
    mean, sd = statistics.mean(total), statistics.stdev(total)
    ex_mean, ex_sd = statistics.mean(excess), statistics.stdev(excess)
    wealth, w = [], 1.0
    for r in total:
        w *= 1.0 + r
        wealth.append(w)
    return {"ann_return": mean * per_year, "ann_vol": sd * math.sqrt(per_year),
            "sharpe": (ex_mean / ex_sd * math.sqrt(per_year)) if ex_sd else 0.0,
            "max_drawdown": max_drawdown(wealth)}


def show(label: str, key: str, dates, rets, rf, weights, lag_daily: bool,
         results: list, matched_c: float | None = None):
    """Evaluate one construction over the common span and print it."""
    total = [w * r + (1.0 - w) * f for w, r, f in zip(weights, rets, rf)]
    excess = [t - f for t, f in zip(total, rf)]
    mkt_ex = [r - f for r, f in zip(rets, rf)]

    if lag_daily:
        span = spanning_alpha(excess, mkt_ex, lag=21, per_year=252)
        desc = describe(total, excess, 252)
    else:
        m_man = monthly_compound(dates, total)
        m_mkt = monthly_compound(dates, rets)
        m_rf = monthly_compound(dates, rf)
        span = spanning_alpha([a - c for a, c in zip(m_man, m_rf)],
                              [b - c for b, c in zip(m_mkt, m_rf)],
                              lag=3, per_year=12)
        desc = describe(total, excess, 252)

    turn = sum(abs(weights[i] - weights[i - 1]) for i in range(1, len(weights)))
    ann_turn = turn / (len(weights) / 252.0)
    breakeven = (span.get("alpha_ann", 0.0) / ann_turn) if ann_turn > 0 else None

    print(f"\n{label}")
    print(f"  ann return {desc['ann_return']:+7.2%}   vol {desc['ann_vol']:6.2%}   "
          f"Sharpe {desc['sharpe']:5.2f}   maxDD {desc['max_drawdown']:+.0%}   "
          f"avg weight {statistics.mean(weights):.2f}")
    if "t" in span:
        print(f"  spanning alpha {span['alpha_ann']:+7.2%}/yr   beta {span['beta']:.2f}   "
              f"t = {span['t']:+.2f}   MDE@t2 {span['mde_ann_t2']:.2%}/yr   "
              f"(n={span['n']} {'days' if lag_daily else 'months'})")
    be = f"{breakeven * 100:.2f}%" if breakeven and breakeven > 0 else "n/a (alpha <= 0)"
    print(f"  turnover {ann_turn:.1f}x/yr   breakeven one-way cost {be}")
    if matched_c is not None:
        print(f"  (uncapped weight: mean {statistics.mean(weights):.2f}, "
              f"max {max(weights):.1f} — leverage an actual long-only book cannot take)")
    results.append({"cell": key, **span, "ann_turnover": ann_turn,
                    "breakeven_cost": breakeven, **desc,
                    "avg_weight": statistics.mean(weights)})


# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", default="SPY")
    ap.add_argument("--period", default="max")
    ap.add_argument("--json")
    args = ap.parse_args(argv)

    print(f"fetching {args.symbol} + {BILL} ({args.period})")
    bars, failures = fetch_history([args.symbol, BILL], period=args.period)
    series = bars.get(args.symbol) or []
    if len(series) < 2 * BURN_IN:
        raise SystemExit(f"{args.symbol}: {len(series)} sessions "
                         f"({failures.get(args.symbol, 'too few')}). "
                         f"Rate limited? Wait and retry.")
    dates, rets, closes = simple_returns(series)
    rf, have_rf = bill_rate(bars.get(BILL) or [], dates)
    if not have_rf:
        print(f"  WARNING: {BILL} unavailable — cash earns zero, which "
              f"understates the no-leverage cells in high-rate eras")

    rv = trailing_rv(rets)
    starts = month_starts(dates)

    # Matched-vol weights (paper): c / RV at month starts, c matching
    # full-sample excess vol. c is cosmetic for t(alpha); stated, not hidden.
    raw = [None] * len(rets)
    for k, i in enumerate(starts):
        w = (1.0 / rv[i]) if rv[i] else None
        end = starts[k + 1] if k + 1 < len(starts) else len(rets)
        for j in range(i, end):
            raw[j] = w

    keep = [i for i in range(BURN_IN, len(rets)) if raw[i] is not None]
    d = [dates[i] for i in keep]
    r = [rets[i] for i in keep]
    f = [rf[i] for i in keep]
    raw_k = [raw[i] for i in keep]

    mkt_ex_sd = statistics.stdev([x - y for x, y in zip(r, f)])
    man0 = [w * (x - y) for w, x, y in zip(raw_k, r, f)]
    c = mkt_ex_sd / statistics.stdev(man0)
    w_matched = [c * w for w in raw_k]

    # No-leverage monthly + daily EWMA weights, expanding-median scaled.
    w_nolev_full = expanding_median_weights(rv, starts)
    w_nolev = [w_nolev_full[i] for i in keep]

    ev = ewma_vol_series(closes)
    evar = [None if v is None else v * v for v in ev]
    w_ewma_full = [1.0] * len(rets)
    seen: list[float] = []
    for i in range(len(rets)):
        s = evar[i - 1] if i > 0 else None
        if s is not None:
            if len(seen) >= BURN_IN:
                w_ewma_full[i] = max(0.0, min(1.0, statistics.median(seen) / s))
            seen.append(s)
    w_ewma = [w_ewma_full[i] for i in keep]

    print(f"  {len(d)} sessions evaluated, {d[0]} .. {d[-1]} "
          f"(burn-in {BURN_IN} sessions dropped)")

    results: list[dict] = []
    print("\n" + "=" * 74)
    print(f"BUY AND HOLD — the thing to beat")
    print("=" * 74)
    bh = describe(r, [x - y for x, y in zip(r, f)], 252)
    print(f"  ann return {bh['ann_return']:+7.2%}   vol {bh['ann_vol']:6.2%}   "
          f"Sharpe {bh['sharpe']:5.2f}   maxDD {bh['max_drawdown']:+.0%}")

    print("\n" + "=" * 74)
    print("VOLATILITY-MANAGED")
    print("=" * 74)
    show("Monthly, matched-vol (Moreira-Muir construction, uncapped)",
         "mm_matched", d, r, f, w_matched, False, results, matched_c=c)
    show("Monthly, no leverage (weight in [0,1], cash at bill rate)",
         "mm_nolev", d, r, f, w_nolev, False, results)
    show("Daily EWMA lambda=0.94, no leverage",
         "ewma_nolev", d, r, f, w_ewma, False, results)

    n5 = min(SESSIONS_5Y, len(d))
    show("Monthly, no leverage — last ~5 years only",
         "mm_nolev_5y", d[-n5:], r[-n5:], f[-n5:], w_nolev[-n5:],
         False, results)

    cells = sum(1 for x in results if "t" in x)
    print(f"\nMultiple testing: {cells} alpha cells; Harvey, Liu & Zhu (2016) "
          f"hurdle for a new claim is t>3.")
    print("One market, one path (Cederburg et al. 2020): vol-managed alphas "
          "are fragile out-of-sample across factors; the market factor is "
          "the strongest case and still cannot be settled on one path.")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"symbol": args.symbol, "period": args.period,
             "sessions": len(d), "first": d[0], "last": d[-1],
             "have_bill_rate": have_rf, "buy_hold": bh,
             "cells_tested": cells, "results": results}, indent=2))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
