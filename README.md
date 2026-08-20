# Market Desk

A stock tracking dashboard: TradingView-style candlestick charts, valuation
ranked against peers, volume analytics, and a daily analysis note written by
Claude. Static front end on GitHub Pages, Python pipeline in GitHub Actions.

**Live dashboard:** https://dtomkatsu.github.io/Market-Desk/

> Tracker context, not trading advice. Nothing here is a recommendation to buy
> or sell any security, and no figure on the page is a price target.

## What it does

**Charts.** A TradingView-style interface built on [KLineChart][klc]
(Apache-2.0, vendored — no CDN dependency): candlesticks with a drawing-tool
rail (trend lines, rays, channels, Fibonacci retracements, price lines, text
notes, magnet snapping, right-click or one-button erase), toggleable indicator
panes (volume, relative volume, RSI-14, MACD, OBV, KDJ), price overlays (MA
20/50/200, EMA, Bollinger, rolling VWAP), daily/weekly/monthly timeframes, and
the damped-trend forecast drawn as a shaded 90% cone past the last bar.

**Valuation.** The rule the whole valuation layer is built around: *a multiple
only means something against a comparison set.* Nothing reports a raw P/E as a
verdict. Every multiple comes back as a percentile inside a named peer group —
the sector when enough tracked names share one, the whole tracked universe
otherwise, and the label always says which. Funds and ETFs are excluded from
peer groups entirely, since a holdings-weighted P/E is not comparable to a
company's. Negative multiples are dropped rather than ranked, so a loss-maker
never sorts as the cheapest name on the board.

**Volume.** Relative volume against a trailing baseline that excludes the
current bar, volume trend (20-day against 60-day), up/down volume ratio, dollar
volume, OBV, the Chaikin accumulation/distribution line, and a price-volume
divergence label — an advance on expanding volume reads differently from the
same advance on contracting volume. The divergence verdict is a descriptive
heuristic, not a backtested signal, and it is labeled as such everywhere.

**Forecasts.** A damped-drift projection with a 90% band, borrowed from
[Census-Forecaster](https://github.com/dtomkatsu/Census-Forecaster) (see below).

**Benchmark cross-section.** Percentiles rank against the **S&P 500** (503
constituents, pinned in `config/benchmark/sp500.csv`), not just the tracked
watchlist. Momentum ranks index-wide (the standard construction is not
industry-adjusted); value and quality rank **within each name's own sector**,
because raw multiples are dominated by industry effects — banks always screen
cheap on P/E, software always expensive, so a cross-sector value rank partly
measures sector membership. Every GICS sector carries ≥20 names, so
within-sector ranking is available everywhere.

The effect is not cosmetic. BOH's value score falls 0.88 → 0.58 once it is
compared with 70 actual financials rather than a tech-heavy watchlist, and
HE's value-trap flag *clears* — its quality rises 0.25 → 0.40 against 31
utilities, which structurally run low ROA and high leverage. That also retires
the financials caveat this repo previously had to attach to bank trap flags.

The constituent list is committed and refreshed manually
(`scripts/refresh_constituents.py`), never scraped by CI: the benchmark is the
yardstick every percentile is measured against, and a yardstick that changes
silently under the statistics is worse than a stale one. A committed snapshot
(`history/benchmark_snapshot.json`) makes a throttled fetch fall back to the
previous day rather than silently reverting every percentile to
watchlist-relative.

**Factors.** Cross-sectional momentum, value, and quality, built the way the
empirical literature says they work — including the documented traps:

- *Momentum* is the academic **12-1 construction**: the return from twelve
  months ago to *one month ago*. The most recent month mean-reverts, so it is
  excluded from the formation window and shown separately as the reversal
  window; a last month that fights the 12-1 signal gets flagged.
- *Value* is deliberately **multi-ratio** — a rank average of EBITDA yield
  (1÷EV/EBITDA), free-cash-flow yield, and earnings yield — because a low P/E
  alone walks straight into value traps. Banks legitimately report no EBITDA
  or FCF (the reason value studies exclude financials from EV/EBITDA sorts)
  and are scored on what they have, with a note saying so.
- *Quality* rank-averages ROE, ROA (the closest ROIC proxy the data source
  provides, labeled as such), operating margin, and low leverage.
- The **value-trap flag** is the interaction: cheap third on value *and*
  bottom third on quality — cheap-because-dying. For financials the flag
  carries an extra caveat, since cross-sector quality ranks penalize the
  banking business model mechanically.

The standing caveat, printed on every factor surface: these ranks are
cross-sectional within this small watchlist, not the market-wide cross-section
the academic evidence is built on, and composites are withheld entirely below
five companies. Descriptive screens, not signals.

**Portfolio.** A Portfolio tab reading `config/holdings.yml`: weighted vs
equal-weighted factor tilt (the gap is what position sizing is doing),
concentration via HHI and effective position count, sector weight, and a
per-position factor table. Holdings are scored against the **full tracked
cross-section, never against each other** — five names ranked among themselves
would always put someone in the "cheapest quintile" by construction.

Position data is split by sensitivity: `config/holdings.yml` holds tickers and
percent weights and is committed; `config/holdings.local.yml` holds dollar
values, cost basis and unrealized P/L and is gitignored. The payload builder
loads holdings with `include_local=False`, so dollar figures cannot reach
`docs/` even by accident — there is a test asserting it.

**Factor drift.** `history/factors.jsonl` accumulates a per-symbol factor
snapshot each run, and the Portfolio tab draws sparklines showing how each
holding's standing has moved. Two provenances, labeled rather than blended:

- *Momentum is reconstructed exactly* — the 12-1 window at any past date uses
  only bars up to that date, so ~4 years backfills from the price history with
  no look-ahead. There is a test that recomputes every backfilled value from
  truncated bars and asserts it matches.
- *Value and quality accumulate forward only* — the data source publishes no
  history for trailing P/E, ROE or margins. Quarterly statements exist for
  about five quarters, but they are the figures **as restated today**, so
  rebuilding a past valuation from them would leak information that did not
  exist at the time. The series start when the file starts.

The whole file carries one caveat: the cross-section is *today's* watchlist, so
a backfilled rank answers "how did the things I now track compare back then",
not "what would I have seen at the time".

**Timing.** A Timing tab answering "how much might this move, and when" —
never which way, which is not forecastable at this horizon.

- *Volatility regime* — EWMA daily volatility (λ=0.94, the RiskMetrics daily
  constant, deliberately not the λ=0.97 the upstream forecaster uses for
  monthly bars) placed against each name's **own** trailing year, since 2%
  daily vol is turbulent for a utility and ordinary for a biotech.
- *Validation travels with the label.* Walk-forward through history, label
  each day from prior data only, measure the move that followed. Across this
  universe it confirms on 19 series, is weak on 13, and shows **no separation
  on 12** — it works on index/sector ETFs and large caps, and fails on
  already-noisy single names. Unvalidated rows are dimmed in the table: the
  label describes the present rather than forecasting anything.
- *Expected range* — a band whose multiplier is calibrated per series by
  walk-forward coverage. Measured here it lands ~1.08–1.30, scattered around
  the 1.2816 Gaussian value; the case for calibrating is per-series accuracy,
  not a uniformly wider band.
- *Catalyst calendar* — scheduled earnings dates with each name's own measured
  amplification (median announcement-day move ÷ median ordinary session). The
  reaction session accounts for reporting time: a company announcing after the
  close moves the **next** session, and treating the announcement date as the
  reaction understates after-close reporters badly — it read NVDA at 0.7x when
  the true figure is 2.9x.

Deliberately absent: options-implied moves. The chains for this universe are
too thin to trust — a probe returned nine contracts for one holding with an
implied volatility of 0.00001.

**Momentum crash risk.** Momentum fails violently rather than gradually.
Daniel & Moskowitz (2016) document that the crashes occur in an identifiable
state — after sustained declines, with elevated volatility, worst on a
rebound. The Portfolio tab classifies the benchmark's state on those axes and
combines it with the book's own momentum tilt, since exposure is the product
of the two: a neutral book in a panic has little to reverse.

The provenance is kept explicit, because it cannot be otherwise. The crash
condition is **imported from published research, not validated on this data**
— the defining events are decades apart and a five-year watchlist contains
none of them. What the local history does say is reported with its sample size
attached, and buckets below the floor are dimmed in the UI:

| market state | obs | 1-mo winners−losers |
|---|---|---|
| near highs | 30 | +0.27% |
| moderate drawdown | 10 | +3.14% |
| deep drawdown | 6 | −0.43% |

The sign on the last row is what the literature predicts. Six observations is
not evidence, and the payload says `insufficient sample` rather than letting a
suggestive number read as confirmation. A long-only book also carries a muted
version of the published effect, which measures a long-short strategy where
the crash lands mostly on the short leg.

**Claude, two ways.**

*Daily notes* — Claude reads the rebuilt payloads each trading day and writes
`analysis/YYYY-MM-DD.md`, leading with volume and valuation, then factors, then
what changed. The Analysis tab in the dashboard renders them.

*Ask, on demand* — an Ask panel on every chart page, with preset questions
(factor read, volume check, valuation vs peers) or anything you type. It is
answered by a **local companion server**, not by the published site:

```bash
export CLAUDE_CODE_OAUTH_TOKEN=$(claude setup-token)   # once
python scripts/desk_server.py                          # http://127.0.0.1:8793
```

That server hosts the same dashboard *and* an `/api/ask` endpoint that shells
out to the Claude Code CLI, so questions bill against your existing
subscription rather than a separate API key — and it is the same secret the
daily notes need, so one token turns on both. It binds to `127.0.0.1` only.

The reason this is local rather than a button on the public site: GitHub Pages
is static and world-readable, so it has nowhere to keep a credential. Anything
the page could call, anyone could call, and they would be spending your tokens.
On the published site the Ask panel explains this instead of failing blank.

The model only ever sees the committed payloads — the same numbers on screen —
and is instructed to refuse to invent one, to name the peer group behind every
multiple, and to give analysis rather than advice.

## How it connects to Census-Forecaster

Census-Forecaster is consumed as a **pinned git dependency**, not vendored —
the same convention `Cost-of-Living-Tracker` uses against the same upstream.
Both requirement lines point at one commit; letting them drift apart produces
an import error that looks like a code bug.

Two things come across:

1. **The forecaster.** `census_forecaster.markets.trend` supplies a damped
   drift point at the repo-standard φ=0.92/month and a 90% band whose
   multiplier is *walk-forward calibrated per ticker*. Equity returns are
   fat-tailed, so that empirical multiplier lands well above the 1.645 a normal
   table would give — which is the entire reason to reuse this rather than
   reimplement it. Cadence is the seam: this repo is daily, that forecaster is
   monthly, so daily bars are folded to month-end closes before crossing over.

2. **The Hawaii lead signals.** Census-Forecaster runs a pre-registered Granger
   lead-lag screen of market series against Hawaii macro targets, BH-FDR
   controlled, with a 2020-exclusion robustness re-run. Symbols that survive get
   a badge naming which Hawaii series they lead and by how many months. Three
   caveats travel with every signal and the page prints them: Granger causality
   is predictive precedence and not causation; signals that fail the
   2020-exclusion re-run are COVID-dependent artifacts and are labeled rather
   than hidden; and the arrow runs prices → Hawaii economy, never the reverse.

Nothing from the macro side touches a price forecast. The upstream repo tested
that direction and found a clean efficient-markets null, so importing macro
state into a return forecast would be re-running a failed experiment.

To bump the pin: pick a commit that exists on Census-Forecaster's `origin/main`,
replace the SHA on **both** lines in `requirements.txt`, reinstall, run tests.

## Setup

```bash
git clone https://github.com/dtomkatsu/Market-Desk.git
cd Market-Desk
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python scripts/refresh.py
```

Then serve `docs/` and open it:

```bash
python3 -m http.server 8793 --directory docs
```

## Setting your watchlist

`config/watchlist.yml` is the only place tickers are declared; everything else
follows from it. Edit it directly, or import a Yahoo Finance list.

Export from Yahoo (My Portfolio → open the list → ⋯ → Export), then:

```bash
python scripts/import_yahoo_watchlist.py ~/Downloads/quotes.csv
```

Symbols merge into the `watchlist` tier, `BRK.B`-style share classes are
normalized to Yahoo's `BRK-B` form, duplicates are dropped, and the file's
comments survive. `--tier core`, `--replace` and `--dry-run` do what they sound
like. You can also skip the CSV:

```bash
python scripts/import_yahoo_watchlist.py --symbols NVDA AMD TSM
```

Note that peer groups get better as the watchlist grows: a sector needs at least
`min_sector_peers` tracked members (default 4) before P/E is ranked within it
rather than against the whole universe.

## Layout

```
config/watchlist.yml      the tracked universe — edit this
src/market_desk/
  config.py               parse and validate the watchlist
  fetch.py                daily OHLCV + fundamentals (yfinance)
  indicators.py           SMA/EMA/RSI/MACD/ATR/Bollinger — pure functions
  volume.py               RVOL, OBV, VWAP, A/D, divergence — pure functions
  valuation.py            peer groups and percentile ranks
  factors.py              momentum / value / quality cross-sections
  portfolio.py            portfolio exposure over held positions
  benchmark.py            S&P 500 cross-section + sector-relative ranking
scripts/momentum_study.py  does momentum work here? measure, don't assume
  history.py              factor drift: backfilled momentum + live snapshots
  volatility.py           EWMA regime, expected range, walk-forward validation
  catalysts.py            earnings calendar + measured reaction size
  crashrisk.py            market state + momentum-tilt exposure
  forecast.py             bridge to Census-Forecaster's damped-drift forecaster
  macro.py                the Hawaii lead-signal overlay
  build.py                assemble docs/data/*.json
scripts/refresh.py        the entrypoint the daily workflow runs
scripts/desk_server.py    local server: dashboard + /api/ask (Claude)
scripts/import_yahoo_watchlist.py
docs/                     the GitHub Pages front end
analysis/                 Claude's daily notes
```

Everything in `indicators.py` and `volume.py` is a pure function over lists of
floats — no pandas — and returns a series index-aligned with the bars, padded
with `None` where a window has not filled. That padding convention is load
bearing: the front end zips series against candles with no offset arithmetic,
and an un-warmed indicator is visibly absent rather than quietly wrong.

## Data pipeline

```
config/watchlist.yml → fetch → indicators/volume/valuation → forecast → docs/data/*.json → docs/
```

`docs/data/` is generated, not committed — the workflow builds it and hands it
straight to the Pages artifact. Three artifacts come out: `meta.json` (fetch
date, failures, the upstream pin), `index.json` (one summary row per symbol —
what the table and every sort reads, kept small so it loads instantly), and
`symbols/<SYM>.json` (full series, fetched only when a symbol is opened).

The page reads only committed JSON at view time. No API calls from the browser,
so the dashboard keeps working when a data source breaks and every number on
screen is reproducible from one refresh run.

## Automation

`.github/workflows/refresh.yml` runs at 22:00 UTC (12:00 HST) Mon–Fri: tests,
refresh, Claude's note, commit, deploy. Market holidays still fire the cron; the
workflow notices the latest session already has a note and skips the analysis
rather than trying to encode the NYSE calendar.

It needs one secret, `CLAUDE_CODE_OAUTH_TOKEN`, from `claude setup-token` —
the same token the local Ask server uses. These
last a year and cannot be renewed non-interactively, so a `token-expiry` job
opens a reminder issue 30 days out. Update `TOKEN_ISSUED` in the workflow
whenever you regenerate it.

**Does momentum actually work here?** `scripts/momentum_study.py` measures it
on the S&P 500 cross-section rather than assuming. Result on 5 years, 46
monthly observations, ~164 names per leg:

| | mean spread | t | positive months |
|---|---|---|---|
| terciles | +0.82%/mo (+10.3%/yr) | +1.55 | 31/46 (67%) |
| deciles | +1.76%/mo (+23.4%/yr) | +1.58 | 28/46 (61%) |

**Not significant at t=2**, so this remains suggestive rather than
established — but it is now *informative*, which the watchlist version was
not. Broadening from ~8 to ~164 names per leg cut the spread's standard
deviation from 9.94% to 3.58% while leaving the mean essentially unchanged
(+0.80% → +0.82%): the estimate did not move, it got 2.8× more precise. At
this effect size, reaching t=2 needs ~77 monthly observations (~6.4 years);
the sample has 46 and grows by one a month.

See [`PREDICTIONS.md`](PREDICTIONS.md) for a full account of what this
dashboard can and cannot predict, with the evidence behind each claim.

## Known limitations

- **Yahoo is an unofficial source.** yfinance scrapes undocumented endpoints.
  Fields disappear, values are sometimes wrong (yfinance reported NVDA's
  dividend rate at $1.00/yr against an actual $0.04 in August 2026), and the
  package breaks when Yahoo changes its API. Missing data is recorded as
  missing, never defaulted to zero.
- **Small comparison universe.** Percentile ranks are computed against tracked
  symbols, not the full market. A "cheapest quartile" reading means cheapest of
  a couple of dozen names.
- **Daily bars only.** No intraday data, so VWAP is the standard typical-price
  proxy rather than true tick-weighted VWAP.
- **The forecast is a damped trend, not skill.** Equity prices are close to a
  random walk. The band is the honest content and it is wide on purpose.

[klc]: https://github.com/klinecharts/KLineChart
