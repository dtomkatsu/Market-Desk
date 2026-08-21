#!/usr/bin/env python3
"""Is the insider day-1 repricing a gap, or tradeable after the open?

    python scripts/insider_gap_study.py                 # both universes
    python scripts/insider_gap_study.py --min-dollars 25000

The small-cap insider study found the entire purchase effect concentrated
on day 1 after the filing (+1.14%, t=+16.07) with zero drift afterwards —
a disclosure repricing, not a signal. That leaves exactly one open
question, and it decides whether the finding is unexploitable or only
mostly: **where inside day 1 does the repricing happen?**

Day 1's close-to-close return spans two very different windows:

* the **overnight gap** (prior close -> open), which contains the
  after-hours stretch where most Form 4s are accepted. Nobody trades this
  without watching EDGAR in real time;
* the **open-to-close** stretch, which anyone can trade at the next open
  with no infrastructure at all.

If the repricing is all gap, the finding closes honestly and permanently.
If open-to-close is materially positive, a same-day filing monitor becomes
a real design question — and only then.

Method notes:

* Both legs are adjusted by the universe ETF's SAME-session gap and
  intraday move, so a market-wide gap day does not masquerade as insider
  information. The equal-weight tilt measured in the insider study is
  -0.016%/day — two orders of magnitude below the +1.14% being
  decomposed — so the ETF adjustment is sufficient here.
* Statistics are clustered by DATE (the shock study's estimator): filings
  arrive in bursts, and one heavy filing day is one observation, not
  thirty.
* ``fetch.py`` flattens a missing open to the close (stale-print
  handling), which zeroes that session's intraday leg rather than
  inventing one; sessions where open == close on nonzero range are
  counted and reported.
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

from market_desk.fetch import fetch_history              # noqa: E402

from insider_study import UNIVERSES, anchor, norm_symbol  # noqa: E402
from market_desk.benchmark import load_constituents      # noqa: E402
from shock_study import clustered                        # noqa: E402


def decompose(bars) -> tuple[dict, dict]:
    """Per-session overnight gap and open-to-close, as log returns."""
    gap, intra = {}, {}
    for i in range(1, len(bars)):
        prev, cur = bars[i - 1], bars[i]
        if prev.close > 0 and cur.open > 0 and cur.close > 0:
            gap[cur.date] = math.log(cur.open / prev.close)
            intra[cur.date] = math.log(cur.close / cur.open)
    return gap, intra


def run_universe(universe: str, min_dollars: float) -> list[dict]:
    spec = UNIVERSES[universe]
    pin = REPO_ROOT / "config" / "benchmark" / spec["pin"]
    cache = REPO_ROOT / ".cache" / f"insider_events_{universe}.json"
    if not cache.exists():
        raise SystemExit(f"no insider event cache for {universe}; run "
                         f"insider_study.py --universe {universe} --zips DIR")
    filings = json.loads(cache.read_text())
    symbols = [r["symbol"] for r in load_constituents(pin)]

    print(f"\n{'=' * 74}\n{spec['label']} — {len(filings)} cached filings, "
          f"market {spec['market']}\n{'=' * 74}")
    bars, _ = fetch_history(symbols + [spec["market"]], "5y")
    mkt_gap, mkt_intra = decompose(bars.pop(spec["market"], []))
    panel_dates = {s: [b.date for b in bs] for s, bs in bars.items()}
    gaps, intras = {}, {}
    for s, bs in bars.items():
        gaps[s], intras[s] = decompose(bs)

    sides = anchor(filings, panel_dates, min_dollars)
    results = []
    for side, label in (("P", "purchases"), ("S", "sales")):
        events = sides.get(side, [])
        by_date_gap: dict[str, dict] = defaultdict(dict)
        by_date_intra: dict[str, dict] = defaultdict(dict)
        flat_opens = 0
        for ev in events:
            ds = panel_dates[ev["symbol"]]
            j = ev["i"] + 1                       # day 1 after the filing session
            if j >= len(ds):
                continue
            d = ds[j]
            g, x = gaps[ev["symbol"]].get(d), intras[ev["symbol"]].get(d)
            if g is None or x is None or d not in mkt_gap:
                continue
            if x == 0.0:
                flat_opens += 1
            # one NAME once per date (the insider-study lesson)
            by_date_gap[d][ev["symbol"]] = g - mkt_gap[d]
            by_date_intra[d][ev["symbol"]] = x - mkt_intra[d]

        cells = {}
        for name, series in (("overnight gap", by_date_gap),
                             ("open-to-close", by_date_intra)):
            flat = {d: list(v.values()) for d, v in series.items()}
            mean, se, t, n_dates = clustered(flat)
            cells[name] = {"mean": mean, "t": t, "dates": n_dates,
                           "mde_t2": 2 * se}
            print(f"  {label:10} {name:14} {mean * 100:+7.3f}%   "
                  f"t = {t:+6.2f}   ({n_dates} event dates, "
                  f"MDE@t2 {2 * se * 100:.3f}%)")
        total = cells["overnight gap"]["mean"] + cells["open-to-close"]["mean"]
        share = (cells["overnight gap"]["mean"] / total) if total else float("nan")
        print(f"  {label:10} {'sum (~day-1 c2c)':14} {total * 100:+7.3f}%   "
              f"gap carries {share:+.0%}"
              + (f"   [{flat_opens} flat-open sessions]" if flat_opens else ""))
        results.append({"universe": universe, "side": side, **{
            k.replace(" ", "_").replace("-", "_"): v for k, v in cells.items()},
            "gap_share": share, "flat_opens": flat_opens,
            "n_events": len(events)})
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--universes", default="sp600,sp500")
    ap.add_argument("--min-dollars", type=float, default=10_000)
    ap.add_argument("--json")
    args = ap.parse_args(argv)

    out = []
    for u in args.universes.split(","):
        out.extend(run_universe(u.strip(), args.min_dollars))

    print("\nReading: if the repricing is all gap, nothing here is tradeable "
          "without a real-time EDGAR feed, and even then only at the open "
          "auction. A material open-to-close component would be the only "
          "thing worth building on.")
    print("Tracker context, not trading advice.")
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=2))
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
