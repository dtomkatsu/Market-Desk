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
- **Factor scores are watchlist-relative, and every surface says so.** The
  academic momentum/value/quality evidence ranks the whole market; this repo
  ranks ~14 companies. `MIN_CROSS_SECTION` withholds composites below 5.
  The 12-1 momentum construction must keep skipping the most recent month
  (`ret_1m` is the reversal window, reported separately, never folded in),
  value must stay multi-ratio (EV/EBITDA + FCF yield + earnings yield — a
  P/E-only sort is the documented value-trap generator), and the trap flag is
  the value×quality interaction. ROA is a ROIC *proxy* and is labeled as one.
- **The arrow runs prices → Hawaii economy.** Macro state never enters a price
  forecast. Upstream tested the reverse direction (`markets/fundamentals.py`)
  and got a clean EMH null; re-adding it is re-running a failed experiment.
- **`docs/data/` is generated, not committed.** It is in `.gitignore`. If you
  find yourself committing payloads, something is wrong.
- **Do NOT hand-bump `?v=` on `styles.css` / `app.js`.** The global
  `core.hooksPath` pre-commit hook rewrites both query strings to a fresh
  timestamp on every commit that touches them. Editing them by hand just
  fights the hook.
- **No credential ever reaches `docs/`.** The published site is static and
  world-readable. Claude answers come from `scripts/desk_server.py`, which
  binds `127.0.0.1` only and shells out to the `claude` CLI. Never "simplify"
  this by putting an API key in the front end or by binding `0.0.0.0` — the
  endpoint runs a subprocess on the user's behalf.
- **Model and note text is untrusted input to the page.** `markdown()` in
  `app.js` escapes the whole string *before* introducing any markup, and only
  then applies its small subset. Keep that order; never switch to a parser
  that emits raw HTML.
- **Tests must pass before committing.** `python -m pytest tests/ -q`.

## Gotchas already paid for

- **KLineChart is pinned at v9 (9.8.12) on purpose.** v10 removed
  `applyNewData` in favor of a `setSymbol`/`setPeriod`/`setDataLoader` model —
  a full data-flow reshape. Do not "upgrade" the vendored file without
  rewriting the chart module against the v10 API.
- **Future overlay points must use `dataIndex`, never `timestamp`.** A
  timestamp beyond the last bar clamps to the last bar and misplaces the
  point (verified empirically); `dataIndex` past the end extrapolates
  linearly. The forecast fan depends on this, which is also why it must be
  re-created after every `applyNewData` (timeframe switch): dataIndexes shift.
- **Anything sized while the Browser pane (or tab) is hidden measures 0.**
  The chart shell reads `clientWidth` in `applyRange`; a toggle fired while
  hidden computes garbage barSpacing. The ResizeObserver fixes the canvas but
  not the range state — re-run `applyRange()` after the pane is visible when
  debugging "everything is squeezed right".
- **Hiding the drawing rail needs its grid column collapsed too**
  (`grid-template-columns: minmax(0,1fr)` in the mobile block) — with
  `display: none` alone the chart auto-places into the 38px rail track.
- **`min-height: 0` on `.layout`** lets the grid collapse below its content, so
  the footer paints on top of the cards. Leave it off.
- **`anthropics/claude-code-action@v1` rejects `push` events** with
  "Unsupported event type: push", failing in ~300ms. The refresh workflow
  therefore skips the note on pushes and writes one only on `schedule` and
  `workflow_dispatch`. Do not "fix" this by adding push back.
- **`SimpleHTTPRequestHandler.log_message`'s format args are not always
  strings.** `send_error` passes an int status code as one of them, so
  filtering log noise by indexing into `args` and testing `in` crashes on
  every non-string caller. Check `self.path` instead — always a string,
  always present during a request.
- **`~/.zshrc` is not sourced by tool-invoked (non-interactive) shells** —
  only by interactive ones. A Bash tool call that needs
  `CLAUDE_CODE_OAUTH_TOKEN` from the profile must `source ~/.zshrc` first;
  a fresh Terminal.app window picks it up automatically.
- **`dividendYield` from yfinance is in PERCENT** (0.46 means 0.46%). Use
  `trailingAnnualDividendYield`, which is a true fraction and is computed from
  dividends actually paid.
- **ETFs report a holdings-weighted P/E.** Yahoo returns one for SPY. It is
  displayed as index context but must never enter a company peer group.

## Stack

- Python 3.12, stdlib-only transforms (no pandas outside `fetch.py`), pytest.
- Local companion server (`scripts/desk_server.py`) is **stdlib-only on
  purpose** — it runs under system `python3` with no venv, so the launch
  config and a fresh clone both work. Do not add imports to it.
- Front end: vanilla JS + vendored KLineChart 9.8.12 (Apache-2.0) — the
  open-source TradingView-style chart: candle + indicator panes, drawing
  overlays (trend lines, channels, fibs), magnet snapping.
- GitHub Actions → GitHub Pages.

## Source-of-truth docs

- `README.md` — what it does, how the Census-Forecaster link works, limitations.
- `config/watchlist.yml` — the tracked universe, with the import recipe in its
  header comment.
