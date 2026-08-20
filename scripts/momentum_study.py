#!/usr/bin/env python3
"""Does momentum actually work on the S&P 500 cross-section? Measure it.

    python scripts/momentum_study.py                  # terciles + deciles
    python scripts/momentum_study.py --horizon 63     # 3-month forward window
    python scripts/momentum_study.py --json out.json

At each monthly sample date, rank every constituent by its 12-1 formation
return computed from prices **up to that date only**, split into terciles (and
deciles), and measure the forward return of the top group minus the bottom.
The output is the mean monthly spread with its standard error, t-statistic and
hit rate — everything needed to judge whether the result is distinguishable
from luck, rather than just its sign.

Why this exists as its own script: the same test on the ~26-name watchlist had
a standard error of 1.47%/month, meaning it could not have detected a ~1%/month
effect even if one were fully present. Its "coin flip" result was therefore
uninformative rather than negative. Re-running on ~500 names is the only way to
tell those two apart.

**Survivorship bias, unavoidable and undirected.** The constituent list is
today's members. Companies dropped from the index over the sample (typically
poor performers) are absent, which lifts the loser leg and *understates* the
spread; companies recently added (typically after strong runs) are present,
which can *overstate* it. Point-in-time membership is a paid dataset, so the
net direction here is genuinely unknown and the result should be read as
indicative, not precise.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import warnings
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
warnings.filterwarnings("ignore")

from market_desk.benchmark import load_constituents          # noqa: E402
from market_desk.factors import (                            # noqa: E402
    SESSIONS_1M, SESSIONS_12M, momentum_metrics,
)
from market_desk.fetch import fetch_history                  # noqa: E402

# A cross-section this size is the whole point; below it the sample date is
# skipped rather than ranked on a handful of names.
MIN_CROSS_SECTION = 100
SAMPLE_EVERY = 21          # ~monthly
BENCHMARK_ETF = "SPY"      # market series for drawdown conditioning


def build_panel(period: str = "5y") -> dict[str, dict]:
    symbols = [r["symbol"] for r in load_constituents()]
    if not symbols:
        raise SystemExit("no pinned constituent list; run scripts/refresh_constituents.py")
    print(f"fetching {len(symbols)} constituents ({period})")
    bars, failures = fetch_history(symbols, period=period)
    print(f"  {len(bars)} ok, {len(failures)} failed")
    return {s: {"d": [b.date for b in bs], "c": [b.close for b in bs]}
            for s, bs in bars.items()}


def market_drawdowns(period: str = "5y") -> dict[str, float]:
    bars, _ = fetch_history([BENCHMARK_ETF], period=period)
    series = bars.get(BENCHMARK_ETF) or []
    out, peak = {}, 0.0
    for b in series:
        peak = max(peak, b.close)
        out[b.date] = b.close / peak - 1.0 if peak else 0.0
    return out


def run(panel: dict[str, dict], fraction: float, horizon: int) -> list[dict]:
    """One pass: spread between the top and bottom `fraction` of the panel."""
    calendar = max((v["d"] for v in panel.values()), key=len)
    index = {s: {d: i for i, d in enumerate(v["d"])} for s, v in panel.items()}
    start = SESSIONS_12M + SESSIONS_1M

    rows = []
    for t in range(start, len(calendar) - horizon, SAMPLE_EVERY):
        date = calendar[t]
        momentum, forward = {}, {}
        for symbol, series in panel.items():
            i = index[symbol].get(date)
            if i is None or i < start:
                continue
            m = momentum_metrics(series["c"][:i + 1]).mom_12_1
            if m is None:
                continue
            momentum[symbol] = m
            if i + horizon < len(series["d"]):
                a, b = series["c"][i], series["c"][i + horizon]
                if a > 0:
                    forward[symbol] = b / a - 1.0

        common = [s for s in momentum if s in forward]
        if len(common) < MIN_CROSS_SECTION:
            continue
        ordered = sorted(common, key=lambda s: momentum[s])
        k = max(1, int(len(ordered) * fraction))
        winners = statistics.mean(forward[s] for s in ordered[-k:])
        losers = statistics.mean(forward[s] for s in ordered[:k])
        rows.append({"date": date, "spread": winners - losers,
                     "winners": winners, "losers": losers,
                     "n": len(ordered), "per_leg": k})
    return rows


def summarize(rows: list[dict], label: str, drawdowns: dict[str, float]) -> dict:
    spreads = [r["spread"] for r in rows]
    n = len(spreads)
    if n < 3:
        print(f"{label}: too few observations ({n})")
        return {"label": label, "n": n}

    mean = statistics.mean(spreads)
    sd = statistics.stdev(spreads)
    se = sd / math.sqrt(n)
    t = mean / se if se else 0.0
    positive = sum(1 for s in spreads if s > 0)

    print(f"\n{label}: n={n} monthly observations, ~{rows[0]['per_leg']} names "
          f"per leg of {rows[0]['n']}")
    print(f"  mean spread   {mean * 100:+.2f}%/mo   (annualized {((1 + mean) ** 12 - 1) * 100:+.1f}%)")
    print(f"  sd {sd * 100:.2f}%   SE {se * 100:.2f}%   t = {t:+.2f}")
    print(f"  positive months {positive}/{n} ({positive / n * 100:.0f}%)")
    print(f"  smallest effect detectable at t=2: {2 * se * 100:.2f}%/mo")
    if abs(t) < 2:
        needed = math.ceil((2 * sd / abs(mean)) ** 2) if mean else 0
        print(f"  NOT significant at t=2. At this effect size that would need "
              f"~{needed} observations (~{needed / 12:.1f} years); have {n}.")

    buckets = {}
    for name, test in (("near highs (>-5%)", lambda d: d > -0.05),
                       ("moderate (-5..-15%)", lambda d: -0.15 <= d <= -0.05),
                       ("deep (<-15%)", lambda d: d < -0.15)):
        sub = [r["spread"] for r in rows if test(drawdowns.get(r["date"], 0.0))]
        if sub:
            buckets[name] = {"n": len(sub), "mean": statistics.mean(sub),
                             "positive": sum(1 for s in sub if s > 0)}
            flag = "" if len(sub) >= 25 else "   (too few to read as a result)"
            print(f"    {name:22} n={len(sub):3}  mean {statistics.mean(sub) * 100:+6.2f}%{flag}")

    return {"label": label, "n": n, "mean": mean, "sd": sd, "se": se, "t": t,
            "positive": positive, "per_leg": rows[0]["per_leg"],
            "universe": rows[0]["n"], "buckets": buckets}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--period", default="5y")
    ap.add_argument("--horizon", type=int, default=21,
                    help="forward window in sessions (default 21 ≈ 1 month)")
    ap.add_argument("--json", help="write the summary to this path")
    args = ap.parse_args(argv)

    panel = build_panel(args.period)
    drawdowns = market_drawdowns(args.period)

    results = []
    for fraction, label in ((1 / 3, "Terciles"), (0.10, "Deciles")):
        rows = run(panel, fraction, args.horizon)
        results.append(summarize(rows, label, drawdowns))

    print("\nSurvivorship: the constituent list is today's members. Dropped "
          "names (usually poor performers) are absent, lifting the loser leg; "
          "recently added names are present, having qualified after strong "
          "runs. Net direction unknown — read as indicative, not precise.")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"horizon_days": args.horizon, "period": args.period,
             "results": results}, indent=2))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
