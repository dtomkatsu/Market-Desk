#!/usr/bin/env python3
"""Refresh every dashboard payload. The entrypoint the daily workflow runs.

    python scripts/refresh.py                 # full refresh
    python scripts/refresh.py --symbols AAPL MSFT   # a subset, for debugging
    python scripts/refresh.py --no-forecast   # skip the Census-Forecaster leg
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from market_desk.build import DATA_DIR, write_all            # noqa: E402
from market_desk.config import load_universe                 # noqa: E402
from market_desk.fetch import fetch_all                      # noqa: E402
from market_desk.forecast import forecast_all                # noqa: E402
from market_desk.macro import load_overlay                   # noqa: E402
from market_desk.valuation import build_valuations           # noqa: E402


def forecaster_pin() -> str | None:
    """The Census-Forecaster commit this run resolved to.

    Recorded in meta.json so a number on the dashboard can always be
    traced back to the exact upstream code that produced it.
    """
    try:
        import census_forecaster  # type: ignore
        version = getattr(census_forecaster, "__version__", "?")
    except Exception:                                          # noqa: BLE001
        return None
    req = REPO_ROOT / "requirements.txt"
    if req.exists():
        for line in req.read_text().splitlines():
            if "Census-Forecaster.git@" in line:
                sha = line.split("Census-Forecaster.git@", 1)[1].split("#")[0].strip()
                return f"{sha} (census_forecaster {version})"
    return f"census_forecaster {version}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="*", help="override the configured universe")
    ap.add_argument("--no-forecast", action="store_true",
                    help="skip the Census-Forecaster forecast leg")
    ap.add_argument("--period", help="override history_period from the config")
    ap.add_argument("--data-dir", help="write payloads somewhere other than docs/data")
    args = ap.parse_args(argv)

    universe = load_universe()
    symbols = [s.upper() for s in args.symbols] if args.symbols else list(universe.symbols)
    period = args.period or universe.settings.history_period

    result = fetch_all(symbols, period=period)
    if not result.bars:
        print("::error::no symbol returned usable price history", file=sys.stderr)
        return 1

    valuations = build_valuations(
        result.fundamentals, min_peers=universe.settings.min_sector_peers,
    )

    if args.no_forecast:
        forecasts = {}
        print("forecasts: skipped (--no-forecast)")
    else:
        forecasts = forecast_all(result.bars)
        ok = sum(1 for f in forecasts.values() if f.horizons)
        print(f"forecasts: {ok}/{len(forecasts)} produced horizons")

    overlay = load_overlay()
    if not overlay.available:
        print(f"::warning::macro overlay unavailable: {overlay.error}")

    meta = write_all(
        universe, result, valuations, forecasts, overlay,
        forecaster_pin=forecaster_pin(),
        data_dir=Path(args.data_dir) if args.data_dir else None,
    )

    # The latest session actually present in the data — not today's date.
    # On a market holiday the fetch succeeds and returns the previous
    # session, and the workflow uses this to skip writing a second analysis
    # note about a day the market never traded.
    sessions = {row["last_date"] for row in
                json.loads((Path(args.data_dir) if args.data_dir else DATA_DIR)
                           .joinpath("index.json").read_text())["rows"]}
    latest_session = max(sessions) if sessions else result.fetch_date

    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a") as fh:
            fh.write(f"latest_session={latest_session}\n")
            fh.write(f"symbols_ok={meta['symbols_ok']}\n")

    print(f"\nwrote {meta['symbols_ok']} symbols; latest session {latest_session}")
    if meta["symbols_failed"]:
        # A failure is a warning, not an error: one dead symbol should not
        # stop the other twenty-seven from updating. The workflow surfaces
        # these in the job summary.
        for sym, reason in meta["symbols_failed"].items():
            print(f"::warning::{sym}: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
