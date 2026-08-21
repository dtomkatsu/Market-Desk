#!/usr/bin/env python3
"""Cache earnings surprise history for one or more pinned universes.

    python scripts/fetch_surprises.py sp500 sp600

One row per past announcement: timestamp (hour kept — the after-close rule
needs it), EPS estimate, reported EPS, and Yahoo's Surprise(%). Cached to
.cache/earnings_surprise.json and topped up per symbol, same pattern as the
earnings-date cache; symbols already present are not re-fetched.
"""
from __future__ import annotations

import json
import sys
import time
import warnings
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
warnings.filterwarnings("ignore")

from market_desk.benchmark import load_constituents

CACHE = REPO_ROOT / ".cache" / "earnings_surprise.json"


def main(argv=None) -> int:
    universes = (argv or sys.argv[1:]) or ["sp500", "sp600"]
    symbols: list[str] = []
    for u in universes:
        rows = load_constituents(REPO_ROOT / "config" / "benchmark" / f"{u}.csv")
        symbols += [r["symbol"] for r in rows]
    symbols = sorted(set(symbols))

    cached = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    todo = [s for s in symbols if s not in cached]
    print(f"{len(symbols)} symbols, {len(cached)} cached, {len(todo)} to fetch")

    import yfinance as yf
    for k, s in enumerate(todo):
        rows = []
        try:
            f = yf.Ticker(s).earnings_dates
            if f is not None and not f.empty:
                past = f[f["Reported EPS"].notna()]
                for ts, r in past.iterrows():
                    spct = r.get("Surprise(%)")
                    rows.append({
                        "ts": ts.isoformat(),
                        "est": None if r.get("EPS Estimate") != r.get("EPS Estimate") else float(r["EPS Estimate"]),
                        "eps": float(r["Reported EPS"]),
                        "spct": None if spct != spct else float(spct),
                    })
        except Exception as exc:                     # noqa: BLE001
            print(f"  ! {s}: {type(exc).__name__}")
        cached[s] = rows
        if (k + 1) % 50 == 0:
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(cached))
            print(f"  {k + 1}/{len(todo)} ({s})")
        time.sleep(0.2)

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cached))
    with_s = sum(1 for v in cached.values() if any(r["spct"] is not None for r in v))
    print(f"done: {len(cached)} symbols cached, {with_s} with at least one surprise")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
