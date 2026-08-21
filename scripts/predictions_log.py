#!/usr/bin/env python3
"""Log today's falsifiable claims to history/predictions.jsonl, ex ante.

    python scripts/predictions_log.py            # tracked universe + book

Run daily by the workflow after the data refresh. Every claim is written
BEFORE its window opens and carries everything needed to grade it, so the
grader never recomputes an input (a changed estimator must not be able to
silently regrade the past). Re-running the same day adds nothing — claim
ids are deterministic and the logger dedupes against what is already on
disk.

What gets claimed, and what deliberately does not:

* **5-session 80% ranges** for every tracked symbol with enough history —
  event-aware for companies (the band widens through a scheduled reaction
  session by the name's own walk-forward amplification), flat for funds.
  The walk-forward coverage evidence for shipping this construction is in
  event_range_study.py: flat bands cover ~52% of earnings weeks, aware
  bands ~77-79% against the 80% claim.
* **Event-size claims** for reactions scheduled inside the next 10
  weekdays: the name's typical |move| and amplification, graded against
  the realized reaction session. Withheld below 4 past events.
* **Regime-persistence claims** only for names whose walk-forward verdict
  is CONFIRMED — an unvalidated label is a description and does not get
  to make claims. Quiet names claim small, turbulent names claim large;
  both are magnitude claims.
* **One book claim**: the 5-session 80% band on the whole account, EWMA
  covariance with event inflation on the diagonal. Its multiplier is the
  Gaussian default and the claim says so — the registry will measure the
  book's true multiplier the slow, honest way.
* **No claim names a direction.** The registry is the enforcement point:
  a claim kind without a grader that scores magnitude does not exist.

Scheduled dates are estimates until confirmed, and future sessions are
approximated by weekdays (exchange holidays are not modelled). Both
approximations bias grades AGAINST the registry — a moved date grades an
ordinary session as if it were a reaction — so the scoreboard understates
rather than flatters.
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
warnings.filterwarnings("ignore")

from market_desk.catalysts import announcement_date, measure_reaction  # noqa: E402
from market_desk.config import load_universe             # noqa: E402
from market_desk.fetch import fetch_history              # noqa: E402
from market_desk.predictions import (                    # noqa: E402
    GAUSSIAN_80, book_claim, dedupe_new, event_aware_half_width,
    event_claim, ewma_cov, portfolio_band_pct, range_claim, regime_claim,
    walkforward_amplification,
)
from market_desk.volatility import (                     # noqa: E402
    calibrate_multiplier, classify_regime, ewma_vol, validate_regimes,
)

from shock_study import load_earnings                    # noqa: E402
from swing_forecast import abs_moves, earnings_sessions  # noqa: E402
from upcoming_swings import holdings_weights             # noqa: E402

REGISTRY = REPO_ROOT / "history" / "predictions.jsonl"
HORIZON = 5
EVENT_LOOKAHEAD_WEEKDAYS = 10


def future_weekdays(after: str, n: int) -> list[str]:
    d, out = datetime.fromisoformat(after).date(), []
    while len(out) < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            out.append(d.isoformat())
    return out


def upcoming_reactions(stamps: list[str], today: str, window: list[str]):
    """(announce, approx reaction session) pairs inside the window."""
    out = []
    for stamp in sorted(stamps):
        day, after_close = announcement_date(stamp)
        if day < today:
            continue
        pool = [w for w in window if (w > day if after_close else w >= day)]
        if pool:
            out.append((day, pool[0]))
    return out


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--registry", default=str(REGISTRY))
    args = ap.parse_args(argv)
    registry = Path(args.registry)

    symbols = sorted(set(load_universe().symbols) | set(holdings_weights()))
    print(f"fetching {len(symbols)} symbols (5y)")
    bars, failures = fetch_history(symbols, "5y")
    print(f"  {len(bars)} ok, {len(failures)} failed")
    if len(bars) < 0.8 * len(symbols):
        raise SystemExit("truncated fetch; refusing to log claims from it")
    earnings = load_earnings(sorted(bars), False)

    claims: list[dict] = []
    rets: dict[str, dict[str, float]] = {}
    amps_today: dict[str, float] = {}
    as_of_global = max(bs[-1].date for bs in bars.values())
    window = future_weekdays(as_of_global, EVENT_LOOKAHEAD_WEEKDAYS)

    for symbol, series in sorted(bars.items()):
        closes = [b.close for b in series]
        dates = [b.date for b in series]
        if len(closes) < 320 or series[-1].date != as_of_global:
            continue
        rets[symbol] = {dates[i]: closes[i] / closes[i - 1] - 1.0
                        for i in range(1, len(closes)) if closes[i - 1] > 0}
        vol = ewma_vol(closes)
        if not vol:
            continue
        m = calibrate_multiplier(closes, HORIZON)
        stamps = earnings.get(symbol) or []
        moves = abs_moves(series)
        reactions = earnings_sessions(dates, stamps)

        # events inside the range window (first HORIZON weekdays)
        amps: list[float] = []
        if stamps:
            amp = walkforward_amplification(moves, reactions,
                                            before="9999-12-31")
            nxt = upcoming_reactions(stamps, as_of_global, window)
            in_range = [r for _, r in nxt if r in window[:HORIZON]]
            if amp is not None:
                if in_range:
                    amps = [amp] * len(in_range)
                    amps_today[symbol] = amp

            reaction = measure_reaction(series, stamps)
            for announce, session in nxt:
                if reaction and reaction.n_events >= 4:
                    claims.append(event_claim(
                        symbol, as_of_global, announce, session,
                        typical_move=reaction.median_move,
                        amplification=reaction.amplification,
                        baseline_move=reaction.baseline_move,
                        n_events=reaction.n_events))

        half = event_aware_half_width(vol, HORIZON, m or GAUSSIAN_80, amps)
        claims.append(range_claim(
            symbol, as_of_global, HORIZON, closes[-1], half,
            coverage=0.80, calibrated=m is not None, amps=amps,
            method="event_aware" if amps else "flat"))

        val = validate_regimes(closes, horizon=HORIZON)
        reg = classify_regime(closes)
        if val.verdict == "confirmed" and reg.label in ("quiet", "turbulent"):
            recent = [abs(closes[i + HORIZON] / closes[i] - 1.0)
                      for i in range(len(closes) - HORIZON - 252,
                                     len(closes) - HORIZON)
                      if closes[i] > 0]
            if recent:
                import statistics as st
                claims.append(regime_claim(
                    symbol, as_of_global, HORIZON, reg.label,
                    separation=val.separation or 0.0,
                    baseline_abs=st.median(recent)))

    # ---- the book -------------------------------------------------------
    weights = {s: w for s, w in holdings_weights().items() if s in rets}
    if len(weights) >= 2:
        cov = ewma_cov({s: rets[s] for s in weights})
        band = portfolio_band_pct(weights, cov, HORIZON,
                                  {s: a for s, a in amps_today.items()
                                   if s in weights})
        claims.append(book_claim(as_of_global, HORIZON, weights, band,
                                 {s: a for s, a in amps_today.items()
                                  if s in weights}))

    existing = set()
    if registry.exists():
        existing = {json.loads(line)["id"]
                    for line in registry.read_text().splitlines() if line}
    fresh = dedupe_new(existing, claims)
    registry.parent.mkdir(parents=True, exist_ok=True)
    with registry.open("a") as fh:
        for c in fresh:
            fh.write(json.dumps(c) + "\n")

    kinds = {}
    for c in fresh:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    print(f"as of {as_of_global}: {len(fresh)} new claims "
          f"({', '.join(f'{k}={v}' for k, v in sorted(kinds.items())) or 'none'}), "
          f"{len(claims) - len(fresh)} already logged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
