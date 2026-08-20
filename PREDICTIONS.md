# What this dashboard can and cannot predict

Every number in Market Desk falls into one of two buckets: things that are
genuinely predictable and have been checked, or things that are not and are
labeled as description rather than forecast. This doc draws that line
explicitly, because the two look identical on a chart and are not.

The one-sentence version: **this dashboard predicts the size and timing of
movement — never the direction.** Everything defensible below is a claim
about the width of a distribution, not about which side of it a stock lands
on.

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

Momentum continuation is documented at market-wide, hundreds-of-names scale.
Measured on this tracked universe directly — 46 monthly observations,
top-momentum names against bottom-momentum names, one-month forward
return — the "winners" beat the "losers" in 26 of 46 months. That is
barely better than a coin flip and should not be read as a signal for any
single name. It is reported as context, never as a call.

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

## Why the split holds

This is not an arbitrary line. Directional edges that are easy to see get
traded away — if "stock with rank X will go up" were reliably true and
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
a name's regime label is even worth reading. Do not use anything here for
entry or exit timing on direction — that question is asked and answered
nowhere in this codebase, deliberately.

---

Tracker context, not trading advice. Nothing in this document or the
dashboard it describes is a recommendation to buy or sell any security.
