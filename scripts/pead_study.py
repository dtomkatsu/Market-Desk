#!/usr/bin/env python3
"""Post-earnings-announcement drift, sorted on ACTUAL surprise. The last
directional candidate anywhere in this repo's reach.

    python scripts/fetch_surprises.py sp500 sp600     # once, ~20 min
    python scripts/pead_study.py                      # the battery
    python scripts/pead_study.py --universes sp600

Every directional test in the sweep came back empty, but none of them
tested Bernard & Thomas (1989): sort on EARNINGS SURPRISE, measure the
drift after the announcement. The shock study sorted on price reaction —
a weak proxy — and deliberately blacked earnings out; the insider and
calendar studies never touched surprise. The scope probe confirmed Yahoo
carries `Surprise(%)` for 22-24 past events per name, small caps
included, which makes the real construction buildable at ~17k events.

THE PRE-REGISTERED BATTERY (from PREDICTIVE_ANALYSIS_SCOPE.md, frozen
before this script first ran on real data):

* standardized surprise = cross-sectional percentile of Surprise(%)
  within (universe, calendar quarter of the reaction session) — ranks,
  because a raw surprise percent explodes when the estimate is near zero;
* terciles AND deciles, top minus bottom;
* horizons 5, 21, 63 sessions;
* entry lags 1 and 2 (lag 2 sheds the bid-ask bounce; a result that dies
  at lag 2 was microstructure);
* both universes separately — the literature concentrates what remains
  of PEAD in small caps;
* market-model residuals against the universe's own ETF, alpha feedback
  excluded, calendar-time daily spreads, names deduped, Newey-West at
  the horizon lag. Every estimator lesson from the sweep, inherited.

That is 24 spread cells. At t=2 roughly 1.2 clear by chance; the cells
are siblings, not independent discoveries, and the read is the PATTERN —
sign agreement across horizon, lag, sort and universe — plus the
Harvey-Liu-Zhu t>3 hurdle on anything called a finding. Reaction-day
returns are NOT part of any cell: day 0 is the announcement being priced,
and the only claim under test is what happens afterwards.

Survivorship is the standing caveat and points the usual way (worse on
the S&P 600); Yahoo's consensus is retail-grade and its estimate
revisions are invisible, which adds noise to the sort and biases toward
the null, not away.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import warnings
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
warnings.filterwarnings("ignore")

from market_desk.benchmark import load_constituents      # noqa: E402
from market_desk.catalysts import announcement_date      # noqa: E402
from market_desk.fetch import fetch_history              # noqa: E402

from insider_study import UNIVERSES, universe_series     # noqa: E402
from shock_study import calendar_time, newey_west, residual_returns  # noqa: E402

SURPRISES = REPO_ROOT / ".cache" / "earnings_surprise.json"
MIN_QUARTER = 30        # a 3-name quarter is not a cross-section
HORIZONS = (5, 21, 63)
LAGS = (1, 2)


def reaction_index(dates: list[str], stamp: str) -> int | None:
    day, after_close = announcement_date(stamp)
    later = [i for i, d in enumerate(dates)
             if (d > day if after_close else d >= day)]
    return later[0] if later else None


def build_events(universe: str, panel_dates: dict[str, list[str]],
                 surprises: dict) -> list[dict]:
    raw = []
    for symbol, dates in panel_dates.items():
        seen = set()
        for row in surprises.get(symbol) or []:
            if row.get("spct") is None:
                continue
            i = reaction_index(dates, row["ts"])
            if i is None or i < 260 or (symbol, i) in seen:
                continue
            seen.add((symbol, i))
            raw.append({"symbol": symbol, "i": i, "date": dates[i],
                        "spct": row["spct"],
                        "quarter": dates[i][:4] + "Q" + str((int(dates[i][5:7]) + 2) // 3)})
    by_q = defaultdict(list)
    for e in raw:
        by_q[e["quarter"]].append(e)
    out = []
    for q, evs in by_q.items():
        if len(evs) < MIN_QUARTER:
            continue
        ranked = sorted(evs, key=lambda e: e["spct"])
        n = len(ranked)
        for k, e in enumerate(ranked):
            e["srank"] = k / (n - 1)
            out.append(e)
    return out


def run_universe(universe: str, results: list[dict]) -> None:
    spec = UNIVERSES[universe]
    symbols = [r["symbol"] for r in load_constituents(
        REPO_ROOT / "config" / "benchmark" / spec["pin"])]
    print(f"\n{'=' * 74}\n{spec['label']} — residualized against "
          f"{spec['market']}\n{'=' * 74}")
    bars, failures = fetch_history(symbols + [spec["market"]], "5y")
    print(f"  {len(bars)} ok, {len(failures)} failed")
    market_bars = bars.pop(spec["market"], [])
    market = {}
    for i in range(1, len(market_bars)):
        p, c = market_bars[i - 1].close, market_bars[i].close
        if p > 0 and c > 0:
            market[market_bars[i].date] = math.log(c / p)

    panel_dates = {s: [b.date for b in bs] for s, bs in bars.items()}
    resid = {s: residual_returns(panel_dates[s], [b.close for b in bs],
                                 market, True, False)
             for s, bs in bars.items()}
    surprises = json.loads(SURPRISES.read_text())
    events = build_events(universe, panel_dates, surprises)
    quarters = len({e["quarter"] for e in events})
    print(f"  {len(events)} surprise-ranked reactions across {quarters} "
          f"quarters, {len({e['symbol'] for e in events})} names")

    for sort_name, lo, hi in (("terciles", 1 / 3, 2 / 3),
                              ("deciles", 0.10, 0.90)):
        top = [{**e, "up": True} for e in events if e["srank"] >= hi]
        bot = [{**e, "up": False} for e in events if e["srank"] <= lo]
        print(f"\n  {sort_name}: {len(top)} top / {len(bot)} bottom")
        print(f"  {'lag':>4} {'horizon':>8} | {'daily spread':>13} "
              f"{'~CAR':>8} {'t(NW)':>7} {'MDE@t2/d':>9} {'dates':>6}")
        for lag in LAGS:
            for horizon in HORIZONS:
                spread, breadth = calendar_time(top + bot, resid,
                                                panel_dates, horizon, lag)
                mean, se, t = newey_west(spread, horizon)
                print(f"  {lag:>4} {horizon:>8} | {mean * 100:>+12.3f}% "
                      f"{mean * horizon * 100:>+7.2f}% {t:>+7.2f} "
                      f"{2 * se * 100:>8.3f}% {len(spread):>6}")
                results.append({
                    "universe": universe, "sort": sort_name, "lag": lag,
                    "horizon": horizon, "daily_spread": mean, "t": t,
                    "mde_daily_t2": 2 * se, "dates": len(spread),
                    "n_top": len(top), "n_bottom": len(bot)})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--universes", default="sp500,sp600")
    ap.add_argument("--json")
    args = ap.parse_args(argv)
    if not SURPRISES.exists():
        raise SystemExit("no surprise cache; run scripts/fetch_surprises.py first")

    results: list[dict] = []
    for u in args.universes.split(","):
        run_universe(u.strip(), results)

    cells = len(results)
    sig2 = [r for r in results if abs(r["t"]) >= 2]
    sig3 = [r for r in results if abs(r["t"]) >= 3]
    pos = sum(1 for r in results if r["daily_spread"] > 0)
    print(f"\n{'=' * 74}")
    print(f"BATTERY: {cells} pre-registered cells; {pos} positive-signed; "
          f"{len(sig2)} at |t|>=2 (~{cells * 0.05:.1f} expected by chance); "
          f"{len(sig3)} at the Harvey-Liu-Zhu t>3 hurdle.")
    print("Read the pattern, not a cell: PEAD predicts positive spreads "
          "concentrated in small caps, fading with horizon, surviving lag 2.")
    print("Survivorship: pinned lists, today's members — worse on the 600. "
          "Yahoo's consensus is retail-grade; sort noise biases toward the "
          "null.")
    print("Tracker context, not trading advice.")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"cells": cells, "results": results}, indent=2))
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
