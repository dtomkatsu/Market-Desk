#!/usr/bin/env python3
"""Do expected-range bands that widen through earnings deserve to ship?

    python scripts/event_range_study.py                 # tracked companies
    python scripts/event_range_study.py --universe sp500

The dashboard's expected-range band is volatility-only. A band spanning an
earnings date ignores the best-measured thing this repo knows about that
week — the name's own reaction amplification. The fix is arithmetic
(``horizon_variance_factor``: a reaction session contributes a^2 units of
variance), but arithmetic does not decide whether it should ship.
Walk-forward coverage does, and that is what this script measures:

  For every non-overlapping 5-session window in each name's history, build
  BOTH bands from data available at the window's start — same volatility,
  same calibrated multiplier, the only difference being the event term,
  whose amplification is itself estimated walk-forward from past events
  only (>= 4 of them, else the arms are identical and the window is not
  compared). Then check whether the realized 5-session move stayed inside.

The claim being tested is specific: on EVENT windows, flat bands should
under-cover (they ignore known extra variance) and event-aware bands
should sit closer to nominal. Windows without events are the control — the
two arms are identical there by construction, and that identity is
asserted, not assumed.

Power: one name contributes ~15-19 usable event windows, so per-name
verdicts are meaningless and are not printed. Pooled across a universe the
binomial SE is a percent or two, which resolves the shortfalls that
matter. Fisher's rule everywhere else in this repo applies here too: the
width increase is reported next to the coverage gain, because any band can
reach 100% coverage by being useless.
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
sys.path.insert(0, str(REPO_ROOT / "scripts"))
warnings.filterwarnings("ignore")

from market_desk.benchmark import load_constituents      # noqa: E402
from market_desk.config import load_universe             # noqa: E402
from market_desk.fetch import fetch_history              # noqa: E402
from market_desk.predictions import (                    # noqa: E402
    GAUSSIAN_80, event_aware_half_width, walkforward_amplification,
)
from market_desk.volatility import (                     # noqa: E402
    calibrate_multiplier, ewma_vol_series,
)

from shock_study import load_earnings                    # noqa: E402
from swing_forecast import abs_moves, earnings_sessions  # noqa: E402

HORIZON = 5
MIN_HISTORY = 300


def walk(symbol: str, bars, stamps: list[str]) -> dict:
    dates = [b.date for b in bars]
    closes = [b.close for b in bars]
    moves = abs_moves(bars)
    reactions = earnings_sessions(dates, stamps)
    vol = ewma_vol_series(closes)
    m = calibrate_multiplier(closes, HORIZON) or GAUSSIAN_80

    out = {"event": {"flat": [], "aware": [], "widths": []},
           "quiet": {"flat": []}}
    for t in range(MIN_HISTORY, len(dates) - HORIZON, HORIZON):
        v = vol[t]
        if v is None or closes[t] <= 0:
            continue
        window = dates[t + 1: t + 1 + HORIZON]
        n_ev = sum(1 for d in window if d in reactions)
        realized = abs(math.log(closes[t + HORIZON] / closes[t]))
        flat = event_aware_half_width(v, HORIZON, m)
        if n_ev == 0:
            out["quiet"]["flat"].append(realized <= flat)
            continue
        amp = walkforward_amplification(moves, reactions, before=dates[t])
        if amp is None:
            continue                       # arms identical; nothing to compare
        aware = event_aware_half_width(v, HORIZON, m, [amp] * n_ev)
        out["event"]["flat"].append(realized <= flat)
        out["event"]["aware"].append(realized <= aware)
        out["event"]["widths"].append(aware / flat)
    return out


def pool(label: str, hits: list[bool]) -> dict:
    n = len(hits)
    if n == 0:
        return {"n": 0}
    p = sum(hits) / n
    se = math.sqrt(p * (1 - p) / n)
    print(f"  {label:34} {p * 100:5.1f}%  (n={n}, SE {se * 100:.1f}pp)")
    return {"n": n, "coverage": p, "se": se}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--universe", choices=("tracked", "sp500", "sp600"),
                    default="tracked")
    ap.add_argument("--period", default="5y")
    ap.add_argument("--json")
    args = ap.parse_args(argv)

    if args.universe == "tracked":
        symbols = list(load_universe().symbols)
    else:
        symbols = [r["symbol"] for r in load_constituents(
            REPO_ROOT / "config" / "benchmark" / f"{args.universe}.csv")]

    print(f"{args.universe}: {len(symbols)} symbols ({args.period})")
    bars, failures = fetch_history(symbols, args.period)
    print(f"  {len(bars)} ok, {len(failures)} failed")
    earnings = load_earnings(sorted(bars), False)

    agg = {"event_flat": [], "event_aware": [], "quiet_flat": [], "widths": []}
    used = 0
    for s, bs in bars.items():
        stamps = earnings.get(s) or []
        if not stamps or len(bs) < MIN_HISTORY + HORIZON + 10:
            continue
        r = walk(s, bs, stamps)
        if r["event"]["flat"]:
            used += 1
        agg["event_flat"] += r["event"]["flat"]
        agg["event_aware"] += r["event"]["aware"]
        agg["quiet_flat"] += r["quiet"]["flat"]
        agg["widths"] += r["event"]["widths"]

    print(f"\n{used} names contributed event windows; nominal coverage 80%")
    print("-" * 60)
    res = {
        "event_flat": pool("event windows, flat band", agg["event_flat"]),
        "event_aware": pool("event windows, event-aware band", agg["event_aware"]),
        "quiet": pool("quiet windows (control, arms identical)",
                      agg["quiet_flat"]),
    }
    if agg["widths"]:
        w = statistics.median(agg["widths"])
        print(f"  {'median width increase on event windows':34} "
              f"{(w - 1) * 100:+5.1f}%")
        res["median_width_ratio"] = w

    ef, ea = res["event_flat"], res["event_aware"]
    if ef.get("n") and ea.get("n"):
        diff = ea["coverage"] - ef["coverage"]
        se = math.sqrt(ef["se"] ** 2 + ea["se"] ** 2)   # conservative: paired
        print(f"\n  coverage gained by the event term: {diff * 100:+.1f}pp "
              f"(unpaired SE {se * 100:.1f}pp; the arms share windows, so the "
              f"paired SE is smaller — this understates significance)")
        gap_flat = 0.80 - ef["coverage"]
        gap_aware = 0.80 - ea["coverage"]
        print(f"  distance from nominal: flat {gap_flat * 100:+.1f}pp, "
              f"aware {gap_aware * 100:+.1f}pp")
        res["coverage_gain"] = diff

    print("\nShip test: the event term earns its width if it moves event-window "
          "coverage materially toward nominal without overshooting it.")
    if args.json:
        Path(args.json).write_text(json.dumps(
            {"universe": args.universe, "horizon": HORIZON, **res}, indent=2))
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
