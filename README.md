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

**Daily note.** Claude reads the rebuilt payloads each trading day and writes
`analysis/YYYY-MM-DD.md`, leading with volume and valuation and saying what
changed since the previous note.

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
  forecast.py             bridge to Census-Forecaster's damped-drift forecaster
  macro.py                the Hawaii lead-signal overlay
  build.py                assemble docs/data/*.json
scripts/refresh.py        the entrypoint the daily workflow runs
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

It needs one secret, `CLAUDE_CODE_OAUTH_TOKEN`, from `claude setup-token`. These
last a year and cannot be renewed non-interactively, so a `token-expiry` job
opens a reminder issue 30 days out. Update `TOKEN_ISSUED` in the workflow
whenever you regenerate it.

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
