#!/usr/bin/env python3
"""Do unscheduled price shocks drift, and does volume decide which way?

    python scripts/shock_study.py                    # 3 sigma, horizons 5/10/21
    python scripts/shock_study.py --threshold 2.5
    python scripts/shock_study.py --residual raw     # no market model
    python scripts/shock_study.py --json out.json

The earnings half of this question is already answered in ``catalysts.py``:
a scheduled report moves a stock, by a per-name amplification the dashboard
measures. This asks the *unscheduled* version. A stock jumps 4 sigma on no
calendar event — does it keep going, or give it back?

The literature says the answer depends on volume. Pritamani & Singal (2001)
find large moves accompanied by high volume continue, while large moves
without it reverse; Gutierrez & Kelley (2008) document drift after extreme
weekly returns lasting well beyond the first month. Volume is the proxy for
"something happened" as against "the book was thin." That makes it a
directional timing claim with a date attached, which is what the earnings
machinery gives us and nothing else in this repo does.

WHAT MAKES THIS HARD, AND WHAT IS DONE ABOUT IT

* **Earnings must actually be excluded, not assumed away.** Announcement
  sessions are carved out through ``announcement_date`` — the same function
  the catalysts module uses, so an after-close reporter's blackout lands on
  the session that actually reacted. A symbol whose earnings dates fail to
  fetch is DROPPED from the study rather than included unscreened: an
  unscreened name silently readmits the exact events this study exists to
  exclude, and it would bias the result toward "shocks drift" because
  post-earnings drift is real.

* **A market-wide day is not 500 stock-specific events.** Shocks are
  detected on market-model residuals, with beta estimated over the trailing
  252 sessions ending the session BEFORE — never including the day whose
  residual it prices. ``--residual raw`` turns the model off to show how
  much of any result is simply beta.

* **The shock must not inflate its own denominator.** The EWMA volatility
  used to standardize day t is the value as of t-1. Using the contemporaneous
  figure divides a big move by a variance that big move just raised, which
  quietly makes shocks look smaller the more extreme they are.

* **Event clustering is the trap that decides this study.** Events arrive in
  bursts — one macro print manufactures hundreds. Treating those as
  independent observations inflates t by a factor of several. Two estimators
  are reported and they fail differently:

    1. *Event-time CAR*, averaged within each event date and t-tested across
       dates. Clustered, interpretable, the "average shock drifts X%" number.
    2. *Calendar-time portfolio* (Fama 1998), the primary statistic: hold
       every name in an active event window, take the daily spread between
       the up-shock and down-shock legs, and t-test that daily series with
       Newey-West errors at the horizon's lag. Overlapping windows make the
       series autocorrelated; Newey-West is the correction for it.

  When the two disagree, the calendar-time number is the one to believe.

* **One name may not become the sample.** A volatile fortnight can throw
  five events for a single symbol. ``--cooldown`` (default 21 sessions)
  requires a name's events to be spaced, so the average event is an average
  across names rather than across one name's bad month.

**Multiple testing.** A full run reports 9 continuation cells (3 horizons x
3 volume buckets) plus the legs. The footer prints the count. Harvey, Liu &
Zhu (2016) put the hurdle for a new factor claim at t>3, and that is the
number to hold this to.

**Survivorship** is inherited from the pinned constituent list, unchanged
from the other studies: today's members, dropped names absent.
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
warnings.filterwarnings("ignore")

from market_desk.benchmark import load_constituents            # noqa: E402
from market_desk.catalysts import announcement_date            # noqa: E402
from market_desk.fetch import fetch_earnings_dates, fetch_history  # noqa: E402
from market_desk.volatility import EWMA_LAMBDA, MIN_OBS        # noqa: E402
from market_desk.volume import relative_volume                 # noqa: E402

MARKET = "SPY"
BETA_WINDOW = 252
CACHE = REPO_ROOT / ".cache" / "earnings_dates.json"

# Below this many events a bucket is an anecdote; it is counted and shown but
# never summarized as a result.
MIN_EVENTS = 40
# Below this many usable calendar dates the portfolio series cannot support a
# Newey-West standard error worth printing.
MIN_DATES = 100
# A throttled fetch is silently partial. Refuse rather than report counts from
# whatever fraction happened to arrive.
MIN_COVERAGE = 0.90


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def newey_west(series: list[float], lags: int) -> tuple[float, float, float]:
    """Mean, Newey-West standard error, t. Bartlett kernel.

    The calendar-time portfolio holds each name for `horizon` sessions, so
    consecutive daily returns share most of their members and the series is
    autocorrelated by construction. An iid standard error on it is simply
    wrong, and wrong in the direction that manufactures findings.
    """
    n = len(series)
    if n < 3:
        return (statistics.mean(series) if series else 0.0), 0.0, 0.0
    mean = statistics.mean(series)
    dev = [x - mean for x in series]
    var = sum(d * d for d in dev) / n
    for lag in range(1, min(lags, n - 1) + 1):
        weight = 1.0 - lag / (lags + 1)
        cov = sum(dev[i] * dev[i - lag] for i in range(lag, n)) / n
        var += 2.0 * weight * cov
    if var <= 0:
        return mean, 0.0, 0.0
    se = math.sqrt(var / n)
    return mean, se, (mean / se if se else 0.0)


def clustered(by_date: dict[str, list[float]]) -> tuple[float, float, float, int]:
    """Average within each event date, then t-test across dates."""
    daily = [statistics.mean(v) for v in by_date.values() if v]
    n = len(daily)
    if n < 3:
        return (statistics.mean(daily) if daily else 0.0), 0.0, 0.0, n
    mean = statistics.mean(daily)
    se = statistics.stdev(daily) / math.sqrt(n)
    return mean, se, (mean / se if se else 0.0), n


# ---------------------------------------------------------------------------
# Series construction
# ---------------------------------------------------------------------------

def ewma_vol_of_returns(rets: list[float | None],
                        lam: float = EWMA_LAMBDA) -> list[float | None]:
    """EWMA volatility of a RETURN series, index-aligned with it.

    ``volatility.ewma_vol_series`` takes prices and differences them
    internally; residuals are already returns, so its first step does not
    apply. The seeding convention and lambda are imported rather than
    restated so the two estimators cannot drift apart.
    """
    out: list[float | None] = [None] * len(rets)
    seen: list[tuple[int, float]] = [(i, r) for i, r in enumerate(rets) if r is not None]
    if len(seen) < MIN_OBS:
        return out
    seed = [r for _, r in seen[:MIN_OBS]]
    mean = sum(seed) / len(seed)
    var = sum((r - mean) ** 2 for r in seed) / (len(seed) - 1)
    out[seen[MIN_OBS - 1][0]] = math.sqrt(var)
    for i, r in seen[MIN_OBS:]:
        var = lam * var + (1 - lam) * r * r
        out[i] = math.sqrt(var)
    return out


def residual_returns(dates: list[str], closes: list[float],
                     market: dict[str, float],
                     use_model: bool, use_alpha: bool = False) -> list[float | None]:
    """Market-model residual log returns, index-aligned with `closes`.

    Beta comes from the trailing ``BETA_WINDOW`` sessions ending the session
    before, so no residual is priced with a beta that saw the move it is
    measuring.

    **Alpha is excluded by default, and that is not a detail.** A rolling
    window estimates alpha as the mean residual, so the event itself sits
    inside the window used to price the entire following year. One +8%
    shock raises the estimated daily alpha by roughly 8%/252 = 0.03%, and
    every post-event residual then has that subtracted from it — about
    -2% over a 63-session window, manufactured out of nothing but the
    estimator. It produces textbook-looking reversal with a large t and no
    economic content. Subtracting only ``beta * market`` leaves a
    market-adjusted return with no such feedback; ``--alpha include``
    restores the contaminated version so the size of the artifact can be
    seen rather than argued about.
    """
    out: list[float | None] = [None] * len(closes)
    pairs: list[tuple[int, float, float]] = []
    for i in range(1, len(closes)):
        if closes[i - 1] <= 0 or closes[i] <= 0:
            continue
        y = math.log(closes[i] / closes[i - 1])
        if not use_model:
            out[i] = y
            continue
        x = market.get(dates[i])
        if x is None:
            continue
        pairs.append((i, x, y))
    if not use_model:
        return out

    lo = 0
    sx = sy = sxy = sxx = 0.0
    for k, (i, x, y) in enumerate(pairs):
        while k - lo > BETA_WINDOW:                    # sums cover pairs[lo:k]
            _, ox, oy = pairs[lo]
            sx -= ox; sy -= oy; sxy -= ox * oy; sxx -= ox * ox
            lo += 1
        if k - lo == BETA_WINDOW:
            n = float(BETA_WINDOW)
            varx = sxx - sx * sx / n
            if varx > 0:
                beta = (sxy - sx * sy / n) / varx
                alpha = (sy - beta * sx) / n if use_alpha else 0.0
                out[i] = y - (alpha + beta * x)
        sx += x; sy += y; sxy += x * y; sxx += x * x
    return out


def blackout_sessions(dates: list[str], stamps: list[str], pad: int) -> set[int]:
    """Indices within `pad` sessions of an earnings reaction session.

    ``announcement_date`` owns the after-close rule: a company reporting at
    16:00 moves the NEXT session, and the blackout has to land there.
    """
    out: set[int] = set()
    position = {d: i for i, d in enumerate(dates)}
    for stamp in stamps:
        day, next_session = announcement_date(stamp)
        idx = position.get(day)
        if idx is None:                                # not a trading day
            later = [i for d, i in position.items() if d >= day]
            if not later:
                continue
            idx = min(later)
        elif next_session:
            idx += 1
        for j in range(idx - pad, idx + pad + 1):
            if 0 <= j < len(dates):
                out.add(j)
    return out


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def find_events(symbol: str, series: dict, market: dict[str, float],
                earnings: list[str], args) -> list[dict]:
    dates, closes, volumes = series["d"], series["c"], series["v"]
    resid = residual_returns(dates, closes, market, args.residual == "market",
                             args.alpha == "include")
    vol = ewma_vol_of_returns(resid)
    rvol = relative_volume(volumes, 20)
    blocked = blackout_sessions(dates, earnings, args.earnings_pad)
    # Positive control: keep ONLY the reaction sessions instead of dropping
    # them. Post-earnings drift is one of the best-documented effects in the
    # literature, so a harness that cannot see it here has not measured a
    # null anywhere else — it has measured its own lack of power.
    keep_only = blackout_sessions(dates, earnings, 0) if args.only_earnings else None

    events, last = [], -10 ** 9
    for i in range(1, len(dates)):
        if keep_only is not None:
            if i not in keep_only:
                continue
        elif i in blocked:
            continue
        if i - last < args.cooldown:
            continue
        e, sigma_prev, rv = resid[i], vol[i - 1], rvol[i]
        if e is None or sigma_prev is None or sigma_prev <= 0 or rv is None:
            continue
        z = e / sigma_prev
        if abs(z) < args.threshold:
            continue
        events.append({"symbol": symbol, "i": i, "date": dates[i],
                       "z": z, "up": z > 0, "relvol": rv})
        last = i
    return events, resid


def bucket_of(event: dict, args) -> str | None:
    if event["relvol"] >= args.relvol_high:
        return "confirmed"
    if event["relvol"] < args.relvol_low:
        return "unconfirmed"
    return None                                        # deliberate dead zone


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------

def event_time_car(events: list[dict], resid: dict[str, list], horizon: int,
                   lag: int = 1):
    """Cumulative residual return over t+lag .. t+lag+horizon-1, per event.

    ``lag`` exists because conditioning on an extreme return is not a clean
    way to select events: the event-day return carries a transitory
    component — price pressure, the bid-ask bounce, liquidity paid to
    whoever took the other side — and picking the largest returns
    preferentially picks the days where that component was largest. It
    reverses the next session regardless of what the news was. Jegadeesh
    (1990) skips a day for exactly this reason. If a result dies at lag 2,
    it was microstructure, not information.
    """
    by_date_up: dict[str, list[float]] = defaultdict(list)
    by_date_dn: dict[str, list[float]] = defaultdict(list)
    for ev in events:
        series = resid[ev["symbol"]]
        window = series[ev["i"] + lag: ev["i"] + lag + horizon]
        vals = [v for v in window if v is not None]
        if len(vals) < horizon:
            continue
        car = math.expm1(sum(vals))
        (by_date_up if ev["up"] else by_date_dn)[ev["date"]].append(car)
    return by_date_up, by_date_dn


def calendar_time(events: list[dict], resid: dict[str, list],
                  dates_by_symbol: dict[str, list[str]], horizon: int,
                  lag: int = 1):
    """Daily spread between the up-shock leg and the down-shock leg.

    Each NAME held contributes its residual return once per session,
    however many live event windows it has. That deduplication is not a
    nicety: a name with twenty overlapping filings would otherwise be
    averaged in twenty times, so the "portfolio" would be event-weighted
    and a single heavily-filed ticker could carry the whole result. The
    give-away is a breadth number larger than the universe.

    Dates where either leg is empty are skipped, and the count of usable
    dates is reported.
    """
    up: dict[str, dict[str, float]] = defaultdict(dict)
    dn: dict[str, dict[str, float]] = defaultdict(dict)
    for ev in events:
        series, dates = resid[ev["symbol"]], dates_by_symbol[ev["symbol"]]
        target = up if ev["up"] else dn
        for j in range(ev["i"] + lag, min(ev["i"] + lag + horizon, len(series))):
            v = series[j]
            if v is not None:
                target[dates[j]][ev["symbol"]] = v

    spread, breadth = [], []
    for day in sorted(set(up) & set(dn)):
        spread.append(statistics.mean(up[day].values())
                      - statistics.mean(dn[day].values()))
        breadth.append((len(up[day]) + len(dn[day])) / 2)
    return spread, (statistics.mean(breadth) if breadth else 0.0)


# ---------------------------------------------------------------------------

def load_earnings(symbols: list[str], refresh: bool) -> dict[str, list[str]]:
    if CACHE.exists() and not refresh:
        cached = json.loads(CACHE.read_text())
        missing = [s for s in symbols if s not in cached]
        if not missing:
            print(f"earnings dates: {len(cached)} symbols from {CACHE}")
            return cached
        print(f"earnings cache missing {len(missing)} symbols; fetching those")
        fetched = fetch_earnings_dates(missing)
        cached.update({s: r.get("earnings_dates", []) for s, r in fetched.items()})
    else:
        print(f"fetching earnings dates for {len(symbols)} symbols "
              f"(slow — cached to {CACHE} afterwards)")
        fetched = fetch_earnings_dates(symbols)
        cached = {s: r.get("earnings_dates", []) for s, r in fetched.items()}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cached, indent=0))
    return cached


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--period", default="5y")
    ap.add_argument("--threshold", type=float, default=3.0,
                    help="|standardized residual| defining a shock (default 3.0)")
    ap.add_argument("--horizons", default="5,10,21")
    ap.add_argument("--cooldown", type=int, default=21,
                    help="minimum sessions between one name's events")
    ap.add_argument("--earnings-pad", type=int, default=2,
                    help="sessions blacked out either side of a report")
    ap.add_argument("--relvol-high", type=float, default=2.0)
    ap.add_argument("--relvol-low", type=float, default=1.5)
    ap.add_argument("--residual", choices=("market", "raw"), default="market")
    ap.add_argument("--alpha", choices=("exclude", "include"), default="exclude",
                    help="whether to subtract the trailing-window alpha; "
                         "including it lets the event contaminate its own "
                         "post-event benchmark (see residual_returns)")
    ap.add_argument("--entry-lag", type=int, default=1,
                    help="sessions between the event and the start of the "
                         "measured window (1 = next session; 2 skips a day to "
                         "shed the bid-ask bounce)")
    ap.add_argument("--only-earnings", action="store_true",
                    help="POSITIVE CONTROL: keep only earnings reaction "
                         "sessions, where drift is known to exist")
    ap.add_argument("--refresh-earnings", action="store_true")
    ap.add_argument("--json")
    args = ap.parse_args(argv)
    horizons = [int(h) for h in args.horizons.split(",")]

    symbols = [r["symbol"] for r in load_constituents()]
    print(f"fetching {len(symbols)} constituents + {MARKET} ({args.period})")
    bars, failures = fetch_history(symbols + [MARKET], args.period)
    print(f"  {len(bars)} ok, {len(failures)} failed")

    # A throttled download returns a partial universe and the study would
    # happily run on it — 298 names instead of 503, with no visible signal
    # and every count quietly wrong. yfinance rate-limits hard on repeated
    # 500-symbol pulls, so this is a routine failure, not an exotic one.
    if MARKET not in bars:
        raise SystemExit(f"{MARKET} failed to download; cannot residualize. "
                         f"Rate limited? Wait a few minutes and retry.")
    if len(bars) < MIN_COVERAGE * (len(symbols) + 1):
        raise SystemExit(
            f"only {len(bars)} of {len(symbols) + 1} symbols downloaded "
            f"({len(bars) / (len(symbols) + 1):.0%}); refusing to run on a "
            f"truncated universe. yfinance rate limit — wait and retry.")

    market_bars = bars.pop(MARKET, [])
    market = {}
    for i in range(1, len(market_bars)):
        p, c = market_bars[i - 1].close, market_bars[i].close
        if p > 0 and c > 0:
            market[market_bars[i].date] = math.log(c / p)

    earnings = load_earnings(list(bars), args.refresh_earnings)
    screened = {s: e for s, e in earnings.items() if e}
    dropped = [s for s in bars if s not in screened]
    print(f"  {len(screened)} symbols screened for earnings; "
          f"{len(dropped)} dropped for having none "
          f"(unscreened names would readmit the events this study excludes)")

    panel = {s: {"d": [b.date for b in bs], "c": [b.close for b in bs],
                 "v": [b.volume for b in bs]}
             for s, bs in bars.items() if s in screened}

    all_events, resid, dates_by_symbol = [], {}, {}
    for symbol, series in panel.items():
        evs, r = find_events(symbol, series, market, screened[symbol], args)
        all_events.extend(evs)
        resid[symbol] = r
        dates_by_symbol[symbol] = series["d"]

    for ev in all_events:
        ev["bucket"] = bucket_of(ev, args)

    n_up = sum(1 for e in all_events if e["up"])
    if args.only_earnings:
        print("\n*** POSITIVE CONTROL: earnings reaction sessions ONLY. A null "
              "here would mean the harness lacks power, not that drift is "
              "absent. ***")
    print(f"\n{len(all_events)} shocks at |z| >= {args.threshold} "
          f"({n_up} up, {len(all_events) - n_up} down) across "
          f"{len({e['symbol'] for e in all_events})} names, "
          f"residual = {args.residual}")
    for name in ("confirmed", "unconfirmed", None):
        sub = [e for e in all_events if e["bucket"] == name]
        label = {"confirmed": f"relvol >= {args.relvol_high}",
                 "unconfirmed": f"relvol < {args.relvol_low}",
                 None: "dead zone (excluded from the split)"}[name]
        print(f"  {str(name or 'neither'):12} {len(sub):5}   {label}")

    results = []
    for horizon in horizons:
        print("\n" + "=" * 74)
        print(f"HORIZON {horizon} SESSIONS")
        print("=" * 74)
        print(f"{'bucket':13} {'n up':>6} {'n dn':>6} | {'CAR up':>8} {'CAR dn':>8} "
              f"{'t(cl)':>7} | {'daily spr':>10} {'t(NW)':>7} {'MDE@t2':>8} "
              f"{'names/day':>10}")
        for name in ("all", "confirmed", "unconfirmed"):
            sub = ([e for e in all_events] if name == "all"
                   else [e for e in all_events if e["bucket"] == name])
            up_map, dn_map = event_time_car(sub, resid, horizon, args.entry_lag)
            n_u = sum(len(v) for v in up_map.values())
            n_d = sum(len(v) for v in dn_map.values())
            car_u = statistics.mean([x for v in up_map.values() for x in v]) if n_u else 0.0
            car_d = statistics.mean([x for v in dn_map.values() for x in v]) if n_d else 0.0

            # Continuation = up-leg CAR minus down-leg CAR, clustered by date.
            joint: dict[str, list[float]] = defaultdict(list)
            for day, vals in up_map.items():
                joint[day].extend(vals)
            for day, vals in dn_map.items():
                joint[day].extend(-v for v in vals)
            _, _, t_cl, n_dates = clustered(joint)

            spread, breadth = calendar_time(sub, resid, dates_by_symbol, horizon,
                                           args.entry_lag)
            mean_sp, se_sp, t_nw = newey_west(spread, horizon)

            thin = "" if min(n_u, n_d) >= MIN_EVENTS and len(spread) >= MIN_DATES else "  THIN"
            print(f"{name:13} {n_u:6} {n_d:6} | {car_u * 100:+7.2f}% {car_d * 100:+7.2f}% "
                  f"{t_cl:+7.2f} | {mean_sp * 100:+9.3f}% {t_nw:+7.2f} "
                  f"{2 * se_sp * 100:7.3f}% {breadth:10.1f}{thin}")
            results.append({"horizon": horizon, "bucket": name,
                            "n_up": n_u, "n_down": n_d,
                            "car_up": car_u, "car_down": car_d,
                            "t_clustered": t_cl, "event_dates": n_dates,
                            "daily_spread": mean_sp, "t_newey_west": t_nw,
                            "mde_at_t2": 2 * se_sp,
                            "portfolio_dates": len(spread), "names_per_day": breadth,
                            "thin": bool(thin)})

    cells = len(results)
    print(f"\nCAR up/down are event-time averages; t(cl) tests continuation "
          f"(up minus down) across event DATES, not events. t(NW) is the "
          f"calendar-time portfolio with Newey-West errors at lag = horizon "
          f"and is the primary statistic — believe it over t(cl) when they "
          f"disagree.")
    print(f"MDE@t2 is the smallest daily spread this sample could distinguish "
          f"from zero. A cell whose measured spread is well inside it has not "
          f"found nothing — it could not have found anything.")
    print(f"Multiple testing: {cells} cells reported, so ~{cells * 0.05:.1f} "
          f"clear t=2 by chance. Harvey, Liu & Zhu (2016) put the hurdle for a "
          f"new factor claim at t>3.")
    print("Survivorship: pinned constituent list is today's members; dropped "
          "names absent. Read as indicative, not precise.")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"period": args.period, "threshold": args.threshold,
             "residual": args.residual, "alpha": args.alpha, "cooldown": args.cooldown,
             "earnings_pad": args.earnings_pad, "entry_lag": args.entry_lag,
             "only_earnings": bool(args.only_earnings),
             "relvol_high": args.relvol_high, "relvol_low": args.relvol_low,
             "n_events": len(all_events), "n_symbols": len(panel),
             "cells_tested": cells, "results": results}, indent=2))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
