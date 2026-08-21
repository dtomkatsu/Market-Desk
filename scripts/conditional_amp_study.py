#!/usr/bin/env python3
"""Does the pre-event volatility state predict how big the reaction is?

    python scripts/conditional_amp_study.py             # both universes

Per-name earnings amplification is currently unconditional — a median over
~20 events. Per name, conditioning is hopeless (20 events cannot support
it). Pooled it is not: ~1,100 names x ~19 reactions gives thousands of
events, each normalized by its OWN name's ordinary-day median so a
volatile biotech and a staid utility land on the same scale.

The question: a name about to report while already in the top third of
its own trailing volatility — does it react bigger than the same name
reporting from a quiet state? If yes, the event-aware bands
(event_range_study.py) get state-conditional widths for free; if no, the
unconditional median stands and the extra knob is refused.

Method:

* State = percentile of EWMA vol at the session BEFORE the reaction,
  within the name's trailing year — the same self-relative construction
  classify_regime uses, measured strictly pre-event.
* Response = log(|reaction move| / name's ordinary median), pooled.
* Statistics are clustered by event DATE: earnings cluster in weeks, and
  one macro-shocked reporting day is one observation, not eighty.
* The top-vs-bottom tercile difference is the headline; the tercile means
  are printed so monotonicity can be seen rather than asserted.

The mechanical caution: EWMA vol is a |move| average, so a name whose
recent sessions were large is in a high state partly by construction. The
response uses the FULL-history median as its denominator, not the recent
window, so the state and the normalizer share no sessions... except the
trailing year's, which both touch. The clean check is the quiet-state
tercile: if conditioning were pure artifact, quiet states would predict
SMALL reactions with the same strength that turbulent ones predict large —
symmetric attenuation, which the printed terciles expose either way.
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
from market_desk.fetch import fetch_history              # noqa: E402
from market_desk.volatility import ewma_vol_series       # noqa: E402

from shock_study import clustered, load_earnings         # noqa: E402
from swing_forecast import abs_moves, earnings_sessions  # noqa: E402

MIN_ORDINARY = 250
TRAIL = 252


def collect(universe: str, period: str) -> list[dict]:
    symbols = [r["symbol"] for r in load_constituents(
        REPO_ROOT / "config" / "benchmark" / f"{universe}.csv")]
    print(f"{universe}: fetching {len(symbols)} symbols ({period})")
    bars, failures = fetch_history(symbols, period)
    print(f"  {len(bars)} ok, {len(failures)} failed")
    earnings = load_earnings(sorted(bars), False)

    events = []
    for symbol, series in bars.items():
        stamps = earnings.get(symbol) or []
        if not stamps:
            continue
        dates = [b.date for b in series]
        closes = [b.close for b in series]
        moves = abs_moves(series)
        reactions = earnings_sessions(dates, stamps)
        ordinary = [v for d, v in moves.items() if d not in reactions]
        if len(ordinary) < MIN_ORDINARY:
            continue
        base = statistics.median(ordinary)
        if base <= 0:
            continue
        vol = ewma_vol_series(closes)
        pos = {d: i for i, d in enumerate(dates)}
        for d in sorted(reactions):
            i = pos.get(d)
            if i is None or i < TRAIL + 1 or d not in moves:
                continue
            v = vol[i - 1]
            hist = [x for x in vol[i - 1 - TRAIL:i] if x is not None]
            if v is None or len(hist) < 100:
                continue
            pctl = sum(1 for x in hist if x <= v) / len(hist)
            if moves[d] <= 0:
                continue
            events.append({"symbol": symbol, "date": d, "pctl": pctl,
                           "logratio": math.log(moves[d] / base)})
    return events


def summarize(label: str, events: list[dict]) -> dict:
    if len(events) < 200:
        print(f"\n{label}: too few events ({len(events)})")
        return {"label": label, "n": len(events)}
    print(f"\n{label} — {len(events)} reactions, "
          f"{len({e['symbol'] for e in events})} names")
    print(f"  {'pre-event vol state':26} {'n':>6} {'median amp':>11} "
          f"{'mean log-ratio':>15} {'t(cl)':>7}")
    cells = {}
    for name, lo, hi in (("quiet third (<=33rd pctl)", 0.0, 1 / 3),
                         ("middle third", 1 / 3, 2 / 3),
                         ("turbulent third (>=67th)", 2 / 3, 1.01)):
        sub = [e for e in events if lo <= e["pctl"] < hi]
        by_date = defaultdict(list)
        for e in sub:
            by_date[e["date"]].append(e["logratio"])
        mean, se, t, n_dates = clustered(by_date)
        med_amp = statistics.median(math.exp(e["logratio"]) for e in sub)
        print(f"  {name:26} {len(sub):>6} {med_amp:>10.2f}x "
              f"{mean:>+15.3f} {t:>+7.2f}")
        cells[name.split()[0]] = {"n": len(sub), "median_amp": med_amp,
                                  "mean_logratio": mean, "se": se,
                                  "dates": n_dates}

    top, bot = cells.get("turbulent"), cells.get("quiet")
    if top and bot and top["se"] and bot["se"]:
        diff = top["mean_logratio"] - bot["mean_logratio"]
        se = math.sqrt(top["se"] ** 2 + bot["se"] ** 2)
        t = diff / se if se else 0.0
        print(f"  {'turbulent minus quiet':26} {'':>6} "
              f"{math.exp(diff):>10.2f}x {diff:>+15.3f} {t:>+7.2f}   "
              f"MDE@t2 {2 * se:+.3f}")
        return {"label": label, "n": len(events), "cells": cells,
                "diff_logratio": diff, "t": t, "mde_t2": 2 * se}
    return {"label": label, "n": len(events), "cells": cells}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--universes", default="sp500,sp600")
    ap.add_argument("--period", default="5y")
    ap.add_argument("--json")
    args = ap.parse_args(argv)

    results, pooled = [], []
    for u in args.universes.split(","):
        ev = collect(u.strip(), args.period)
        pooled += ev
        results.append(summarize(u.strip(), ev))
    if len(args.universes.split(",")) > 1:
        results.append(summarize("pooled", pooled))

    print("\nReading: a positive turbulent-minus-quiet difference means a name "
          "reporting from an unsettled state reacts bigger than the same "
          "name's unconditional median — grounds for state-conditional band "
          "widths. Symmetric shrinkage in both outer terciles would instead "
          "be the estimator-overlap artifact the docstring describes.")
    print("Multiple testing: 3 difference cells (one per universe + pooled).")
    print("Tracker context, not trading advice.")
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2))
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
