# Market-Desk — Claude rules

## Hard rules

- **Not trading advice, and it never becomes trading advice.** Every surface —
  dashboard, README, analysis notes — carries that line, and no output may
  recommend buying or selling, state a price target, or frame a forecast as a
  prediction of skill. This is inherited from Census-Forecaster's standing line
  on its markets subpackage; keep it.
- **Never report a raw multiple as a verdict.** A P/E is only meaningful
  against a comparison set. Every valuation figure goes out as a percentile
  inside a *named* peer group, and the name is always shown. When a sector is
  too thin the group falls back to "tracked universe" — say so, never quietly
  substitute it.
- **Missing is missing.** A null P/E, dividend yield or forecast must stay
  null through the whole pipeline. Defaulting one to 0.0 puts a loss-maker at
  the cheapest end of a sort, which is the single most damaging silent bug this
  codebase can have. `_positive()` in `fetch.py` is the gate for ratios that
  are only meaningful above zero.
- **Series stay index-aligned with bars.** Every function in `indicators.py`
  and `volume.py` returns a list the same length as its input, `None`-padded
  through the warmup. The front end depends on zipping these against candles
  with no offset arithmetic.
- **Census-Forecaster is a pinned git dependency, never vendored.** Both lines
  in `requirements.txt` must point at the *same* commit, and that commit must
  exist on the upstream `origin/main` — a local-only SHA passes here and fails
  in CI. Same convention as Cost-of-Living-Tracker.
- **The arrow runs prices → Hawaii economy.** Macro state never enters a price
  forecast. Upstream tested the reverse direction (`markets/fundamentals.py`)
  and got a clean EMH null; re-adding it is re-running a failed experiment.
- **`docs/data/` is generated, not committed.** It is in `.gitignore`. If you
  find yourself committing payloads, something is wrong.
- **Bump `?v=` on `styles.css` / `app.js` in `docs/index.html`** after editing
  either, or Pages serves the cached copy.
- **Tests must pass before committing.** `python -m pytest tests/ -q`.

## Gotchas already paid for

- **`autoSize: true` on Lightweight Charts leaves the internal width at 0** in
  the vendored v4.2.3 build. barSpacing pins to its 0.5 minimum, and both
  `fitContent()` and `setVisibleLogicalRange()` become no-ops — every series
  renders squeezed against the right edge. Size the charts explicitly and drive
  resize with a ResizeObserver.
- **Chart pane sync must be one-way.** The range-change event fires
  asynchronously, so a two-way link guarded by a boolean does not hold: the
  guard is already cleared when the echo arrives and the two charts ratchet
  each other into a handful of visible bars. The lower pane has
  `handleScroll: false, handleScale: false` and never writes back.
- **`min-height: 0` on `.layout`** lets the grid collapse below its content, so
  the footer paints on top of the cards. Leave it off.
- **`dividendYield` from yfinance is in PERCENT** (0.46 means 0.46%). Use
  `trailingAnnualDividendYield`, which is a true fraction and is computed from
  dividends actually paid.
- **ETFs report a holdings-weighted P/E.** Yahoo returns one for SPY. It is
  displayed as index context but must never enter a company peer group.

## Stack

- Python 3.12, stdlib-only transforms (no pandas outside `fetch.py`), pytest.
- Front end: vanilla JS + vendored TradingView Lightweight Charts 4.2.3.
- GitHub Actions → GitHub Pages.

## Source-of-truth docs

- `README.md` — what it does, how the Census-Forecaster link works, limitations.
- `config/watchlist.yml` — the tracked universe, with the import recipe in its
  header comment.
