#!/usr/bin/env python3
"""Do insider open-market purchases predict their own stock? (Form 4 test)

    python scripts/insider_study.py                       # parse cache + run
    python scripts/insider_study.py --zips DIR            # (re)parse SEC zips
    python scripts/insider_study.py --universe sp600      # small caps
    python scripts/insider_study.py --min-dollars 25000
    python scripts/insider_study.py --json out.json

The one Tier-2 timing source with a real directional literature behind it.
Corporate insiders file Form 4 within two business days of trading their
own stock. Purchases predict returns in every major study — Lakonishok &
Lee (2001), Jeng, Metrick & Zeckhauser (2003, ~50bp/mo on purchases),
Cohen, Malloy & Pomorski (2012, ~82bp/mo once routine trades are removed)
— while sales predict roughly nothing, because people sell for houses,
taxes and divorces but buy for only one reason.

Data: the SEC's structured insider-transactions data sets (quarterly ZIPs
of every Form 3/4/5, parsed by DERA from the filings themselves). Nothing
is scraped; the sample is the full population of filings for the quarters
present. Non-derivative transactions only, codes P (open-market purchase)
and S (open-market sale), priced and sized on the form.

Design choices, each of which moves the result and is therefore explicit:

* **The clock starts at the FILING date, not the transaction date.** The
  trade is inside information until filed; the 2024q1 data set contains a
  transaction filed fifteen months late, which would look like astonishing
  foresight measured from its trade date. Filing date is when the signal
  existed for anyone else.
* **Events enter the next session** (entry lag 1): a filing accepted at
  21:59 ET is not tradeable that day.
* **$10k floor** on the summed dollars per filing per direction — below
  that sit DRIP fragments and gift-adjacent noise.
* **10b5-1 plan filings are flagged** (the AFF10B5ONE field, populated
  since the 2022 rule amendments): a scheduled plan trade is the
  definition of an uninformative one, so discretionary purchases are the
  sharper cell. This is the poor man's Cohen-Malloy-Pomorski cut — the
  full routine/opportunistic split needs each insider's calendar history,
  which is deliberately out of scope.
* **Earnings-clean cell**: insider windows open right after earnings, so
  purchase filings cluster where post-earnings drift lives. The cell that
  excludes filings within 2 sessions of an earnings reaction (same
  machinery as shock_study) says how much of any effect is just PEAD
  wearing a Form 4.
* **Measurement is the shock harness's**: market-model residuals with the
  event's own alpha feedback excluded (the -4.68 lesson), calendar-time
  daily portfolios, Newey-West at the horizon lag.

**Universes.** ``--universe sp500`` is the large-cap default; ``sp600`` runs
the S&P SmallCap 600 pin, which is where the literature says these effects
actually live — insider trades are documented as most informative in small,
thinly-covered names, and the large-cap run here came back right-signed but
under-powered. The small-cap cell is therefore the one with a real chance of
resolving the effect, and it is also the one whose caveats bite hardest:

* **The market proxy changes with the universe.** Residualizing S&P 600
  names against SPY leaves the size factor sitting in the residual, so the
  calendar-time portfolio would partly measure small-caps-versus-large-caps
  rather than insider information. ``sp600`` therefore residualizes against
  IJR, the S&P 600's own index ETF, which absorbs market and size together
  because the universe *is* the index. ``--market`` overrides it.
* **Survivorship is worse here, and it is not neutral.** The S&P 600 turns
  over faster than the 500, so today's-members bias is larger — and it
  points in a specific direction for this test. Names that fell out of the
  index did badly; if insiders bought them on the way down, those purchases
  are missing from the sample and the measured purchase effect is
  overstated. Read a positive result with that thumb on the scale.
* **Thinner names, noisier residuals.** Small-cap daily residuals carry more
  idiosyncratic variance per name, which raises the per-name noise floor;
  whether that is offset by more insider activity per name is exactly what
  the MDE column reports rather than assumes.

**Multiple testing.** Twelve cells (six statistics x two horizons); at t=2
about 0.6 clear by chance; Harvey, Liu & Zhu (2016) hurdle stays t>3.
Survivorship: pinned constituent list, today's members, unchanged.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import io
import json
import math
import statistics
import sys
import warnings
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
warnings.filterwarnings("ignore")

from market_desk.benchmark import load_constituents            # noqa: E402
from market_desk.fetch import fetch_history                    # noqa: E402

from shock_study import (                                      # noqa: E402
    MIN_COVERAGE, blackout_sessions, calendar_time, load_earnings,
    newey_west, residual_returns,
)

# Per-universe pin, market proxy and event cache. The proxy is not a detail:
# see the docstring on why sp600 must not be residualized against SPY.
UNIVERSES = {
    "sp500": {"pin": "sp500.csv", "market": "SPY", "label": "S&P 500"},
    "sp600": {"pin": "sp600.csv", "market": "IJR", "label": "S&P SmallCap 600"},
}
EARNINGS_CACHE = REPO_ROOT / ".cache" / "earnings_dates.json"
CLUSTER_SESSIONS = 5      # distinct buyers within this window = a cluster buy
MIN_EVENTS = 40


# ---------------------------------------------------------------------------
# Parsing the SEC data sets
# ---------------------------------------------------------------------------

def norm_symbol(s: str) -> str:
    return (s or "").strip().upper().replace(".", "-")


def parse_date(s: str) -> str | None:
    """DD-MON-YYYY (the DERA convention) or ISO, to ISO."""
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _tsv(zf: zipfile.ZipFile, name: str):
    """Stream a TSV member as dict rows. DERA files are unquoted; the csv
    default quoting would swallow tabs after a stray double-quote in a
    reporting owner's name."""
    with zf.open(name) as fh:
        text = io.TextIOWrapper(fh, encoding="utf-8", errors="replace")
        reader = csv.reader(text, delimiter="\t", quoting=csv.QUOTE_NONE)
        header = next(reader)
        idx = {h.strip().upper(): i for i, h in enumerate(header)}
        for row in reader:
            yield idx, row


def _get(idx, row, col):
    i = idx.get(col)
    return row[i].strip() if i is not None and i < len(row) else ""


def parse_quarter(path: Path, universe: set[str]) -> list[dict]:
    """One quarter's ZIP -> per-filing purchase/sale dollar totals."""
    out: dict[str, dict] = {}
    with zipfile.ZipFile(path) as zf:
        for idx, row in _tsv(zf, "SUBMISSION.tsv"):
            if _get(idx, row, "DOCUMENT_TYPE") != "4":
                continue
            sym = norm_symbol(_get(idx, row, "ISSUERTRADINGSYMBOL"))
            if sym not in universe:
                continue
            filed = parse_date(_get(idx, row, "FILING_DATE"))
            if not filed:
                continue
            plan = _get(idx, row, "AFF10B5ONE").lower() in ("1", "true")
            out[_get(idx, row, "ACCESSION_NUMBER")] = {
                "symbol": sym, "filed": filed, "plan": plan,
                "owners": set(), "P": 0.0, "S": 0.0,
            }

        for idx, row in _tsv(zf, "NONDERIV_TRANS.tsv"):
            rec = out.get(_get(idx, row, "ACCESSION_NUMBER"))
            if rec is None:
                continue
            code = _get(idx, row, "TRANS_CODE")
            if code not in ("P", "S"):
                continue
            try:
                shares = float(_get(idx, row, "TRANS_SHARES") or 0)
                price = float(_get(idx, row, "TRANS_PRICEPERSHARE") or 0)
            except ValueError:
                continue
            if shares > 0 and price > 0:
                rec[code] += shares * price

        for idx, row in _tsv(zf, "REPORTINGOWNER.tsv"):
            rec = out.get(_get(idx, row, "ACCESSION_NUMBER"))
            if rec is not None:
                cik = _get(idx, row, "RPTOWNERCIK")
                if cik:
                    rec["owners"].add(cik)

    filings = []
    for rec in out.values():
        if rec["P"] > 0 or rec["S"] > 0:
            rec["owners"] = sorted(rec["owners"])
            filings.append(rec)
    return filings


def build_event_cache(zip_dir: Path, universe: set[str],
                      cache: Path) -> list[dict]:
    zips = sorted(zip_dir.glob("*.zip"))
    if not zips:
        raise SystemExit(f"no ZIPs in {zip_dir}; download the SEC insider "
                         f"transactions data sets first")
    filings = []
    for z in zips:
        rows = parse_quarter(z, universe)
        filings.extend(rows)
        print(f"  {z.name}: {len(rows)} Form 4 filings with P/S dollars "
              f"in universe")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(filings))
    print(f"  cached {len(filings)} filings -> {cache}")
    return filings


# ---------------------------------------------------------------------------
# Events on the price panel
# ---------------------------------------------------------------------------

def anchor(filings: list[dict], panel_dates: dict[str, list[str]],
           min_dollars: float) -> dict[str, list[dict]]:
    """Filing -> (symbol, session index, direction), deduped per session.

    The anchor session is the last session <= the filing date; with entry
    lag 1 the measured window opens the following session, so an
    after-hours acceptance is never traded same-day.
    """
    merged: dict[tuple, dict] = {}
    for f in filings:
        dates = panel_dates.get(f["symbol"])
        if not dates:
            continue
        i = bisect.bisect_right(dates, f["filed"]) - 1
        if i < 0:
            continue
        for side in ("P", "S"):
            if f[side] < min_dollars:
                continue
            key = (f["symbol"], i, side)
            rec = merged.setdefault(key, {
                "symbol": f["symbol"], "i": i, "side": side, "dollars": 0.0,
                "owners": set(), "plan": True,
            })
            rec["dollars"] += f[side]
            rec["owners"].update(f["owners"])
            rec["plan"] = rec["plan"] and f["plan"]   # all-plan only if every filing was
    out: dict[str, list[dict]] = defaultdict(list)
    for rec in merged.values():
        out[rec["side"]].append(rec)
    for side in out:
        out[side].sort(key=lambda r: (r["symbol"], r["i"]))
    return out


def mark_clusters(purchases: list[dict]) -> None:
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for ev in purchases:
        by_symbol[ev["symbol"]].append(ev)
    for evs in by_symbol.values():
        for k, ev in enumerate(evs):
            owners = set(ev["owners"])
            for other in evs[:k]:
                if ev["i"] - other["i"] <= CLUSTER_SESSIONS:
                    owners |= set(other["owners"])
            ev["cluster"] = len(owners) >= 2


def universe_series(resid: dict[str, list],
                    panel_dates: dict[str, list[str]],
                    min_names: int = 100) -> dict[str, float]:
    """Equal-weighted mean residual across the whole universe, per session.

    This is the benchmark an equal-weighted event leg must be measured
    against, and getting it wrong was worth about two thirds of the raw
    sale-leg "effect". The index ETF is CAP-weighted, so residualizing
    against it leaves a persistent equal-weight-minus-cap-weight tilt in
    every broad subset: measured here it runs -0.016%/day (t=-3.13),
    which is -1.0% over 63 sessions handed to any leg wide enough to
    resemble the index. Differencing against this series removes it.
    """
    daily: dict[str, dict[str, float]] = defaultdict(dict)
    for symbol, series in resid.items():
        dates = panel_dates[symbol]
        for j, v in enumerate(series):
            if v is not None:
                daily[dates[j]][symbol] = v
    return {d: statistics.mean(v.values())
            for d, v in daily.items() if len(v) >= min_names}


def leg_alpha(events: list[dict], resid: dict[str, list],
              panel_dates: dict[str, list[str]], horizon: int,
              lag: int = 1, baseline: dict[str, float] | None = None) -> dict:
    """Calendar-time portfolio of names with a live event window: mean
    daily residual, Newey-West at the horizon lag.

    Each NAME counts once per session no matter how many of its filings
    are live — insiders file in bursts, and small-cap sale filings run to
    29 per name across this sample, so an event-weighted average would
    hand a handful of heavily-filed tickers the entire result.
    """
    daily: dict[str, dict[str, float]] = defaultdict(dict)
    for ev in events:
        series, ds = resid[ev["symbol"]], panel_dates[ev["symbol"]]
        for j in range(ev["i"] + lag, min(ev["i"] + lag + horizon, len(series))):
            v = series[j]
            if v is not None:
                daily[ds[j]][ev["symbol"]] = v
    leg = {d: statistics.mean(v.values()) for d, v in daily.items() if v}
    if baseline is not None:
        days = sorted(set(leg) & set(baseline))
        series = [leg[d] - baseline[d] for d in days]
    else:
        days = sorted(leg)
        series = [leg[d] for d in days]
    if len(series) < 60:
        return {"n_events": len(events), "dates": len(series)}
    mean, se, t = newey_west(series, horizon)
    breadth = statistics.mean(len(daily[d]) for d in days)
    return {"n_events": len(events), "dates": len(series), "mean_daily": mean,
            "t": t, "mde_daily_t2": 2 * se, "car_h": mean * horizon,
            "breadth": breadth}


def show(label: str, cell: dict, horizon: int, results: list, key: str):
    results.append({"cell": key, "horizon": horizon, **cell})
    if "t" not in cell:
        print(f"  {label:26} n={cell['n_events']:5}   too thin "
              f"({cell['dates']} portfolio dates)")
        return
    thin = "" if cell["n_events"] >= MIN_EVENTS else "  THIN"
    print(f"  {label:26} n={cell['n_events']:5}   daily {cell['mean_daily'] * 100:+.3f}%   "
          f"~{horizon}d CAR {cell['car_h'] * 100:+.2f}%   t = {cell['t']:+.2f}   "
          f"MDE@t2 {cell['mde_daily_t2'] * 100:.3f}%/d   names/day {cell['breadth']:.0f}{thin}")


# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--universe", choices=sorted(UNIVERSES), default="sp500")
    ap.add_argument("--market", help="index ETF to residualize against "
                                     "(default: the universe's own)")
    ap.add_argument("--zips", help="directory of SEC quarterly ZIPs; reparse "
                                   "even if the event cache exists")
    ap.add_argument("--period", default="5y")
    ap.add_argument("--min-dollars", type=float, default=10_000)
    ap.add_argument("--horizons", default="21,63")
    ap.add_argument("--entry-lag", type=int, default=1,
                    help="sessions between the filing and the start of the "
                         "measured window (1 = next session; raise it to "
                         "shed the filing-day reaction and the bid-ask "
                         "bounce, and see whether drift survives without them)")
    ap.add_argument("--skip-earnings-cell", action="store_true",
                    help="skip the earnings-date fetch (slow on a fresh "
                         "universe) and omit the ex-earnings cell")
    ap.add_argument("--json")
    args = ap.parse_args(argv)
    horizons = [int(h) for h in args.horizons.split(",")]

    spec = UNIVERSES[args.universe]
    market_sym = args.market or spec["market"]
    pin = REPO_ROOT / "config" / "benchmark" / spec["pin"]
    event_cache = REPO_ROOT / ".cache" / f"insider_events_{args.universe}.json"

    symbols = [r["symbol"] for r in load_constituents(pin)]
    if not symbols:
        raise SystemExit(f"no pinned {spec['label']} list at {pin}; run "
                         f"scripts/refresh_constituents.py --index "
                         f"{args.universe}")
    universe = {norm_symbol(s) for s in symbols}
    print(f"{spec['label']}: {len(symbols)} names, residualized against "
          f"{market_sym}")

    if args.zips:
        filings = build_event_cache(Path(args.zips), universe, event_cache)
    elif event_cache.exists():
        filings = json.loads(event_cache.read_text())
        print(f"{len(filings)} filings from {event_cache}")
    else:
        raise SystemExit(f"no event cache for {args.universe}; run once with "
                         f"--zips DIR")

    print(f"fetching {len(symbols)} constituents + {market_sym} ({args.period})")
    bars, failures = fetch_history(symbols + [market_sym], args.period)
    print(f"  {len(bars)} ok, {len(failures)} failed")
    if market_sym not in bars:
        raise SystemExit(f"{market_sym} failed to download; rate limited? retry.")
    if len(bars) < MIN_COVERAGE * (len(symbols) + 1):
        raise SystemExit(f"only {len(bars)} of {len(symbols) + 1} downloaded; "
                         f"refusing a truncated universe. Retry later.")

    market_bars = bars.pop(market_sym)
    market = {}
    for i in range(1, len(market_bars)):
        p, c = market_bars[i - 1].close, market_bars[i].close
        if p > 0 and c > 0:
            market[market_bars[i].date] = math.log(c / p)

    panel_dates = {s: [b.date for b in bs] for s, bs in bars.items()}
    resid = {s: residual_returns(panel_dates[s], [b.close for b in bs],
                                 market, True, False)
             for s, bs in bars.items()}

    zero_event = [s for s in universe
                  if s in panel_dates
                  and not any(f["symbol"] == s for f in filings)]
    if len(zero_event) > 25:
        print(f"  WARNING: {len(zero_event)} tracked names have ZERO filings "
              f"— check symbol mapping: {sorted(zero_event)[:10]} ...")

    sides = anchor(filings, panel_dates, args.min_dollars)
    purchases, sales = sides.get("P", []), sides.get("S", [])
    mark_clusters(purchases)

    # The ex-earnings cell needs dates for THIS universe; load_earnings
    # tops up the shared cache with whatever symbols it is missing rather
    # than assuming a previous run covered them.
    if args.skip_earnings_cell:
        earnings = {}
        print("  --skip-earnings-cell: ex-earnings cell will be omitted")
    else:
        earnings = load_earnings(sorted(universe), False)
    blocked: dict[str, set[int]] = {}
    for s, stamps in earnings.items():
        if s in panel_dates and stamps:
            blocked[s] = blackout_sessions(panel_dates[s], stamps, 2)

    p_disc = [e for e in purchases if not e["plan"]]
    p_cluster = [e for e in purchases if e.get("cluster")]
    p_clean = [e for e in purchases
               if e["i"] not in blocked.get(e["symbol"], set())]

    med = statistics.median([e["dollars"] for e in purchases]) if purchases else 0
    print(f"\n{len(purchases)} purchase events / {len(sales)} sale events "
          f"(>= ${args.min_dollars:,.0f} per filing side, deduped per session)")
    print(f"  discretionary (no 10b5-1 flag): {len(p_disc)}   "
          f"cluster (>=2 buyers/{CLUSTER_SESSIONS} sessions): {len(p_cluster)}   "
          f"outside earnings blackout: {len(p_clean)}")
    print(f"  median purchase filing ${med:,.0f}; "
          f"{len({e['symbol'] for e in purchases})} names with a purchase")

    baseline = universe_series(resid, panel_dates)
    print(f"  equal-weight universe baseline built over {len(baseline)} sessions")

    results: list[dict] = []

    # The decomposition that decides what this study actually found: a
    # disclosure that reprices a stock is not a signal anyone can trade,
    # because day 0 is the filing session and day 1's close-to-close spans
    # the after-hours window most Form 4s land in. Only days 2+ are drift.
    print("\n" + "=" * 74)
    print("DISCLOSURE vs DRIFT — equal-weight-neutral")
    print("=" * 74)
    for label, evs in (("purchases", purchases), ("sales", sales)):
        print(f"  {label}")
        for name, h, lg, key in (("day 0 (filing session)", 1, 0, "d0"),
                                 ("day 1 (first session after)", 1, 1, "d1"),
                                 ("days 2-21 (drift)", 20, 2, "d2_21"),
                                 ("days 2-63 (drift)", 62, 2, "d2_63")):
            c = leg_alpha(evs, resid, panel_dates, h, lg, baseline)
            key_full = f"{label[0]}_{key}"
            results.append({"cell": key_full, "horizon": h, "lag": lg, **c})
            if "t" not in c:
                print(f"    {name:32} thin")
                continue
            print(f"    {name:32} {c['car_h'] * 100:+7.2f}%   t = {c['t']:+6.2f}")
    print("  Day 0 and day 1 are the market repricing a disclosure, not a "
          "forecast of it.\n  Days 2+ are the only tradeable claim, and the "
          "only one the literature makes.")

    for horizon in horizons:
        print("\n" + "=" * 74)
        print(f"HORIZON {horizon} SESSIONS — equal-weight-neutral, "
              f"INCLUDING the disclosure jump")
        print("=" * 74)
        lag = args.entry_lag
        show("purchases, all",
             leg_alpha(purchases, resid, panel_dates, horizon, lag, baseline),
             horizon, results, "p_all")
        show("purchases, discretionary",
             leg_alpha(p_disc, resid, panel_dates, horizon, lag, baseline),
             horizon, results, "p_discretionary")
        show("purchases, clustered",
             leg_alpha(p_cluster, resid, panel_dates, horizon, lag, baseline),
             horizon, results, "p_cluster")
        if earnings:
            show("purchases, ex-earnings",
                 leg_alpha(p_clean, resid, panel_dates, horizon, lag, baseline),
                 horizon, results, "p_ex_earnings")
        show("sales, all",
             leg_alpha(sales, resid, panel_dates, horizon, lag, baseline),
             horizon, results, "s_all")

        both = ([{**e, "up": True} for e in purchases]
                + [{**e, "up": False} for e in sales])
        spread, breadth = calendar_time(both, resid, panel_dates, horizon, lag)
        mean, se, t = newey_west(spread, horizon)
        print(f"  {'purchases minus sales':26} {'':8}   daily {mean * 100:+.3f}%   "
              f"~{horizon}d CAR {mean * horizon * 100:+.2f}%   t = {t:+.2f}   "
              f"MDE@t2 {2 * se * 100:.3f}%/d")
        results.append({"cell": "p_minus_s", "horizon": horizon,
                        "mean_daily": mean, "t": t, "mde_daily_t2": 2 * se,
                        "dates": len(spread), "breadth": breadth})

    cells = len(results)
    print(f"\nSales are expected to be uninformative (diversification, taxes); "
          f"purchases carry the literature's signal. The ex-earnings cell "
          f"separates insider information from PEAD wearing a Form 4.")
    print(f"Multiple testing: {cells} cells, ~{cells * 0.05:.1f} clear t=2 by "
          f"chance; Harvey, Liu & Zhu (2016) hurdle is t>3.")
    if args.universe == "sp500":
        print("Universe caveat: S&P 500 large caps are where insider effects "
              "are documented WEAKEST; a null here does not refute the "
              "small-cap literature.")
    else:
        print("Universe caveat: small caps are where the literature puts the "
              "effect, so this is the powered test — but survivorship bites "
              "hardest here and is NOT neutral: index leavers did badly, and "
              "insider purchases in those names are missing, which overstates "
              "a positive purchase result.")
    print("Survivorship: pinned list, today's members.")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"universe": args.universe, "market": market_sym,
             "period": args.period, "min_dollars": args.min_dollars,
             "entry_lag": args.entry_lag,
             "n_filings": len(filings), "n_purchases": len(purchases),
             "n_sales": len(sales), "cells_tested": cells,
             "results": results}, indent=2))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
