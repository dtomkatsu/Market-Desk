# Scope: predictive analysis on top of the existing stack

**Status: Tier 1 BUILT and run, 2026-08-21 (same day it was scoped).
Results, one line each — full write-ups in PREDICTIONS.md:**

1. **Prediction registry: live.** `predictions_log.py` /
   `predictions_grade.py`, wired into the daily workflow; 54 claims on the
   record for 2026-08-21, scoreboard between markers in PREDICTIONS.md.
   First stress test already scheduled: the Oct 28 - Nov 3 cluster.
2. **Event-aware bands: shipped the test, passed it.** Flat bands cover
   51.5-51.8% of earnings weeks against an 80% claim; event-aware bands
   79.4% (tracked) / 76.5% (S&P 500, n=7,547) — 87-98% of the shortfall
   closed, no overshoot. `event_range_study.py`; core in
   `market_desk/predictions.py` (tested).
3. **PEAD on real surprises: the sixth directional null.** 16,767 ranked
   reactions, 0 of 24 pre-registered cells at t>3; small-cap long-horizon
   cells are powered nulls (MDE 0.03%/day); the only small-cap action is
   lag-1 5-day reversal that dies at lag 2 (bid-ask bounce).
4. **Conditional amplification: knob refused.** Turbulent-minus-quiet
   0.96x, t=-1.03, MDE ~9% over 16,918 reactions — and the null validates
   the independence the event-aware band arithmetic assumes.
5. **Insider gap decomposition: 83-87% gap.** The pre-registered decision
   cell (small-cap purchases, open-to-close) is +0.17% at t=+3.15 —
   nonzero, cost-exposed, survivorship-inflated; moves the nightly EDGAR
   poll to Tier 2 with numbers, ships nothing.

**Tier 2 below is NOT built. It is the parked backlog** — each item still
carries its probe gate, and nothing in it should start without re-reading
the method constraints at the bottom. The insider result above adds one
entry to it (the nightly EDGAR poll question); the PEAD null retires any
appetite for surprise-based direction beyond it. Feasibility numbers below
are measured, not estimated.

## What "predictive" now means in this repo

The sweep (PREDICTIONS.md, "Directional timing") settled the vocabulary:

- **Size and timing claims are the validated family.** Calibrated expected
  ranges, per-name event amplification, regime persistence, vol-managed
  sizing. Every Tier-1 item strengthens or composes these.
- **Direction claims start from a prior of zero.** One candidate remains
  untested with real power (surprise-sorted PEAD, below); momentum remains
  suggestive at basket scale. Everything else directional is dead here.
- **A prediction that is never graded is a decoration.** The single biggest
  gap is not a new signal — it is that the repo's calibrated claims are
  re-derived in backtests instead of being logged ex ante and scored when
  the future arrives.

## Measured feasibility (probed 2026-08-21)

| question | result | consequence |
|---|---|---|
| Earnings **surprise** history via yfinance | `Surprise(%)` present for **22-24 past events/name**, incl. small caps (NVDA 24, HLIT 22, AAMI 24) | surprise-sorted PEAD on ~1,100 names × ~20 quarters ≈ **20k events** is buildable from free data |
| Option-chain density | SPY: 30 expiries, 1.05M OI nearest; NVDA: 20 / 1.05M; **XYL: 5 / 3.4k; HLIT: 4 / 3.1k** | the 2026-08 options rejection was right for mid-caps and wrong as a universal — an OI/expiry **liquidity gate** admits a usable liquid subset |
| IV sanity on liquid names | only 60-75% of nearest-expiry IVs in (1%, 500%) even on SPY/NVDA | IV work needs per-contract quality filters, not just a liquid-name gate |
| BLS CPI schedule page | **403 to WebFetch** | CPI pin needs the Claude-in-Chrome route (or hand-copy + re-verify); dates exist and are published a year ahead |
| Bars carry `open` | yes (`fetch.Bar`) | overnight-gap vs open-to-close decompositions need no new data |

Already on disk, reusable: two pinned universes (503 + 603), earnings-date
cache for ~1,100 names, insider event caches (40.7k filings), FOMC calendar
verified through 2027, and the EW-neutral calendar-time harness with
Newey-West errors and per-cell MDEs.

## Tier 1 — build

### 1. A prediction registry, graded automatically

The centerpiece, and it is plumbing rather than research. Every daily run
already computes falsifiable, dated claims (80% weekly bands, event-window
amplification, regime persistence). Log them **ex ante** to
`history/predictions.jsonl` (committed by the workflow, like factors.jsonl);
a grader scores every claim whose window has closed; PREDICTIONS.md gains a
live scoreboard: claimed coverage vs realized, by claim type.

This converts "calibrated by walk-forward backtest" into "calibrated on
record, out of sample, accumulating" — a different epistemic category.
First dated test case: the **Oct 28 - Nov 3 cluster** (82% of the book
reporting in 7 sessions; 50% on Nov 3 alone). Effort: ~1 day.

### 2. Event-aware expected ranges (name and book level)

`expected_range` is volatility-only: a band spanning an earnings date
ignores the single best-measured thing this repo knows about that week.
Compose the two validated pieces: inflate the horizon variance by each
scheduled event's measured amplification (earnings; FOMC/payrolls for the
names that react — measured, 12 of 40 don't). Then the falsifiable test:
walk-forward coverage of event-aware vs flat bands on event-containing
windows. Per name, weekly n≈250 → binomial SE ≈2.5%, so 80%-claimed /
73%-realized miscoverage is detectable per name and trivially detectable
pooled.

Book level is the same composition plus EWMA correlations: "your book:
±X% this week, ±Y% in the cluster week" — graded by the registry. Effort:
~1 day name-level, +half-day book-level.

### 3. Surprise-sorted PEAD — the one directional test never actually run

The shock study's positive control sorted on *price reaction* (weak) and
its blackout deliberately excluded earnings. Nothing in the sweep ever
tested the actual Bernard-Thomas construction: sort on **earnings
surprise**, measure post-announcement drift. The probe confirms the data:
~20k surprise-bearing events across both universes, free.

Power, anchored to measured MDEs from the same harness: 3.2k insider events
gave MDE@t2 ≈0.036%/day at 21d EW-neutral; ~6x the events with far wider
daily breadth lands roughly **0.015-0.02%/day ≈ 0.3-0.4% per 21 sessions**.
Modern estimates of surviving PEAD run ~0.5-1.5%/quarter top-minus-bottom,
concentrated in small caps — inside detection range, especially with the
S&P 600 leg. Pre-register: terciles and deciles on standardized surprise,
horizons 5/21/63, EW-neutral, entry lag 1 and 2, both universes separately.
This is the strongest remaining directional candidate anywhere in the
repo's reach. Effort: ~1 day plus a ~40-minute surprise fetch (cacheable,
same pattern as earnings dates).

### 4. Conditional amplification: does today's state size the next event?

Amplification is currently unconditional (median over ~20 events). The
composable question: does **pre-event volatility state** predict reaction
size? Per name it is hopeless (20 events); pooled it is not: ~20k events,
each normalized by its own name's baseline, regressed on pre-event vol
percentile. If it holds, event-aware bands (item 2) get state-conditional
widths — "NVDA reports while already turbulent → expect above its own
median" — a sharper dated claim with no direction attached. Effort:
~half-day on top of item 3's event table.

### 5. Insider day-1 decomposition: gap or tradeable?

The small-cap day-1 repricing (+1.14%, t=16) is unexploitable only if it
is entirely the overnight gap. Bars carry opens, so split day 1 into
**close→open gap** vs **open→close drift** (3.2k events — power is ample).
If open→close is ~zero, the finding is closed honestly and permanently. If
it is materially positive, a same-day EDGAR poll becomes a real design
question — and only then. Effort: ~half-day. Prior: mostly gap.

## Tier 2 — parked backlog (NOT built; probe gates still apply)

- **Combined-universe momentum (+ nearness-neutralized).** ~370/leg
  terciles vs 164 today; if monthly sd scales ~1/√2.2, SE 0.50%→0.34% and
  the same +0.69% mean reads t≈2.0. Worth running as the pre-registered
  confirmation of the sweep's one surviving refinement (t=2.19), but say
  plainly: **t=3 is unreachable at this effect size without ~8 more years
  of months.** S&P 600 survivorship is worse and non-neutral. ~Half-day.
- **Vol-managed momentum overlay** (Barroso-Santa-Clara 2015): scale the
  12-1 spread by its own trailing vol; the documented effect is large
  (crash compression, not mean improvement) and `crashrisk.py` already
  imports the conditions. Judge on sd/drawdown of the spread, not t of the
  mean — 46-60 monthly obs cannot re-litigate the mean. ~1 day.
- **Options-implied moves behind a liquidity gate.** Census the whole
  tracked+600 universe (one script: expiries, OI, sane-IV share), gate at
  measured thresholds (SPY/NVDA-class passes, XYL/HLIT-class fails), then
  two tests on the survivors: IV vs EWMA for band calibration; implied
  earnings move vs historical median amplification. Only the census
  (~half-day) is committed; the rest is conditional on it.
- **Shock study on the S&P 600.** Harness + pins exist; underreaction
  effects concentrate in small caps, so the powered null on the 500 does
  not automatically transfer. ~Half-day, mostly compute.
- **CPI calendar pin.** Published a year ahead; page 403s WebFetch, so
  fetch via Claude in Chrome and pin like FOMC (with the same
  exhaustion guard). Completes the macro calendar swing_forecast shows.
  ~1 hour once fetched.
- **Nightly EDGAR poll for small-cap purchase filings** (added by the
  Tier-1 gap result): +0.17% open-to-close at t=+3.15 is the ceiling
  BEFORE costs and survivorship; ~2-3 qualifying filings/day. Only worth
  designing if a realistic cost model leaves anything, and it would feed a
  size-awareness badge, never a directional call. ~1 day to prototype.
- **Regime transition matrix.** Turn `validate_regimes` persistence into
  explicit P(state next week | state today) per name, walk-forward,
  verdict-gated — feeds the registry as a probabilistic claim. ~Half-day.
- **Industry lead-lag (Hou 2007), large→small within sector.** Both
  universes + sector labels make it buildable (~2,600 sector-weeks), but
  the modern effect size is small and the honest MDE may sit at or above
  it — run the power math first and be prepared to file it as
  could-not-have-found-anything. ~1 day.

## Do NOT build

- **Per-name seasonality.** 600 names × 12 months is a 7,200-cell fishing
  trip; at t=2, ~360 false positives. The calendar study's placebo lesson
  says the market-level version is already indistinguishable from an
  arbitrary window.
- **Analyst-revision signals from yfinance.** Recommendation history there
  is shallow and unreliable; silently wrong data is worse than missing.
- **Variant re-dredges of the five dead signals** (new thresholds, new
  windows on 52w-high / shocks / TOM / FOMC drift / insider drift). The
  sweep's cells were pre-specified; a variant that "works" now is the
  multiple-testing machine working, not a finding.
- **ML fits on the monthly cross-section.** 46-60 observations cannot
  discipline a model with more than ~2 parameters; every extra knob is a
  new seat at the data-mining table.
- **Trading the insider day-1 jump before item 5 says what it is.**

## Method constraints (inherited, non-negotiable)

1. Pre-register cells before running; report every cell; BH-FDR across each
   new battery; Harvey-Liu-Zhu t>3 for any new claim.
2. Every study prints per-cell **MDEs** — "found nothing" vs "could not
   have found anything" stay distinguishable.
3. Calendar-time portfolios, name-deduped, EW-neutral against the
   universe's own equal-weight series; Newey-West at the horizon lag.
4. No estimator may let an event price its own benchmark (the t=-4.68
   lesson); placebo and decomposition cells are mandatory, not optional.
5. Coverage guards on every multi-hundred-symbol fetch; caches committed to
   `.cache/` (gitignored), never to the repo.
6. Nothing ships a directional call to the dashboard. Registry claims are
   size, timing, and coverage claims unless a pre-registered battery clears
   the bar first.

## Sequencing

| order | item | effort | gate |
|---|---|---|---|
| 1 | prediction registry + grader | 1 day | none — plumbing |
| 2 | event-aware bands (name, then book) | 1.5 days | registry exists to grade them |
| 3 | surprise cache + PEAD battery | 1 day | pre-registered spec in this doc's terms |
| 4 | conditional amplification | 0.5 day | item 3's event table |
| 5 | insider gap decomposition | 0.5 day | none |
| 6+ | Tier 2 by appetite | — | each gated as listed |

Total Tier 1: ~4.5 days of work for one new powered directional test, two
sharpened validated predictors, one closed loose end, and — the part that
outlasts all of it — a live, graded, accumulating out-of-sample record of
every claim the dashboard makes.

---

Tracker context, not trading advice.
