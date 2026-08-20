# Scope: S&P 500 benchmark cross-section

**Status: BUILT 2026-08-20.** Shipped substantially as scoped; see README
and `CLAUDE.md` for the resulting rules. Deviations noted inline below. Feasibility numbers below are
measured, not estimated. Decision and build order at the end.

## Objective

Every percentile this dashboard shows is currently computed against the ~26
companies the user happens to track. That yardstick is too small in three
measurable ways:

1. **Rank instability.** With 26 names, one ordinary bad week for a handful
   of them reshuffles everyone else's percentile — the yardstick moves, not
   the stock.
2. **Fake sector peers.** Only Financial Services clears the 4-name floor
   for a within-sector P/E comparison; everything else falls back to
   "tracked universe," which is the comparison the valuation layer was built
   to avoid.
3. **No statistical power.** The momentum-spread test on this universe
   (46 monthly observations, ~8 names per tercile leg) has a monthly spread
   standard deviation of **9.94%**, so its standard error is 1.47%/month and
   the smallest effect it could detect at t=2 is **~2.9%/month**. The
   documented market-wide momentum premium is roughly 1%/month — meaning the
   current test could not see the real effect even if it were fully present.
   The earlier "26 of 46 months, coin flip" finding is therefore
   *uninformative*, not negative.

The fix is a second, larger universe that serves as the ranking population:
the S&P 500, in full.

## Why this universe, and why "balanced" means what it means here

"Balanced for best statistical results" decomposes into five properties, and
the full S&P 500 delivers all of them without any sampling scheme:

1. **Sector coverage with adequate depth.** Constituents by GICS sector
   (fetched 2026-08-20, n=503): Industrials 83, Financials 76, Info Tech 73,
   Health Care 59, Consumer Discretionary 47, Consumer Staples 34, Utilities
   31, Real Estate 30, Materials 25, Communication Services 24, Energy 21.
   **Every sector ≥ 21 names**, so within-sector percentiles are computable
   everywhere. No sampling needed — and sampling down to equalize sector
   counts would *hurt*, because within-sector ranks want the maximum n per
   sector, and rank-based composites don't care about unequal group sizes.

2. **Sector-relative ranking for value and quality.** Raw value ratios are
   dominated by industry effects — banks always screen "cheap" on P/E,
   software always "expensive" — so cross-sector value ranks partly measure
   sector membership. The standard remedy (industry-adjusted value, per the
   academic factor-construction literature) is to rank within sector, then
   compare the within-sector percentiles across the universe. With ≥21
   names per sector this is now statistically legitimate. Momentum stays
   universe-wide (the standard Jegadeesh–Titman construction is not
   industry-adjusted). Quality: sector-relative, same reasoning as value
   (bank ROA vs software ROA is the exact artifact the current CLAUDE.md
   financials caveat exists to apologize for — sector-relative ranks retire
   that caveat instead of footnoting it).

3. **Objective membership.** Index inclusion is decided by S&P, not by us —
   the universe is pre-registered by a third party, so there is no
   cherry-picking channel through which the universe choice can flatter the
   results.

4. **Liquidity floor for data quality.** Measured field completeness on a
   20-name S&P sample (2026-08-20): trailingPE 19/20, EV/EBITDA 18/20,
   FCF 18/20, ROE/ROA/margins 20/20 — *better* than the current watchlist,
   because large caps have cleaner Yahoo records.

5. **Survivorship bias, labeled.** Today's constituent list contains only
   survivors. For the *current-day yardstick* ("where does XYL rank among
   503 large caps today") this is irrelevant. For *backfilled* cross-
   sectional tests (re-running the momentum spread over past years using
   today's members) it biases the losers' leg upward. Every backfilled
   benchmark statistic must carry a `survivorship` label, same convention as
   the existing `selection_bias` note in history.json. This repo does not
   publish return claims, so labeling (not point-in-time membership data,
   which is a paid product) is the proportionate mitigation.

## Measured feasibility (2026-08-20)

| question | result |
|---|---|
| Constituent list | Wikipedia S&P 500 table via pandas: 503 rows with GICS sectors, parses cleanly |
| Price history | `yf.download`, 100 tickers × 2y in **5.4s** → full index ≈ 30s; 100/100 usable |
| Fundamentals | `.info` at **0.39s/ticker** → 503 ≈ 3.3 min projected; **actual full run 4:53** (network variance at scale); no throttling observed |
| Refresh impact | ~+4 min on the daily CI job (currently ~1.5 min) — comfortable |
| Payload impact | benchmark names get compact records (no candles/indicators): ~100 KB total, vs ~290 KB for ONE display symbol today |

### Power improvement, stated honestly

At ~168 names per tercile leg (vs ~8 today), the *sampling* component of the
monthly spread noise shrinks ~4.6×. The *common-shock* component (market-wide
factor moves hitting both legs) does not shrink with n — that is why even
market-wide studies need long samples. Net effect: the detectable threshold
drops from ~2.9%/month toward roughly ~1%/month over the same 46-month
window — i.e., from "cannot see the documented effect" to "right at the edge
of seeing it." More calendar months keep accruing daily either way.

## Architecture: two tiers, not one bigger tier

- **Tier A — display universe** (current 42 symbols): unchanged. Full
  payloads: candles, indicators, forecasts, catalysts, charts, Ask panel.
- **Tier B — benchmark universe** (S&P 500): prices (2y) + fundamentals
  only. Never charted, never gets catalysts/forecasts. Exists purely as the
  ranking population.

Pieces:

1. `config/benchmark/sp500.csv` — the constituent list, **committed and
   pinned**, with a `scripts/refresh_constituents.py` that re-fetches from
   Wikipedia on demand (run manually or monthly, diff reviewed in the
   commit). CI never scrapes Wikipedia; the universe cannot drift silently
   under the statistics.
2. `src/market_desk/benchmark.py` — fetch closes (batch) + fundamentals
   (per-ticker loop, retry with backoff) for the benchmark list; emit
   `docs/data/benchmark.json` holding, per name: sector, momentum metrics,
   value/quality inputs, and the resulting universe + sector percentiles.
3. `factors.py` integration — the cross-section for ranking becomes
   *benchmark ∪ tracked companies*. Tracked names get: universe percentile
   (n≈503+), sector percentile (n = that GICS sector's size), and the
   existing watchlist-relative rank retained for continuity. Every consumer
   (screen table, factor tab, portfolio tilts, Ask context, daily-note
   prompt) states which population a percentile is against.
4. History: benchmark cross-sectional **breakpoints** (deciles per factor
   per date, ~30 numbers/day) get appended to history rather than 503 rows
   per day — placing any tracked name historically needs the distribution,
   not every member. Keeps `history/factors.jsonl` growth negligible.
5. UI: labels change from "tracked universe (n=26)" to "S&P 500 (n=503)" /
   "Industrials (n=83)". The `MIN_CROSS_SECTION` and thin-sector fallback
   machinery stays — it now simply never triggers for benchmark-ranked
   names.

## Risks

- **Yahoo throttling at 503 `.info` calls/day.** Not observed in the probe,
  but the endpoint is unofficial. Mitigations, in order: exponential
  backoff; on failure, reuse the previous day's benchmark fundamentals
  (these fields move quarterly — a stale day is harmless and flagged in
  meta.json); worst case, rotate a fifth of the list per day (full cycle
  weekly).
- **Constituent list drift / Wikipedia format change.** Neutralized by the
  pinned CSV; a parse failure breaks the *refresh script*, never the daily
  CI run.
- **Interpretation creep.** A 503-name percentile looks more authoritative,
  and is — but it is still a ranking, not a signal. PREDICTIONS.md governs;
  nothing in this scope adds directional capability.

## What this deliberately does not fix

- **Crash-risk sample size** — that bucket needs more *years* of stressed
  markets, not more tickers. Unchanged (n=6, `insufficient sample`).
- **Direction** — unchanged and out of scope permanently.
- **Regime validation on individual holdings** — a per-series time-series
  property; universe size is irrelevant to it.

## Effort and order

Roughly half a day: benchmark fetch + pinned constituents (2h), factors
integration + payload changes (2h), UI labels + Ask/note prompt updates
(1h), tests — pinned-list parsing, sector-relative ranks, breakpoint
history, survivorship labels (1–2h). No new dependencies beyond `lxml`
(already installed for the constituents script; CI needs it added to
requirements only if CI ever runs the refresh script, which it does not).

**Recommendation: go.** The measured costs are small (+4 min CI, +100 KB
payload), the statistical gains are large and quantified, and the two-tier
design leaves the existing dashboard untouched.

---

Tracker context, not trading advice.
