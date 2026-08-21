#!/usr/bin/env python3
"""Do calendar windows time the market? Turn-of-month, FOMC, payroll days.

    python scripts/calendar_study.py                  # SPY, full history
    python scripts/calendar_study.py --period 5y
    python scripts/calendar_study.py --symbol TLT
    python scripts/calendar_study.py --json out.json

The cross-sectional studies in this repo (momentum_study, high52_study,
shock_study) all fight the same power problem: five years of monthly
observations cannot resolve sub-percent effects. Calendar effects dodge it
twice over. They are claims about the MARKET — one series with thirty-plus
years of daily history, so the sample is decades rather than five years —
and their dates are known in advance, which makes them the only directional
timing claims in this repo dated before the fact.

Three effects, each with real literature behind it:

* **Turn of month** (Ariel 1987; Lakonishok & Smidt 1988; McConnell & Xu
  2008). The last session of the month plus the first three of the next —
  about a fifth of all sessions — historically carried essentially ALL of
  the U.S. market's cumulative return, with the remaining four fifths of
  days summing to roughly nothing, over samples back to 1926 and across
  countries. Mechanism debated (payroll and pension flows, rebalancing).
* **Pre-FOMC drift** (Lucca & Moench 2015). ~49bp accrued in the 24 hours
  BEFORE scheduled FOMC announcements over 1994-2011 — then weakened
  sharply after publication. This sample is entirely post-decay, so the
  cell runs as a DECAY TEST: the interesting outcome is how much of the
  published effect remains, and "none detectable" is a finding.
* **Macro announcement premium** (Savor & Wilson 2013). Scheduled
  announcement days (employment, inflation, FOMC) averaged ~11bp against
  ~1bp on other days in their 1958-2009 sample.

Honesty constraints, same rules as the sibling studies:

* **Daily bars cannot isolate the Lucca-Moench window.** The drift runs
  14:00-to-14:00; a close-to-close announcement-day bar mixes most of the
  pre-drift with the post-announcement reaction and the press conference.
  The three cells reported (day before / of / after) are what daily data
  can honestly say; the published effect is an intraday measurement.
* **FOMC dates are hardcoded from the Fed's published calendars.** The
  most recent scheduled meetings postdate this script's writing; re-verify
  when extending the list. A misdated event demotes an event day to an
  ordinary day and vice versa — both DILUTE cells toward zero; a wrong
  date cannot manufacture a result.
* **Payroll dates are DERIVED, not sourced**: first Friday of the month,
  used only when that Friday is a trading session. BLS occasionally
  shifts a release (holiday weeks, some Januaries); both failure modes of
  the rule dilute toward the null.
* **CPI days are deliberately absent.** There is no derivation rule for
  them, and hand-typing hundreds of release dates from memory is how
  silently wrong data gets made. The announcement-day cell is therefore
  labeled FOMC+payrolls, not the full Savor-Wilson set.
* **One market, one path.** Time-series claims on a single index have no
  survivorship problem, for once — but also no cross-section to
  diversify a regime away. The last five years hold one hiking cycle, one
  bear market, one melt-up. Full-history cells are the evidence; the 5y
  cells say whether the present still resembles it.

**Multiple testing.** Nine cells; at t=2 roughly 0.5 clear by chance, and
Harvey, Liu & Zhu (2016) put the hurdle for a new claim at t>3. The
placebo cell — a mid-month window the same width as TOM — exists to catch
a broken harness, and its correct value is zero.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import warnings
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
warnings.filterwarnings("ignore")

from market_desk.fetch import fetch_history            # noqa: E402

SESSIONS_5Y = 5 * 252

# Announcement (second) day of every scheduled FOMC meeting since the 5y
# sample can begin. Source: the Fed's published meeting calendars
# (federalreserve.gov/monetarypolicy/fomccalendars.htm), which run a year+
# ahead. Nov 2024 really was Thursday the 7th — the election pushed it.
# Extend by hand and re-verify against the site when the sample grows.
FOMC_ANNOUNCEMENTS = [
    "2021-09-22", "2021-11-03", "2021-12-15",
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15",
    "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14",
    "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
    "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29",
]


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def one_sample(xs: list[float]) -> dict:
    n = len(xs)
    if n < 3:
        return {"n": n}
    m = statistics.mean(xs)
    se = statistics.stdev(xs) / math.sqrt(n)
    return {"n": n, "mean": m, "se": se, "t": (m / se if se else 0.0),
            "mde_t2": 2 * se}


def welch(event: list[float], quiet: list[float]) -> dict:
    """Two-sample difference of means, unequal variances."""
    na, nb = len(event), len(quiet)
    if na < 3 or nb < 3:
        return {"n_event": na, "n_quiet": nb}
    ma, mb = statistics.mean(event), statistics.mean(quiet)
    se = math.sqrt(statistics.variance(event) / na
                   + statistics.variance(quiet) / nb)
    return {"n_event": na, "n_quiet": nb, "mean_event": ma, "mean_quiet": mb,
            "diff": ma - mb, "se": se, "t": ((ma - mb) / se if se else 0.0),
            "mde_t2": 2 * se}


# ---------------------------------------------------------------------------
# Calendar machinery
# ---------------------------------------------------------------------------

def session_returns(bars) -> tuple[list[str], list[float]]:
    """Daily log returns (dividend- and split-adjusted upstream)."""
    dates, rets = [], []
    for i in range(1, len(bars)):
        a, b = bars[i - 1].close, bars[i].close
        if a > 0 and b > 0:
            dates.append(bars[i].date)
            rets.append(math.log(b / a))
    return dates, rets


def trim_partial_months(dates: list[str], rets: list[float]):
    """Drop the first and last calendar months, which are almost certainly
    partial — a sample starting mid-January would otherwise call January's
    20th trading day its 'first', and every position-based flag after it
    would be wrong."""
    months = [d[:7] for d in dates]
    if not months:
        return dates, rets
    first, last = months[0], months[-1]
    keep = [i for i, m in enumerate(months) if m not in (first, last)]
    return [dates[i] for i in keep], [rets[i] for i in keep]


def classify_days(dates: list[str], tom_k: int, placebo_start: int = 9):
    """TOM flag (last session of a month or first `tom_k` of one) and a
    placebo flag — a mid-month window of the same width (tom_k + 1),
    starting at the `placebo_start`-th session of the month."""
    n = len(dates)
    months = [d[:7] for d in dates]
    pos, p = [], 0
    for i, m in enumerate(months):
        p = 0 if i == 0 or months[i - 1] != m else p + 1
        pos.append(p)
    tom, placebo = [False] * n, [False] * n
    width = tom_k + 1
    for i in range(n):
        last_of_month = i < n - 1 and months[i] != months[i + 1]
        tom[i] = last_of_month or pos[i] < tom_k
        placebo[i] = placebo_start <= pos[i] < placebo_start + width
    return tom, placebo


def monthly_paired_diffs(dates, rets, flags) -> list[float]:
    """One observation per calendar month: mean flagged daily return minus
    mean unflagged. Pairing by month is the primary estimator — it is
    robust to volatility clustering across months, where a pooled t-test
    would quietly treat 2022 and 2024 days as exchangeable."""
    by_month: dict[str, tuple[list, list]] = defaultdict(lambda: ([], []))
    for d, r, f in zip(dates, rets, flags):
        by_month[d[:7]][0 if f else 1].append(r)
    out = []
    for _, (yes, no) in sorted(by_month.items()):
        if len(yes) >= 2 and len(no) >= 5:
            out.append(statistics.mean(yes) - statistics.mean(no))
    return out


def first_friday(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    d = date(y, m, 1)
    return (d + timedelta(days=(4 - d.weekday()) % 7)).isoformat()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def show_window(label: str, dates, rets, flags, results: list, key: str):
    tom_rs = [r for r, f in zip(rets, flags) if f]
    rest_rs = [r for r, f in zip(rets, flags) if not f]
    diffs = monthly_paired_diffs(dates, rets, flags)
    st = one_sample(diffs)
    share_days = len(tom_rs) / len(rets) if rets else 0.0
    total = sum(rets)
    share_ret = (sum(tom_rs) / total) if abs(total) > 1e-9 else float("nan")
    print(f"\n{label}")
    print(f"  window days {len(tom_rs)} ({share_days:.0%} of {len(rets)} sessions), "
          f"months paired {st.get('n', 0)}")
    if tom_rs and rest_rs:
        print(f"  mean daily return   in-window {statistics.mean(tom_rs) * 100:+.3f}%   "
              f"rest {statistics.mean(rest_rs) * 100:+.3f}%")
    print(f"  share of cumulative log return carried by the window: {share_ret:+.0%}")
    if "t" in st:
        print(f"  monthly paired diff {st['mean'] * 100:+.3f}%/day   t = {st['t']:+.2f}   "
              f"MDE@t2 {st['mde_t2'] * 100:.3f}%/day")
    results.append({"cell": key, "kind": "paired_monthly", **st,
                    "share_days": share_days, "share_return": share_ret,
                    "mean_in": statistics.mean(tom_rs) if tom_rs else None,
                    "mean_rest": statistics.mean(rest_rs) if rest_rs else None})


def show_event(label: str, cell: dict, results: list, key: str):
    if "t" not in cell:
        print(f"  {label}: too few observations ({cell})")
        results.append({"cell": key, "kind": "welch", **cell})
        return
    print(f"  {label:22} n={cell['n_event']:4}   mean {cell['mean_event'] * 100:+.3f}%   "
          f"vs quiet {cell['mean_quiet'] * 100:+.3f}%   diff {cell['diff'] * 100:+.3f}%   "
          f"t = {cell['t']:+.2f}   MDE@t2 {cell['mde_t2'] * 100:.3f}%")
    results.append({"cell": key, "kind": "welch", **cell})


# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", default="SPY")
    ap.add_argument("--period", default="max",
                    help="full history by default — the whole point of a "
                         "single-series study is the long sample")
    ap.add_argument("--tom-days", type=int, default=3,
                    help="first N sessions of the month in the TOM window "
                         "(plus the prior month's last session)")
    ap.add_argument("--json")
    args = ap.parse_args(argv)

    print(f"fetching {args.symbol} ({args.period})")
    bars, failures = fetch_history([args.symbol], period=args.period)
    series = bars.get(args.symbol) or []
    if len(series) < 300:
        raise SystemExit(f"{args.symbol}: {len(series)} sessions downloaded "
                         f"({failures.get(args.symbol, 'too few to study')}). "
                         f"Rate limited? Wait a few minutes and retry.")
    dates, rets = trim_partial_months(*session_returns(series))
    print(f"  {len(dates)} sessions, {dates[0]} .. {dates[-1]} "
          f"(partial edge months dropped)")

    results: list[dict] = []

    # ---- turn of month -----------------------------------------------------
    print("\n" + "=" * 74)
    print(f"TURN OF MONTH — last session + first {args.tom_days} "
          f"(McConnell & Xu construction)")
    print("=" * 74)
    tom, placebo = classify_days(dates, args.tom_days)
    show_window("Full history", dates, rets, tom, results, "tom_full")

    d5, r5 = trim_partial_months(dates[-SESSIONS_5Y:], rets[-SESSIONS_5Y:])
    tom5, _ = classify_days(d5, args.tom_days)
    show_window("Last ~5 years", d5, r5, tom5, results, "tom_5y")

    show_window("Placebo: mid-month window, same width (harness check — "
                "correct value is zero)", dates, rets, placebo,
                results, "placebo_midmonth")

    # ---- events ------------------------------------------------------------
    index = {d: i for i, d in enumerate(dates)}

    fomc_idx, fomc_missing = [], []
    for a in FOMC_ANNOUNCEMENTS:
        i = index.get(a)
        (fomc_idx.append(i) if i is not None else fomc_missing.append(a))
    in_range = [a for a in fomc_missing if dates[0] <= a <= dates[-1]]

    months = sorted({d[:7] for d in dates})
    nfp_idx, nfp_skipped = [], 0
    for ym in months:
        i = index.get(first_friday(ym))
        if i is None:
            nfp_skipped += 1        # holiday Friday etc. — skipped, not guessed
        else:
            nfp_idx.append(i)

    # Era-matched baseline: quiet sessions inside the FOMC-covered window.
    # Comparing 2021-26 announcement days against a 1993-2026 baseline would
    # measure the era, not the announcement.
    era_start = min(fomc_idx) if fomc_idx else 0
    excluded = set()
    for i in fomc_idx:
        excluded.update((i - 1, i, i + 1))
    excluded.update(nfp_idx)
    quiet_recent = [rets[i] for i in range(era_start, len(rets))
                    if i not in excluded]
    quiet_full = [rets[i] for i in range(len(rets)) if i not in excluded]

    print("\n" + "=" * 74)
    print("FOMC ANNOUNCEMENTS — decay test of Lucca-Moench (49bp pre-drift, "
          "1994-2011, intraday)")
    print("=" * 74)
    print(f"  {len(fomc_idx)} scheduled announcements in sample "
          f"({dates[min(fomc_idx)]} .. {dates[max(fomc_idx)]}); "
          f"{len(in_range)} hardcoded dates NOT found in the data"
          + (f" — CHECK THESE: {in_range}" if in_range else ""))
    print(f"  baseline: {len(quiet_recent)} quiet sessions in the same era "
          f"(no FOMC±1, no payroll days)")
    for off, label, key in ((-1, "day before (t-1)", "fomc_tm1"),
                            (0, "announcement day (t)", "fomc_t0"),
                            (+1, "day after (t+1)", "fomc_tp1")):
        cell = [rets[i + off] for i in fomc_idx if 0 <= i + off < len(rets)]
        show_event(label, welch(cell, quiet_recent), results, key)
    print("  (daily bars mix most of the published pre-drift into the t cell "
          "along with the reaction; see docstring)")

    print("\n" + "=" * 74)
    print("PAYROLL FRIDAYS — first-Friday rule "
          f"({nfp_skipped} months skipped where that Friday wasn't a session)")
    print("=" * 74)
    show_event("full history", welch([rets[i] for i in nfp_idx], quiet_full),
               results, "nfp_full")
    recent_nfp = [rets[i] for i in nfp_idx if i >= era_start]
    show_event("FOMC-era only", welch(recent_nfp, quiet_recent),
               results, "nfp_recent")

    print("\n" + "=" * 74)
    print("ANNOUNCEMENT DAYS COMBINED — FOMC + payrolls vs quiet, same era "
          "(Savor-Wilson premium, minus the CPI days this repo can't date)")
    print("=" * 74)
    combined = [rets[i] for i in sorted(set(fomc_idx) | {i for i in nfp_idx
                                                         if i >= era_start})]
    show_event("announcement days", welch(combined, quiet_recent),
               results, "announcement_combined")

    cells = len(results)
    print(f"\nMultiple testing: {cells} cells reported, so ~{cells * 0.05:.1f} "
          f"clear t=2 by chance; Harvey, Liu & Zhu (2016) put the hurdle for "
          f"a new claim at t>3. The placebo cell is spent on checking the "
          f"harness, not on a finding.")
    print("One market, one path: no survivorship here, but no cross-section "
          "either — a regime cannot be diversified away, only outlived.")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"symbol": args.symbol, "period": args.period,
             "tom_days": args.tom_days, "sessions": len(dates),
             "first": dates[0], "last": dates[-1],
             "fomc_in_sample": len(fomc_idx), "fomc_unmatched": in_range,
             "nfp_events": len(nfp_idx), "nfp_skipped_months": nfp_skipped,
             "cells_tested": cells, "results": results}, indent=2))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
