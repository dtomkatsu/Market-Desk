#!/usr/bin/env python3
"""Does 52-week-high nearness beat momentum on the S&P 500 cross-section?

    python scripts/high52_study.py                  # univariate + double sort
    python scripts/high52_study.py --horizon 63     # 3-month forward window
    python scripts/high52_study.py --skip-month     # nearness measured at t-21
    python scripts/high52_study.py --high-basis close
    python scripts/high52_study.py --json out.json

George & Hwang (2004) report that a stock's *nearness to its own 52-week
high* — price divided by the highest price of the past year — predicts
future returns, and that in a joint test it **subsumes** Jegadeesh-Titman
momentum: control for nearness and the momentum spread loses its bite, but
not the reverse. The reading is anchoring — the 52-week high acts as a
reference level and traders under-react to news that should carry a stock
through it.

Both halves of that claim are tested here: the univariate spread, and the
nested double sort that says which signal survives controlling for the
other.

**The statistics are imported, not reimplemented.** ``summarize`` and
``market_drawdowns`` come from ``momentum_study`` unchanged, so every number
below is produced by the estimator that produced the +0.82%/mo, t=1.55
momentum figure in PREDICTIONS.md. A nearness result is therefore directly
comparable to that one rather than merely resembling it.

Three design choices could each carry the result, so each is a flag:

* **The formation windows are NOT aligned by default.** 12-1 momentum ends
  one month before the sample date, deliberately, because the last month
  mean-reverts. Nearness as George & Hwang define it is measured *at* the
  sample date and therefore includes that month. ``--skip-month`` measures
  it at t-21 instead. If nearness only wins in the unaligned form, what it
  captures is the reversal window, not anchoring.
* **``--high-basis``** picks intraday highs (the paper's construction) or
  closing highs (robust to yfinance's occasional bad intraday print). A
  result that flips between the two is a data artifact, not a finding.
* **Sampling is monthly and the t-test runs across sample dates**, so one
  observation is one cross-sectional spread on one day. That is the
  calendar-time portfolio construction, and it is already the fix for the
  date-clustering that would otherwise inflate t badly on a panel this
  wide — 500 names shocked by one macro print are not 500 observations.
  It only holds while the forward window matches the sampling interval:
  at ``--horizon 63`` on a 21-session grid, consecutive observations share
  two thirds of their returns and t is inflated by about sqrt(3). The run
  prints that warning rather than leaving the reader to notice.

**Multiple testing.** A full run reports six cells. At t=2 about one cell in
twenty clears by chance, so one significant cell out of six is not a
finding. Harvey, Liu & Zhu (2016) argue for a t>3 hurdle on any *new* factor
claim given how thoroughly this literature has been mined; the footer prints
the cell count so the hurdle stays in view.

**Survivorship bias** is inherited unchanged from the pinned constituent
list: today's members, dropped names absent. Read as indicative, not precise.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import warnings
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
warnings.filterwarnings("ignore")

from market_desk.benchmark import load_constituents          # noqa: E402
from market_desk.factors import (                            # noqa: E402
    SESSIONS_1M, SESSIONS_12M, momentum_metrics,
)
from market_desk.fetch import fetch_history                  # noqa: E402

# The estimator, imported so this study and the momentum study are measured
# by identical code. momentum_study guards its entry point, so importing it
# runs nothing.
from momentum_study import (                                 # noqa: E402
    MIN_CROSS_SECTION, SAMPLE_EVERY, market_drawdowns, summarize,
)

# Within a nested sort each outer bucket is split again, so cells run about a
# third the size of a univariate leg. Below this a "controlled spread" is
# noise dressed up as a control.
MIN_CELL = 30


# ---------------------------------------------------------------------------
# Panel — same shape as momentum_study's, plus intraday highs
# ---------------------------------------------------------------------------

def build_panel(period: str = "5y") -> dict[str, dict]:
    symbols = [r["symbol"] for r in load_constituents()]
    if not symbols:
        raise SystemExit("no pinned constituent list; run scripts/refresh_constituents.py")
    print(f"fetching {len(symbols)} constituents ({period})")
    bars, failures = fetch_history(symbols, period=period)
    print(f"  {len(bars)} ok, {len(failures)} failed")
    return {s: {"d": [b.date for b in bs],
                "c": [b.close for b in bs],
                "h": [b.high for b in bs]}
            for s, bs in bars.items()}


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

def nearness_signal(basis: str, skip: int, window: int = SESSIONS_12M):
    """close / max(high over the trailing year), both as of t-`skip`.

    Bounded (0, 1]; 1.0 means the stock is sitting on its own 52-week high.
    """
    key = "h" if basis == "intraday" else "c"

    def signal(series: dict, i: int):
        j = i - skip
        if j < window:
            return None
        peak = max(series[key][j - window + 1: j + 1])
        price = series["c"][j]
        if peak <= 0 or price <= 0:
            return None
        return price / peak

    return signal


def momentum_signal(series: dict, i: int):
    """12-1 formation return — the published construction, unchanged."""
    return momentum_metrics(series["c"][:i + 1]).mom_12_1


# ---------------------------------------------------------------------------
# Sorts
# ---------------------------------------------------------------------------

def _legs(ordered: list[str], forward: dict[str, float], fraction: float):
    k = max(1, int(len(ordered) * fraction))
    winners = statistics.mean(forward[s] for s in ordered[-k:])
    losers = statistics.mean(forward[s] for s in ordered[:k])
    return winners, losers, k


def _cross_section(panel: dict, signals: dict, i_by_symbol: dict,
                   date: str, horizon: int, start: int):
    """Every name's signal values and forward return on one sample date."""
    values = {name: {} for name in signals}
    forward: dict[str, float] = {}
    for symbol, series in panel.items():
        i = i_by_symbol[symbol].get(date)
        if i is None or i < start:
            continue
        got = {}
        for name, fn in signals.items():
            v = fn(series, i)
            if v is None:
                break
            got[name] = v
        else:
            if i + horizon < len(series["d"]):
                a, b = series["c"][i], series["c"][i + horizon]
                if a > 0:
                    forward[symbol] = b / a - 1.0
                    for name, v in got.items():
                        values[name][symbol] = v
    return values, forward


def _sample_dates(panel: dict, horizon: int):
    calendar = max((v["d"] for v in panel.values()), key=len)
    index = {s: {d: i for i, d in enumerate(v["d"])} for s, v in panel.items()}
    start = SESSIONS_12M + SESSIONS_1M
    dates = [calendar[t] for t in range(start, len(calendar) - horizon, SAMPLE_EVERY)]
    return dates, index, start


def run_univariate(panel: dict, signal_fn, fraction: float, horizon: int) -> list[dict]:
    """Top-minus-bottom `fraction` of the panel, sampled ~monthly."""
    dates, index, start = _sample_dates(panel, horizon)
    rows = []
    for date in dates:
        values, forward = _cross_section(panel, {"x": signal_fn}, index,
                                         date, horizon, start)
        common = sorted(values["x"], key=lambda s: values["x"][s])
        if len(common) < MIN_CROSS_SECTION:
            continue
        winners, losers, k = _legs(common, forward, fraction)
        rows.append({"date": date, "spread": winners - losers,
                     "winners": winners, "losers": losers,
                     "n": len(common), "per_leg": k})
    return rows


def run_controlled(panel: dict, outer_fn, inner_fn, horizon: int,
                   fraction: float = 1 / 3) -> list[dict]:
    """Inner signal's spread *within* terciles of the outer signal.

    The subsumption test. Split the cross-section into terciles on the
    control, take the inner signal's top-minus-bottom spread inside each,
    and average the three. A signal that only works because it proxies the
    control collapses here; one with its own information survives.
    """
    dates, index, start = _sample_dates(panel, horizon)
    rows = []
    for date in dates:
        values, forward = _cross_section(panel, {"outer": outer_fn, "inner": inner_fn},
                                         index, date, horizon, start)
        outer, inner = values["outer"], values["inner"]
        common = sorted(outer, key=lambda s: outer[s])
        if len(common) < MIN_CROSS_SECTION:
            continue

        third = len(common) // 3
        buckets = [common[:third], common[third:2 * third], common[2 * third:]]
        spreads, per_leg = [], 0
        for bucket in buckets:
            if len(bucket) < MIN_CELL:
                continue
            ordered = sorted(bucket, key=lambda s: inner[s])
            winners, losers, k = _legs(ordered, forward, fraction)
            spreads.append(winners - losers)
            per_leg = k
        if len(spreads) < 3:
            continue
        rows.append({"date": date, "spread": statistics.mean(spreads),
                     "winners": None, "losers": None,
                     "n": len(common), "per_leg": per_leg})
    return rows


def rank_correlation(panel: dict, fn_a, fn_b, horizon: int) -> tuple[float, int]:
    """Mean Spearman correlation between the two signals across sample dates.

    Two signals correlated at 0.95 have nothing to decompose, and a
    subsumption test on them says more about collinearity than about which
    one carries the information. This is the sanity check that has to be
    read before the controlled spreads.
    """
    dates, index, start = _sample_dates(panel, horizon)
    out = []
    for date in dates:
        values, _ = _cross_section(panel, {"a": fn_a, "b": fn_b},
                                   index, date, horizon, start)
        a, b = values["a"], values["b"]
        common = [s for s in a if s in b]
        if len(common) < MIN_CROSS_SECTION:
            continue
        ra = {s: i for i, s in enumerate(sorted(common, key=lambda s: a[s]))}
        rb = {s: i for i, s in enumerate(sorted(common, key=lambda s: b[s]))}
        try:
            out.append(statistics.correlation([ra[s] for s in common],
                                              [rb[s] for s in common]))
        except statistics.StatisticsError:
            continue
    return (statistics.mean(out) if out else float("nan")), len(out)


# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--period", default="5y")
    ap.add_argument("--horizon", type=int, default=21,
                    help="forward window in sessions (default 21 ~ 1 month)")
    ap.add_argument("--high-basis", choices=("intraday", "close"), default="intraday",
                    help="52-week high from intraday highs (the paper) or closes")
    ap.add_argument("--skip-month", action="store_true",
                    help="measure nearness at t-21, aligning it with 12-1 momentum")
    ap.add_argument("--json", help="write the summary to this path")
    args = ap.parse_args(argv)

    if args.horizon > SAMPLE_EVERY:
        overlap = (args.horizon / SAMPLE_EVERY) ** 0.5
        print(f"WARNING: a {args.horizon}-session forward window sampled every "
              f"{SAMPLE_EVERY} sessions overlaps {args.horizon / SAMPLE_EVERY:.1f} "
              f"deep. Consecutive observations share returns, so they are not "
              f"independent and every t below is inflated by roughly "
              f"{overlap:.2f}x. Divide before reading them, or use --horizon "
              f"{SAMPLE_EVERY} for a clean non-overlapping test.\n")

    skip = SESSIONS_1M if args.skip_month else 0
    near = nearness_signal(args.high_basis, skip)
    alignment = ("nearness at t-21 (aligned with 12-1 momentum)" if skip
                 else "nearness at t (George & Hwang construction)")
    print(f"52-week high: {args.high_basis} highs, {alignment}, "
          f"forward horizon {args.horizon} sessions")

    panel = build_panel(args.period)
    drawdowns = market_drawdowns(args.period)

    rho, rho_n = rank_correlation(panel, near, momentum_signal, args.horizon)
    print(f"\nSpearman rank correlation, nearness vs 12-1 momentum: "
          f"{rho:+.2f}  (mean over {rho_n} sample dates)")
    if rho > 0.9:
        print("  NOTE: above 0.9 these are nearly the same sort. The controlled "
              "spreads below measure collinearity more than information.")

    results = []
    print("\n" + "=" * 68)
    print("UNIVARIATE — nearness alone")
    print("=" * 68)
    for fraction, label in ((1 / 3, "Nearness terciles"), (0.10, "Nearness deciles")):
        results.append(summarize(run_univariate(panel, near, fraction, args.horizon),
                                 label, drawdowns))

    print("\n" + "=" * 68)
    print("UNIVARIATE — momentum alone, same estimator, for comparison")
    print("=" * 68)
    for fraction, label in ((1 / 3, "Momentum terciles"), (0.10, "Momentum deciles")):
        results.append(summarize(run_univariate(panel, momentum_signal, fraction, args.horizon),
                                 label, drawdowns))

    print("\n" + "=" * 68)
    print("NESTED DOUBLE SORT — which signal survives controlling for the other")
    print("=" * 68)
    results.append(summarize(
        run_controlled(panel, momentum_signal, near, args.horizon),
        "Nearness within momentum terciles", drawdowns))
    results.append(summarize(
        run_controlled(panel, near, momentum_signal, args.horizon),
        "Momentum within nearness terciles", drawdowns))

    cells = len(results)
    print(f"\nMultiple testing: {cells} cells reported. At t=2 roughly one in "
          f"twenty clears by chance, so ~{cells * 0.05:.1f} false positives are "
          f"expected here. A single significant cell is not a finding; Harvey, "
          f"Liu & Zhu (2016) put the hurdle for a new factor claim at t>3.")
    print("\nSurvivorship: the constituent list is today's members. Dropped "
          "names (usually poor performers) are absent, lifting the loser leg; "
          "recently added names are present, having qualified after strong "
          "runs. Net direction unknown — read as indicative, not precise.")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"horizon_days": args.horizon, "period": args.period,
             "high_basis": args.high_basis, "skip_month": bool(skip),
             "rank_correlation": rho, "rank_correlation_dates": rho_n,
             "cells_tested": cells, "results": results}, indent=2))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
