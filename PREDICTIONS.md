# What this dashboard can and cannot predict

Every number in Market Desk falls into one of two buckets: things that are
genuinely predictable and have been checked, or things that are not and are
labeled as description rather than forecast. This doc draws that line
explicitly, because the two look identical on a chart and are not.

The one-sentence version: **this dashboard predicts the size and timing of
movement — never the direction.** Everything defensible below is a claim
about the width of a distribution, not about which side of it a stock lands
on.

<!-- prediction-registry:begin -->
## Live record — every claim graded after the fact

Maintained automatically: `predictions_log.py` writes each day's claims to `history/predictions.jsonl` before their windows open; `predictions_grade.py` scores them when the windows close. No backtest below — only claims made on the record and what then happened.

*No claims have matured yet; 54 pending.*
<!-- prediction-registry:end -->

## Predictions that are genuinely defensible

### 1. How far a stock is likely to move over a given window

The expected-range bands (Timing tab, and the per-symbol timing card) are
real, falsifiable predictions — "80% chance SPY stays within ±2.2% this
week." The multiplier is calibrated per name by walk-forward coverage: I
checked the band against every historical week in the series and confirmed
roughly 4 in 5 land inside it, which is what "calibrated" means. A
calibrated prediction also tells you its own failure rate — this one should
miss about one week in five, and if it misses more often than that, it is
broken.

This is validated on the index/sector ETFs and large caps in the tracked
universe. On roughly a third of names — including 4 of the 5 current
holdings (XYL, TT, CMPS, NXT) — walk-forward testing found **no separation**
between the regime label and what actually followed. On those names the
regime is a description of the present, not a forecast, and the dashboard
marks it as such (dimmed rows, `insufficient sample` verdicts) rather than
hiding the distinction.

### 2. When the big moves will happen, for scheduled events

The sharpest prediction in the whole system. Companies report earnings on
known dates, and each name's own history shows how much it typically moves
when they do:

| symbol | typical earnings-day move | vs. ordinary day | next report |
|---|---|---|---|
| XYL | ~4.5% | 5.1× | Nov 3 |
| TT | ~3.8% | 4.1× | Oct 29 |
| NXT | ~10% | 4.9× | Oct 28 |
| CMPS | ~5.9% | 2.1× | Nov 3 |
| SU | ~2.1% | 1.8× | Nov 3 |

"XYL will very likely move several percent on Nov 3" is a real, dated
forecast. The sign of that move is not predictable and the dashboard does
not attempt it — but the whole book clustering into one week (Oct 28–Nov 3)
is itself a usable finding: that week is predictably the most volatile
stretch of the quarter for this portfolio.

The reaction session accounts for reporting time — a company announcing
after the close moves the *next* trading session, not the announcement day
itself. Getting this wrong (measuring the announcement date for everything)
understated after-close reporters badly during development: NVDA appeared
to react only 0.7× on earnings when the real figure is 2.9×.

### 3. That elevated volatility will persist

The most robust regularity here, and the reason the bands in (1) work at
all: on the validated names, a turbulent day is followed by moves roughly
1.3–1.7× the size that follow a quiet day. This is a genuine forecast — "the
next week or two will likely stay choppy" — even though "choppy in which
direction" is not answerable.

### 4. Wide-band, long-horizon levels

The damped-trend forecast (borrowed from Census-Forecaster, itself
walk-forward calibrated) gives a 90% band for 3/6/12-month horizons — e.g.
"90% chance MATX is between $136 and $391 in a year." The band's width *is*
the finding; it is deliberately enormous, because equity prices are close
to a random walk and pretending otherwise would be dishonest. The center of
the band is close to "current price, drifted slightly," not a confident
target.

### 5. Weak, basket-scale tendencies (use with real caution)

Momentum continuation is documented at market-wide scale, and is now measured
here rather than assumed — `scripts/momentum_study.py`, S&P 500 cross-section,
5 years, 46 monthly observations:

| | mean spread | t | positive months |
|---|---|---|---|
| terciles (~164/leg) | +0.69%/mo | +1.39 | 31/46 (67%) |
| deciles (~49/leg) | +1.61%/mo | +1.61 | 28/46 (61%) |

**These numbers drift with the data vintage, and that is itself worth
knowing.** The same untouched script produced +0.82%/mo (t=1.55) on a fetch
one day earlier; re-anchoring a 5-year window moves the monthly sample grid
and every spread with it. Against a monthly standard deviation of 3.36% a
0.13%/mo wander is ordinary noise — but a figure quoted to two decimals
invites more confidence than a number that moves that much between Tuesday
and Wednesday deserves. Read the sign and the order of magnitude, not the
decimals.

**Suggestive, not established.** t=1.39 is short of the conventional t=2, so
this could still be chance. What changed versus the earlier watchlist-only
test is precision, not the answer: the mean barely moved (+0.80% → +0.82%)
while the standard deviation fell from 9.94% to 3.58%. The earlier "26 of 46,
coin flip" reading was **uninformative** — that test could not have detected a
1%/month effect. This one could, and sees something around 0.7%/month.

Two limits to keep attached. Reaching t=2 at this effect size needs ~96
monthly observations (~8 years); there are 46. And the constituent list is
today's members, so dropped names (usually poor performers) are absent while
recently added ones qualified after strong runs — survivorship pushes the
result in both directions and the net is unknown.

Critically, this is a **basket** result: ~164 names per leg. It says nothing
about whether any individual holding will outperform, and the dashboard never
presents a single name's momentum rank as a call.

## What is not predictable, and is not attempted

- **Direction**, at any horizon inside about a year. Nothing in the codebase
  produces a buy/sell signal or a directional call, on purpose.
- **Stock selection / alpha.** A few dozen tracked names cannot statistically
  support "this one will outperform" — even the market-wide factor evidence
  this dashboard draws on describes basket returns, not individual picks.
- **Anything derived from options data.** Tested and rejected: the option
  chains available for this universe are too thin to trust (one probe
  returned nine contracts for a holding with an implied volatility of
  0.00001). Silently wrong data is worse than missing data, so this input
  is not used at all.
- **Momentum-crash *events* specifically.** The crash-risk module classifies
  the *conditions* under which momentum has historically reversed (market
  stress + high volatility), but the local sample of genuinely stressed
  periods is far too small (6 observations) to validate the pattern itself
  — that comes from published research, not from this data, and the
  dashboard says so every place it appears.

## Directional timing: five designs, tested and rejected

Everything above was, until 2026-08-20, a *prior*: direction is not
forecastable, so the codebase does not try. That is a comfortable thing to
assert and an uncomfortable thing to check, because the check can come back
the other way. This section is the check. Five candidate directional-timing
signals were pre-specified from the empirical literature, implemented as
standalone study scripts alongside `momentum_study.py`, and run. None
cleared the bar.

| signal | script | result |
|---|---|---|
| 52-week-high nearness | `high52_study.py` | inverts; momentum survives the joint test instead |
| Volume-confirmed price shocks | `shock_study.py` | powered null at 2.5σ and 3.0σ |
| Calendar windows (TOM / FOMC / payrolls) | `calendar_study.py` | null; turn-of-month is placebo-equivalent |
| Volatility-managed sizing | `volmanaged_study.py` | best survivor, t=1.93 — and it never calls direction |
| Insider Form 4 purchases | `insider_study.py` | disclosure reprices the stock; drift after it is zero |

Every script prints its own **minimum detectable effect**, because "found
nothing" and "could not have found anything" are different sentences and
only one of them is evidence.

The insider study was later re-run on the S&P SmallCap 600 — the segment
where the literature actually puts the effect — and that run is the one
that produced the session's only large t-statistics. Working out what they
were measuring is the most instructive part of this whole section.

### 52-week-high nearness inverts

George & Hwang (2004) report that nearness to the 52-week high predicts
returns and *subsumes* Jegadeesh-Titman momentum in a joint test. On the
pinned S&P 500 cross-section both halves come out backwards. Nearness is
negative in every construction tried (intraday vs closing highs, windows
aligned with momentum's skipped month or not, 1- and 3-month horizons) and
never once positive. In the nested double sort it is momentum that survives
— +0.90%/mo at t=+2.19, *better* than raw momentum's +0.69% at t=+1.39,
because neutralizing nearness strips a noisy component out of the sort
(monthly sd falls 3.36% → 2.79%). That is variance reduction, not new alpha,
and it is the one practical thing this study produced.

Held against equal momentum, nearness runs to −1.36%/mo at t=−3.01 in the
window-aligned form: at equal 12-month momentum, the names furthest *below*
their own 52-week high outperformed. Read economically that is a
drawdown-rebound effect, and the sample is one drawdown-and-recovery cycle
(2022 down, 2023-25 up). It is exactly the shape a single cycle
manufactures, it is one cell out of 24 across the robustness variants, and
it is not adopted.

### Unscheduled shocks do not drift

The non-earnings analogue of post-earnings drift: a stock moves 3σ on no
scheduled catalyst — does it continue, or give it back? Pritamani & Singal
(2001) and Gutierrez & Kelley (2008) say the answer turns on volume, with
high-volume moves continuing and low-volume moves reversing. Earnings
sessions are excluded through `announcement_date`, so the after-close rule
that owns the reaction session owns the blackout too; names whose earnings
dates fail to fetch are **dropped rather than included unscreened**, because
an unscreened name silently readmits precisely the events the study exists
to exclude.

4,099 shocks at 3σ and 6,792 at 2.5σ, across 499 names. The largest |t| in
any of the eighteen cells is 1.23. This is a powered null, not a shrug: the
design could resolve roughly 0.8% over ten sessions and 1.1% over
twenty-one, against published effects of 2-3% over twenty. And the volume
hypothesis fails *directionally* as well as statistically — the
volume-confirmed bucket, predicted to continue, carries the more negative
point estimate at both 10 and 21 days.

### Calendar windows: the placebo is the finding

Turn-of-month (Ariel 1987; McConnell & Xu 2008) is the strongest calendar
claim in the literature — those four sessions historically carried
essentially the market's entire cumulative return. On SPY back to 1993, 401
paired months: the turn-of-month window earns +0.037%/day over the rest of
the month at t=+1.21, and a mid-month window of identical width earns
+0.033%/day at t=+1.17. The window holds 19% of sessions and 33% of
cumulative return; the placebo holds 19% and 31%. On synthetic data with a
planted effect the placebo stays flat while the real window lights up at
t=3.6-5.6, so this is not a broken harness — in the SPY era the turn of the
month simply carries no premium over an arbitrary window of the same width.
In the last five years its point estimate is negative.

FOMC announcements are flat in all three cells (day before, of, after), with
the honest caveat attached: at n=40 only the full published 49bp
pre-announcement drift was detectable, so a decayed remnant would be
invisible — and daily bars cannot isolate a 14:00-to-14:00 intraday window
in any case. Payroll Fridays are the only calendar cell with a pulse,
+0.106%/day over 33 years at t=+1.64, and it is dead in the recent era. The
combined announcement-day premium in the FOMC era is −0.001% at t=−0.01.

### Volatility-managed sizing: the one that survives, and why it is allowed

This is the only candidate that does not predict direction. The weight never
goes negative; it decides *how much* of a position to hold, from the one
regularity `validate_regimes` has actually confirmed locally — volatility
clusters. Moreira & Muir (2017) show that scaling exposure by inverse
variance earns positive alpha against buy-and-hold.

On SPY 1994-2026, against a baseline of +12.05%/yr at 18.77% vol, Sharpe
0.51, max drawdown −55%:

| construction | alpha/yr | t | Sharpe | maxDD | turnover | breakeven cost |
|---|---|---|---|---|---|---|
| matched-vol (academic, uncapped) | +4.47% | +1.81 | 0.58 | −50% | 8.4×/yr | 0.54% |
| no leverage, monthly | +1.71% | +1.93 | 0.60 | −29% | 1.8×/yr | 0.94% |
| daily EWMA λ=0.94, no leverage | +0.79% | +0.85 | 0.55 | −38% | 4.3×/yr | 0.18% |
| no leverage, last ~5y | +2.22% | +1.19 | 0.73 | −13% | 1.5×/yr | 1.45% |

No cell clears t=2, so by this document's standard the alpha is **not a
claim**. But the part that needs no alpha at all is mechanical: the same or
better Sharpe at 60% of the volatility, with the worst drawdown cut from
−55% to −29%. That is what vol-targeting buys when alpha is exactly zero,
and it follows from clustering alone. It is also implementable — 1.8×
annual turnover against a 0.94% breakeven one-way cost, on the most liquid
instrument in existence — where every rejected signal above would have
traded at the worst possible moment for spread. Note that the daily variant
has *less* alpha and a *worse* drawdown than the monthly one: chasing
volatility daily whipsaws, and the monthly signal the literature validated
is the one that reproduces here. Cederburg et al. (2020) stays attached:
vol-managed alphas are fragile out-of-sample across most factors, the market
factor is the strongest case, and one path cannot settle it.

### Insider purchases: the disclosure moves the stock, and that is all

The SEC's structured insider-transaction data sets, 19 quarters
(2021q3-2026q1), 40,728 Form 4 filings, run against two universes: the
S&P 500 and the pinned S&P SmallCap 600, which is where the literature
concentrates the effect. Above $10k: 2,363 purchase and 25,773 sale events
in large caps, 3,187 and 17,352 in small caps — the classic asymmetry, since
people sell for houses and taxes but buy for one reason. The clock starts at
the **filing** date, not the transaction date: the trade is inside
information until filed, and one filing in the 2024q1 set arrived fifteen
months late, which measured from its trade date would look like astonishing
foresight.

The small-cap run first reported purchases +1.26% over 21 sessions and a
purchases-minus-sales spread of +2.11% at t=+4.73 — comfortably past the
t>3 hurdle, and the only result in this section that ever cleared it. It
does not survive being taken apart. Decomposed into non-overlapping
segments and measured against the right benchmark:

| purchases, small-cap, equal-weight-neutral | return | t |
|---|---|---|
| day 0 — the filing session | +0.29% | +4.49 |
| day 1 — first session after | +1.14% | **+16.07** |
| days 2-21 — post-disclosure drift | +0.33% | +1.00 |
| days 2-63 — post-disclosure drift | −0.06% | −0.09 |

The entire effect is the market repricing a disclosure. Day 0 is the filing
session itself, which cannot be traded on without knowing what the filing
says before it is filed — that being the exact thing Form 4 discloses. Day
1's return is close-to-close across the after-hours window where most Form
4s land, so it is a gap, not an entry. **Post-disclosure drift — the only
tradeable claim, and the only one Cohen, Malloy & Pomorski actually make —
is zero at both horizons.**

The large-cap run reproduces the same shape at the size the literature
would predict: day 1 +0.42% (t=+7.07) against small-cap +1.14%, thinner
names repricing harder, with drift again indistinguishable from zero
(+0.17%, t=+0.61 over days 2-21). Two universes, one structure, and the
information shows up entirely in the jump.

This is a better result than the null it replaces. The earlier large-cap
reading — "right-signed but underpowered" — was correct that the design
could not resolve a drift effect there; the small-cap universe resolves it
precisely, and the answer is that there is no drift to resolve. What there
is instead is a clean, very large, and completely unexploitable measurement
of how much a disclosure is worth.

Survivorship bites hardest in this universe and is *not* neutral: S&P 600
turnover is faster, index leavers did badly, and insider purchases in those
names are missing, which would overstate a positive purchase result.
Notably, the purchases-minus-sales spread is the cell least exposed to it,
since both legs are drawn from the same surviving universe and the bias
largely differences out.

### What the studies taught about method

Five lessons outlived the signals, and all five are enforced in code.
Three of them were found only because a result looked *too good*, which is
the practical argument for keeping placebo and decomposition cells in a
harness rather than only outcome cells:

* **An estimator can manufacture a t of −4.68.** The shock harness first
  reported violent post-earnings *reversal* at t=−4.68 — the most
  significant-looking number produced in any of these studies, and pure
  artifact. Its market-model residual subtracted a trailing-252-day alpha,
  so each shock sat inside the window pricing its own following year: one
  +8% day lifts estimated daily alpha by ~8%/252 and that gets subtracted
  from every subsequent residual, about −2% over 63 sessions conjured from
  nothing. Predicted −2%, measured −1.77%. Dropping alpha flips the same
  cell to +1.04. Entry lags of 1, 2 and 3 sessions were checked first and
  ruled out the bid-ask bounce, which is what sent the search to the
  estimator. `--alpha include` is retained so the artifact can be
  reproduced rather than argued about.
* **Overlapping windows inflate their own t.** A 63-session forward window
  sampled every 21 sessions overlaps three deep; consecutive observations
  share two-thirds of their returns and t is inflated by about √3.
  `high52_study.py` prints the deflation factor rather than leaving a
  reader to notice.
* **An event-weighted portfolio is not a portfolio.** The calendar-time
  legs first averaged one residual per *event*, so a name with twenty live
  filings was averaged in twenty times and a handful of heavily-filed
  tickers could have carried the entire result. Small-cap sale filings run
  to 29 per name here. The give-away was a breadth number of 902 names/day
  in a 603-name universe — an impossibility printed in the output for
  several runs before anyone read it. Both harnesses now count each NAME
  once per session. Fixing it *raised* the t-statistics, which is worth
  saying: the over-weighted names were adding noise, not the finding.
* **The benchmark has to match the portfolio's weighting.** An
  equal-weighted event leg residualized against a CAP-weighted index ETF
  inherits a persistent equal-weight-minus-cap-weight tilt. Measured on
  this universe it runs −0.016%/day (t=−3.13) — about −1.0% over 63
  sessions, handed free to any leg broad enough to resemble the index. It
  accounted for roughly two thirds of the raw insider *sale* effect. The
  fix is a placebo the study now always computes: the whole universe,
  equal-weighted, no event filter. Every cell is reported net of it.
* **A throttled fetch is silently partial.** yfinance rate-limited a repeat
  500-symbol pull and a study ran happily on 298 names, caught only because
  a *lower* shock threshold reported *fewer* names than a higher one, which
  is impossible. `shock_study.py` and `insider_study.py` now refuse below
  90% coverage. Silently wrong data is worse than missing data — the same
  rule that keeps options out of this codebase.

## Why the split holds

This is not an arbitrary line, and as of 2026-08-20 it is not only an
argument — five pre-specified directional signals were tested and none
survived, while the volatility-side machinery kept validating. Directional
edges that are easy to see get traded away — if "stock with rank X will go up" were reliably true and
simple to compute, it would already be priced in. Magnitude and timing
patterns survive that pressure much better, because knowing volatility will
be elevated next week does not tell a trader which way to position, so
there is less arbitrage force acting against publishing it. Scheduled
events (earnings dates) are a similar case: everyone already knows the date,
so there's no edge in the date itself — the edge, if any, is in sizing risk
around it correctly, which is exactly what the reaction-amplification
numbers are for.

## Practical takeaway

Use the predictable quantities for risk decisions: how large a position can
be before an ordinary week hurts, which week to expect turbulence, whether
a name's regime label is even worth reading. The sizing studies above point
the same way: the only lever that measured up is one that never names a
direction, and its clearest benefit — a drawdown cut roughly in half at
equal Sharpe — needs no alpha claim to hold. Do not use anything here for
entry or exit timing on direction. That question is now asked in five
places in this codebase and answered "no" in all five, which is a stronger
statement than the deliberate silence it replaces.

---

Tracker context, not trading advice. Nothing in this document or the
dashboard it describes is a recommendation to buy or sell any security.
