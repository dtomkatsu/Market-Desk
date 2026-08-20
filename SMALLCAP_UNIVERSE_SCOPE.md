# Scope: S&P SmallCap 600 universe

**Status: scoped 2026-08-20. Recommendation: build the constituent + factor
integration, but do NOT expect a diversification benefit — the measured case
for that specific motivation did not hold up.** All numbers below are
measured, not estimated.

## Why this was requested

To extend the ranking population below the S&P 500's floor. The S&P 500's
smallest constituent is **$6.2B**, and index additions require roughly $20B+,
so "small cap" is effectively absent from the current benchmark. The
diversification screen run against it could only offer *index-relative*
smaller names ($8–20B), which is not what small-cap normally means.

## Measured feasibility (2026-08-20)

| question | result | vs S&P 500 |
|---|---|---|
| Constituent list | Wikipedia S&P 600 table: **603 rows** with GICS sectors, parses cleanly | same source/shape |
| Price history | 5y for all: **602/603 in 44s**, one delisted (`CWEN-A`) | comparable |
| Series depth | 574/602 have ≥1200 sessions; median 1255 | comparable |
| Fundamentals speed | 0.44s/ticker → **~4.4 min** for 603 | 0.39s/ticker |
| Market cap | min $1.05B, median **$3.15B**, max $7.6B | genuinely small/mid |
| Liquidity | median **$37.9M/day** traded; **0 of 30 under $5M/day** | far better than feared |

### Data quality: the surprise

The central risk in the earlier scope was that fundamentals coverage would
degrade badly at this size. **It does not.** Sampled 30 names at random:

| field | S&P 600 | S&P 500 |
|---|---|---|
| trailingPE | 97% | 95% |
| operatingCashflow | 100% | 100% |
| returnOnEquity | 93% | 100% |
| returnOnAssets | 100% | 100% |
| operatingMargins | 100% | 100% |
| enterpriseToEbitda | 87% | 90% |
| freeCashflow | 87% | 90% |
| debtToEquity | 83% | 90% |
| sector / marketCap | 100% | 100% |

Coverage is within a few points of the large-cap benchmark on every field.
The "data gets bad below large-cap" concern was wrong for an *index-member*
universe — S&P 600 membership itself imposes a profitability and liquidity
screen that keeps records clean. It would likely be wrong for the Russell
2000, which has no such requirement; that is a different universe and should
not be assumed to behave like this one.

## The finding that changes the recommendation

The stated motivation was diversification. Measured against the actual
portfolio, **small caps do not diversify it better than large caps do:**

| | best achievable Δ portfolio vol | correlation range | median own vol |
|---|---|---|---|
| S&P 500 candidates | **−5.6pp** (KR) | −0.20 … | ~28% |
| S&P 600 candidates | **−5.1pp** (AWR) | −0.07 … +0.46 | **40%** |

Two measured reasons:

1. **No S&P 600 name reaches a meaningfully negative correlation.** The
   minimum across 589 usable candidates is **−0.07**, versus −0.20 in the
   S&P 500. Median correlation is *higher* (+0.24). Small caps share a
   common risk factor (size/liquidity beta) that keeps them tethered to
   equity drawdowns generally.
2. **Their own volatility is much higher** — median 40% vs ~28%. A
   diversifier's benefit is (low correlation) minus (added variance), and
   the extra volatility eats the correlation advantage.

The top of the S&P 600 diversification list is the *same* profile as the
large-cap list — water utilities (AWR, CWT, HTO, UTL), gas utilities (NWN,
CPK), packaged food (CAG, TR) — just smaller and more volatile versions.
The sector composition is doing the work, not the market cap.

**Conclusion for the stated use case: adding this universe would not improve
the diversification screen.** If the underlying goal is lower concentration,
the earlier finding stands — trimming the oversized position (−7.6pp) or a
broad fund beats any single-name addition at any market cap.

## Where it WOULD add real value

Three uses that are not diversification, and are genuinely served:

1. **Factor cross-section depth.** Doubling the ranking population from 503
   to ~1,100 names roughly halves the sampling error on the momentum study
   again, and small caps are where the documented factor premia are
   historically *strongest*. The momentum test currently sits at t=1.55 with
   46 observations; a wider cross-section is the only lever available that
   does not require waiting years.
2. **A genuinely different value/quality distribution.** Small-cap valuation
   ranges differ materially from large-cap. Ranking a $3B industrial against
   76 S&P 500 industrials measures partly its size, not its cheapness — the
   same industry-adjustment argument that motivated sector-relative ranking,
   applied to size.
3. **An honest opportunity set.** If a future screen ever asks "what is cheap
   in Industrials," restricting the answer to $20B+ companies is an arbitrary
   limitation, not a methodological choice.

## Design (if built)

Mirrors the existing benchmark, deliberately:

1. `config/benchmark/sp600.csv` — pinned, committed, refreshed by
   `scripts/refresh_constituents.py --index sp600` (extend the existing
   script rather than fork it; add a `--index` flag and per-index expected
   size bounds).
2. `benchmark.py` — take a list of index files rather than one. The
   `BenchmarkPopulation` gains an `index` field per member so a percentile
   can name whether it ranked in "S&P 500", "S&P 600", or the combined
   ~1,100-name cross-section.
3. **Ranking policy decision required.** Options: (a) rank everything in one
   combined pool; (b) rank within index, size-adjusted; (c) rank within
   sector *and* size band. (c) is most correct and most complex; (a) is
   simplest and reintroduces the size-confound. Recommend **(b)** initially —
   report a name's percentile within its own index, labeled — and revisit.
4. Snapshot: `history/benchmark_snapshot.json` gains a second index; ~265KB
   total. Same throttle-fallback behaviour.
5. Refresh cost: +4.4 min fundamentals on a full fetch (same-day snapshot
   reuse keeps the daily run unchanged), +44s prices.

## Risks

- **Symbol churn is higher.** The S&P 600 turns over faster than the 500;
  the pinned list will go stale sooner and needs the refresh script run more
  often. `CWEN-A` already failed as delisted in the probe — expect a handful
  of such per refresh, handled by the existing failure path.
- **Survivorship bias is worse.** Small caps are dropped from the index more
  frequently, so today's-members backfills are more biased than the S&P 500
  equivalent. Any historical statistic on this universe needs the label at
  least as loudly.
- **Combined-pool ranking would confound size with value** if design
  decision (3) is resolved carelessly.

## Recommendation

**Build it for cross-section depth and factor work — the original
motivation, diversification, is not served and should not be the
justification.** Effort roughly half a day, mostly the ranking-policy
decision in (3) rather than the plumbing. If the near-term goal remains
lowering concentration, this universe is not the answer and the trim/fund
findings already on record are.

---

Tracker context, not trading advice.
