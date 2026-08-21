#!/usr/bin/env python3
"""When will these names swing, WITHOUT waiting for earnings?

    python scripts/swing_forecast.py                 # book + watchlist
    python scripts/swing_forecast.py --days 120
    python scripts/swing_forecast.py --smallcap
    python scripts/swing_forecast.py --json out.json

``upcoming_swings.py`` answers the scheduled-earnings version of this and
stops there. This is the rest of it, and it rests on the two things that
actually survived the directional testing in PREDICTIONS.md:

1. **Volatility clusters.** ``validate_regimes`` confirms it locally: on
   validated names a turbulent day is followed by moves 1.3-1.7x the size
   that follow a quiet one. That is a genuine forecast of *how big*,
   available today, with no scheduled event required.
2. **Macro releases are dated years ahead.** FOMC announcements and payroll
   Fridays are published in advance, and each name's own history says how
   much it typically moves on them — the same per-name amplification
   ``catalysts.py`` measures for earnings, pointed at the macro calendar.

**No cell here forecasts direction.** The calendar study found nothing
directional on these same dates: FOMC day-before, day-of and day-after all
sat within noise, and the payroll-day premium is dead in the recent era.
What is being claimed is size, which is a different and much better
supported claim.

Design choices that carry the result:

* **Earnings sessions are excluded from every macro measurement.** An
  announcement landing on an FOMC day would otherwise credit the Fed with
  the company's own news. ``announcement_date`` owns the after-close rule,
  as everywhere else in this repo.
* **Amplification is a median ratio, per name, and is often near 1.** Most
  stocks do NOT move unusually on macro days; a rate-sensitive one might.
  The number is measured rather than assumed, and names below 1.15x are
  reported as unmoved rather than quietly padded into a list of "catalysts".
* **The regime label travels with its verdict.** ``validate_regimes``
  confirms on roughly 19 of 44 series in this universe and shows no
  separation on 12. A turbulent label on an unvalidated name is a
  description of the present, not a forecast, and is printed as such.
* **Expected ranges are calibrated per name** by walk-forward coverage, not
  taken from a normal table, and say when they fell back to the Gaussian
  default.

**A regime is a state, not a date.** The honest form of this forecast is
"the next week or two is likely to stay large for this name", and it decays
as the state changes. Re-run it rather than trusting a fortnight-old label.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import warnings
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
warnings.filterwarnings("ignore")

from market_desk.benchmark import load_constituents      # noqa: E402
from market_desk.catalysts import announcement_date      # noqa: E402
from market_desk.config import load_universe             # noqa: E402
from market_desk.fetch import fetch_history              # noqa: E402
from market_desk.volatility import (                     # noqa: E402
    classify_regime, expected_range, validate_regimes,
)

from calendar_study import FOMC_ANNOUNCEMENTS, first_friday   # noqa: E402
from shock_study import load_earnings                         # noqa: E402
from upcoming_swings import holdings_weights                  # noqa: E402

MIN_EVENTS = 6          # macro amplification needs more events than earnings:
                        # the effect is smaller, so the median is noisier
NOTABLE = 1.15          # below this a name simply does not move on macro days


def abs_moves(bars) -> dict[str, float]:
    out = {}
    for i in range(1, len(bars)):
        p, c = bars[i - 1].close, bars[i].close
        if p > 0 and c > 0:
            out[bars[i].date] = abs(c / p - 1.0)
    return out


def earnings_sessions(dates: list[str], stamps: list[str]) -> set[str]:
    """Reaction sessions to exclude, after-close rule respected."""
    out = set()
    for stamp in stamps:
        day, after_close = announcement_date(stamp)
        later = [d for d in dates if d > day] if after_close else \
                [d for d in dates if d >= day]
        if later:
            out.add(later[0])
    return out


def amplification(moves: dict[str, float], event_days: set[str],
                  exclude: set[str]) -> dict:
    """Median |move| on event sessions against ordinary ones.

    Medians because one outlier session should not set an expectation, and
    macro days are exactly where a single 2020-style print would.
    """
    ev = [v for d, v in moves.items() if d in event_days and d not in exclude]
    other = [v for d, v in moves.items()
             if d not in event_days and d not in exclude]
    if len(ev) < MIN_EVENTS or len(other) < 60:
        return {"n": len(ev)}
    med, base = statistics.median(ev), statistics.median(other)
    if base <= 0:
        return {"n": len(ev)}
    return {"n": len(ev), "typical": med, "baseline": base,
            "amplification": med / base, "largest": max(ev)}


def upcoming_macro(today: str, horizon: str) -> list[tuple[str, str]]:
    """Scheduled macro dates in the window, and a loud complaint if the
    hardcoded FOMC list runs out before the horizon does.

    Without the guard an exhausted calendar reports "no FOMC scheduled",
    which is indistinguishable in the output from "the Fed is not meeting"
    and is exactly backwards. Payroll dates are derived by rule so they
    never run out; FOMC dates are typed in and do.
    """
    last = max(FOMC_ANNOUNCEMENTS)
    if last < horizon:
        print(f"  WARNING: the pinned FOMC calendar ends {last}, before this "
              f"{horizon} horizon. Meetings after {last} are MISSING, not "
              f"absent — extend FOMC_ANNOUNCEMENTS in calendar_study.py from "
              f"federalreserve.gov/monetarypolicy/fomccalendars.htm.")
    out = [(d, "FOMC") for d in FOMC_ANNOUNCEMENTS if today <= d <= horizon]
    cur = datetime.fromisoformat(today).date().replace(day=1)
    end = datetime.fromisoformat(horizon).date()
    while cur <= end:
        f = first_friday(cur.isoformat()[:7])
        if today <= f <= horizon:
            out.append((f, "payrolls"))
        cur = (cur + timedelta(days=32)).replace(day=1)
    return sorted(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--smallcap", action="store_true")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--period", default="5y")
    ap.add_argument("--json")
    args = ap.parse_args(argv)

    today = date.today().isoformat()
    horizon = (date.today() + timedelta(days=args.days)).isoformat()

    tracked = list(load_universe().symbols)
    book = holdings_weights()
    core = sorted(set(tracked) | set(book))
    small = ([r["symbol"] for r in
              load_constituents(REPO_ROOT / "config/benchmark/sp600.csv")]
             if args.smallcap else [])
    fetch_list = sorted(set(core) | set(small))

    print(f"as of {today}, looking out {args.days} days (to {horizon})")
    print(f"fetching {len(fetch_list)} symbols ({args.period})")
    bars, failures = fetch_history(fetch_list, args.period)
    print(f"  {len(bars)} ok, {len(failures)} failed")
    earnings = load_earnings(fetch_list, False)

    dates_any = max((["" ] + [b.date for b in v] for v in bars.values()), key=len)
    fomc = {d for d in FOMC_ANNOUNCEMENTS}
    nfp = {first_friday(m) for m in
           sorted({d[:7] for d in dates_any if d})}
    macro_days = fomc | nfp

    rows = []
    for symbol, series in bars.items():
        closes = [b.close for b in series]
        if len(closes) < 300:
            continue
        ds = [b.date for b in series]
        moves = abs_moves(series)
        skip = earnings_sessions(ds, earnings.get(symbol) or [])
        reg = classify_regime(closes)
        val = validate_regimes(closes, horizon=5)
        rows.append({
            "symbol": symbol,
            "weight": book.get(symbol),
            "smallcap": symbol in set(small) and symbol not in set(core),
            "regime": reg.label,
            "regime_pctl": reg.percentile,
            "annual_vol": reg.annualized_vol,
            "verdict": val.verdict,
            "separation": val.separation,
            "fomc": amplification(moves, fomc, skip),
            "nfp": amplification(moves, nfp, skip),
            "macro": amplification(moves, macro_days, skip),
            "range_5": expected_range(closes, 5),
            "range_21": expected_range(closes, 21),
            "n_earnings_excluded": len(skip),
        })

    by_sym = {r["symbol"]: r for r in rows}
    upcoming = upcoming_macro(today, horizon)

    # ---- 1. dated market-wide windows -----------------------------------
    print("\n" + "=" * 74)
    print("DATED NON-EARNINGS WINDOWS — the macro calendar, published years ahead")
    print("=" * 74)
    spy = by_sym.get("SPY")
    if spy:
        for key, label in (("fomc", "FOMC days"), ("nfp", "payroll Fridays")):
            c = spy[key]
            if "amplification" in c:
                verdict = ("moves the index" if c["amplification"] >= NOTABLE
                           else "NOT bigger than an ordinary day")
                print(f"  SPY on {label:18} typ {c['typical'] * 100:.2f}% vs "
                      f"{c['baseline'] * 100:.2f}% ordinary = "
                      f"{c['amplification']:.2f}x  ({c['n']} events) — {verdict}")
    for d, kind in upcoming:
        away = (datetime.fromisoformat(d).date() - date.today()).days
        print(f"  {d}  {kind:9} ({away:3} days away)")
    print("  CPI release dates are deliberately absent: there is no "
          "derivation rule for them and typing them from memory is how "
          "silently wrong data gets made. This calendar is FOMC + payrolls, "
          "and CPI days are among the larger movers it does not show.")

    # ---- 2. which names actually react to macro -------------------------
    print("\n" + "=" * 74)
    print("WHO ACTUALLY MOVES ON MACRO DAYS (earnings sessions excluded)")
    print("=" * 74)
    movers = [r for r in rows if not r["smallcap"]
              and "amplification" in r["macro"]]
    movers.sort(key=lambda r: -r["macro"]["amplification"])
    shown = [r for r in movers if r["macro"]["amplification"] >= NOTABLE]
    for r in shown[:args.top]:
        c = r["macro"]
        w = f"{r['weight'] * 100:5.1f}% " if r["weight"] else "       "
        print(f"  {w}{r['symbol']:6} {c['amplification']:4.2f}x   "
              f"typ {c['typical'] * 100:5.2f}% vs {c['baseline'] * 100:5.2f}% "
              f"ordinary   worst {c['largest'] * 100:5.1f}%   ({c['n']} events)")
    if not shown:
        print("  none above "
              f"{NOTABLE:.2f}x — macro days are ordinary days for this universe.")
    else:
        print(f"\n  {len(movers) - len(shown)} of {len(movers)} names sit below "
              f"{NOTABLE:.2f}x: for them a macro date is not a catalyst, and "
              f"treating it as one is the mistake this table exists to prevent.")

    # ---- 3. state right now ---------------------------------------------
    print("\n" + "=" * 74)
    print("UNSETTLED RIGHT NOW — volatility clusters, so this persists")
    print("=" * 74)
    live = [r for r in rows if not r["smallcap"] and r["regime"] == "turbulent"]
    live.sort(key=lambda r: -(r["regime_pctl"] or 0))
    for r in live:
        w = f"{r['weight'] * 100:5.1f}% " if r["weight"] else "       "
        rng = r["range_21"]
        band = (f"+/-{rng.pct * 100:4.1f}% over 21 sessions" if rng else "")
        gate = ("" if r["verdict"] == "confirmed"
                else f"   [{r['verdict']} — description, not forecast]")
        sep = f" ({r['separation']:.2f}x sep)" if r["separation"] else ""
        print(f"  {w}{r['symbol']:6} vol {r['annual_vol'] * 100:5.1f}% annual, "
              f"{r['regime_pctl'] * 100:3.0f}th pctl of own year   {band}"
              f"{gate}{sep}")
    if not live:
        print("  nothing in this universe is in the top third of its own "
              "trailing volatility.")

    quiet = [r for r in rows if not r["smallcap"] and r["regime"] == "quiet"]
    print(f"\n  {len(quiet)} names are in the calmest third of their own year — "
          f"smaller moves than usual, in either direction.")

    if args.smallcap:
        print("\n" + "=" * 74)
        print("S&P 600 — most unsettled right now, validated names only")
        print("=" * 74)
        sc = [r for r in rows if r["smallcap"] and r["regime"] == "turbulent"
              and r["verdict"] == "confirmed"]
        sc.sort(key=lambda r: -(r["annual_vol"] or 0))
        for r in sc[:args.top]:
            rng = r["range_21"]
            print(f"  {r['symbol']:6} vol {r['annual_vol'] * 100:5.1f}% annual, "
                  f"{r['regime_pctl'] * 100:3.0f}th pctl   "
                  f"+/-{rng.pct * 100:4.1f}% over 21 sessions   "
                  f"({r['separation']:.2f}x sep)" if rng else "")
        print(f"\n  {len(sc)} S&P 600 names are both turbulent and carry a "
              f"confirmed regime verdict.")

    print("\nEvery number here is SIZE, not direction. The same dates were "
          "tested for direction in calendar_study and came back flat.")
    print("A regime is a state, not a date: it decays as the state changes, "
          "so re-run rather than trusting an old label.")
    print("\nTracker context, not trading advice.")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "as_of": today, "horizon": horizon,
            "upcoming_macro": [{"date": d, "kind": k} for d, k in upcoming],
            "rows": [{k: (v if not hasattr(v, "pct") else
                          {"pct": v.pct, "low": v.low, "high": v.high,
                           "calibrated": v.calibrated})
                      for k, v in r.items()} for r in rows],
        }, indent=2, default=str))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
