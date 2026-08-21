#!/usr/bin/env python3
"""When will these names swing? Dated windows, per-name amplification.

    python scripts/upcoming_swings.py                  # book + watchlist
    python scripts/upcoming_swings.py --days 90
    python scripts/upcoming_swings.py --smallcap       # add S&P 600 movers
    python scripts/upcoming_swings.py --json out.json

This is the one question the directional studies left standing. Five
pre-specified direction signals were tested and rejected (see
PREDICTIONS.md); scheduled-event timing was never in doubt, because it
does not require forecasting anything — the dates are published, and each
name's own history says how much it typically moves on them.

**Nothing here predicts direction, and the amplification numbers are not
price targets.** A 5x amplification means the session is typically five
times an ordinary one in ABSOLUTE terms. Which way is not answerable and
is not attempted.

Three things this measures rather than assumes:

* **Amplification is per name and is often below 1.** The obvious
  assumption — earnings equals big move — is wrong often enough to matter:
  a name driven by something other than quarterly results can be QUIETER
  on its announcement day than on an ordinary one. Those are flagged
  explicitly rather than dropped, because "this date is not the risk you
  think it is" is as useful as the reverse.
* **The reaction session, not the announcement date.** A company reporting
  after the close moves the NEXT session. ``announcement_date`` owns that
  rule; ignoring it understates after-close reporters badly.
* **Medians, not means.** One outlier announcement should not set the
  expectation, and earnings reactions are exactly where outliers live.

Confidence gate: a name needs at least ``MIN_EVENTS`` past reactions before
its amplification is reported as a measurement rather than a sample. Below
that the date is still shown — the date is a fact — but the size estimate
is withheld.

**Scheduled dates drift.** yfinance's forward dates are estimates until the
company confirms, and they move by days. Treat the week as the unit, not
the day. Re-run near the date rather than trusting a fortnight-old cache.
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

from market_desk.catalysts import announcement_date, measure_reaction  # noqa: E402
from market_desk.benchmark import load_constituents      # noqa: E402
from market_desk.config import load_universe             # noqa: E402
from market_desk.fetch import fetch_history              # noqa: E402
from market_desk.volatility import classify_regime, ewma_vol  # noqa: E402

from shock_study import load_earnings                    # noqa: E402

MIN_EVENTS = 4          # below this, report the date but withhold the size
TRADING_DAYS_PER_YEAR = 252


def holdings_weights() -> dict[str, float]:
    """Committed weights only. load_holdings(include_local=False) is the
    guarantee that no dollar figure can reach an output; keep it that way."""
    try:
        from market_desk.portfolio import load_holdings
        book = load_holdings(include_local=False)
        out = {}
        for p in getattr(book, "positions", []) or []:
            sym = getattr(p, "symbol", None)
            exp = getattr(p, "exposure", None)
            if sym and exp:
                out[sym] = float(exp)
        return out
    except Exception:                                     # noqa: BLE001
        return {}


def next_weekday(d: date) -> date:
    """Next Mon-Fri. Exchange holidays are NOT modelled — a reaction session
    landing on Thanksgiving or Good Friday slips to the following session,
    which is one more reason to read the week rather than the day."""
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def next_reaction(stamps: list[str], dates: list[str], today: str):
    """Next scheduled announcement and the session it should move.

    The reaction session for a FUTURE announcement cannot be looked up in
    the price history — those sessions have not happened. Past dates use
    the real calendar; future ones step to the next weekday, which is an
    approximation that ignores exchange holidays.
    """
    for stamp in sorted(stamps):
        day, after_close = announcement_date(stamp)
        if day < today:
            continue
        session = day
        if after_close:
            later = [d for d in dates if d > day]
            session = (later[0] if later
                       else next_weekday(datetime.fromisoformat(day).date()).isoformat())
        elif datetime.fromisoformat(day).date().weekday() >= 5:
            session = next_weekday(datetime.fromisoformat(day).date()).isoformat()
        return day, session, after_close
    return None, None, None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=60,
                    help="how far ahead to look (default 60)")
    ap.add_argument("--smallcap", action="store_true",
                    help="also rank S&P 600 names by measured amplification")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--period", default="5y")
    ap.add_argument("--json")
    args = ap.parse_args(argv)

    today = date.today().isoformat()
    horizon = (date.today() + timedelta(days=args.days)).isoformat()

    tracked = list(load_universe().symbols)
    book = holdings_weights()
    universe = sorted(set(tracked) | set(book))
    small = ([r["symbol"] for r in
              load_constituents(REPO_ROOT / "config/benchmark/sp600.csv")]
             if args.smallcap else [])
    fetch_list = sorted(set(universe) | set(small))

    print(f"as of {today}, looking out {args.days} days (to {horizon})")
    print(f"fetching {len(fetch_list)} symbols ({args.period})")
    bars, failures = fetch_history(fetch_list, args.period)
    print(f"  {len(bars)} ok, {len(failures)} failed")

    earnings = load_earnings(fetch_list, False)

    rows = []
    for symbol, series in bars.items():
        stamps = earnings.get(symbol) or []
        if not stamps:
            continue                                   # ETFs, funds, no data
        dates = [b.date for b in series]
        day, session, after_close = next_reaction(stamps, dates, today)
        if not day or day > horizon:
            continue
        reaction = measure_reaction(series, stamps)
        closes = [b.close for b in series]
        vol = ewma_vol(closes)
        rows.append({
            "symbol": symbol,
            "announce": day,
            "session": session,
            "after_close": bool(after_close),
            "days_away": (datetime.fromisoformat(day).date() - date.today()).days,
            "n_events": reaction.n_events if reaction else 0,
            "typical_move": reaction.median_move if reaction else None,
            "baseline": reaction.baseline_move if reaction else None,
            "amplification": reaction.amplification if reaction else None,
            "largest": reaction.largest_move if reaction else None,
            "daily_vol": vol,
            "weight": book.get(symbol),
            "smallcap": symbol in set(small) and symbol not in universe,
        })
    rows.sort(key=lambda r: (r["announce"], r["symbol"]))

    def line(r, show_weight=False):
        amp = r["amplification"]
        sized = r["n_events"] >= MIN_EVENTS and amp is not None
        w = f"{r['weight'] * 100:5.1f}% " if show_weight and r["weight"] else "       "
        if sized:
            note = ""
            if amp < 1.0:
                note = "  <- QUIETER than an ordinary day"
            elif amp >= 3.0:
                note = "  <- outsized"
            size = (f"typ {r['typical_move'] * 100:5.1f}%   "
                    f"{amp:4.1f}x ordinary   worst {r['largest'] * 100:5.1f}%")
        else:
            note, size = "  (too few past reactions to size)", ""
        session = r["session"]
        tag = " (after close -> next session)" if r["after_close"] else ""
        return (f"  {r['announce']}  {w}{r['symbol']:6} {size}{note}\n"
                f"             moves {session}{tag}")

    out_book = [r for r in rows if r["weight"]]
    out_watch = [r for r in rows if not r["weight"] and not r["smallcap"]]

    print("\n" + "=" * 74)
    print("YOUR BOOK — dated swing windows")
    print("=" * 74)
    if out_book:
        for r in out_book:
            print(line(r, show_weight=True))
        covered = sum(r["weight"] for r in out_book)
        print(f"\n  {len(out_book)} of {len(book)} positions report in this "
              f"window, covering {covered * 100:.0f}% of the book.")
    else:
        print("  nothing scheduled in this window.")

    print("\n" + "=" * 74)
    print("WATCHLIST — dated swing windows")
    print("=" * 74)
    for r in out_watch:
        print(line(r))
    if not out_watch:
        print("  nothing scheduled in this window.")

    # Cluster weeks: the portfolio-level finding, not a per-name one.
    weeks = defaultdict(list)
    for r in out_book + out_watch:
        iso = datetime.fromisoformat(r["session"]).date().isocalendar()
        weeks[(iso.year, iso.week)].append(r)
    if weeks:
        print("\n" + "=" * 74)
        print("CLUSTER WEEKS — when the book moves together")
        print("=" * 74)
        for (y, w), rs in sorted(weeks.items()):
            monday = date.fromisocalendar(y, w, 1)
            weight = sum(r["weight"] or 0 for r in rs)
            syms = " ".join(r["symbol"] for r in rs)
            flag = "   <- concentrated" if weight >= 0.25 else ""
            print(f"  week of {monday}  {len(rs):2} names"
                  f"{f', {weight * 100:.0f}% of book' if weight else ''}"
                  f"{flag}\n             {syms}")

    if args.smallcap:
        smalls = [r for r in rows if r["smallcap"]
                  and r["n_events"] >= MIN_EVENTS
                  and r["amplification"] is not None]
        smalls.sort(key=lambda r: -r["amplification"])
        print("\n" + "=" * 74)
        print(f"S&P SMALLCAP 600 — biggest measured amplification, next "
              f"{args.days} days")
        print("=" * 74)
        for r in smalls[:args.top]:
            print(f"  {r['announce']}  {r['symbol']:6} typ "
                  f"{r['typical_move'] * 100:5.1f}%   "
                  f"{r['amplification']:4.1f}x ordinary   "
                  f"worst {r['largest'] * 100:5.1f}%   "
                  f"moves {r['session']}"
                  f"{' (after close)' if r['after_close'] else ''}")
        print(f"\n  {len(smalls)} S&P 600 names report in this window with "
              f"enough history to size.")

    print("\nAmplification is ABSOLUTE size, not direction: 5x means the "
          "session is typically five times an ordinary one, either way. "
          "Direction is not forecastable and is not attempted.")
    print("Scheduled dates are estimates until the company confirms and move "
          "by days — treat the week as the unit and re-run near the date.")
    print("\nTracker context, not trading advice.")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"as_of": today, "horizon": horizon, "rows": rows}, indent=2))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
