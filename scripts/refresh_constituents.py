#!/usr/bin/env python3
"""Refresh a pinned index constituent list.

    python scripts/refresh_constituents.py                    # S&P 500
    python scripts/refresh_constituents.py --index sp600      # S&P SmallCap 600
    python scripts/refresh_constituents.py --dry-run          # show the diff only

Run this manually and review the diff in the commit. It is deliberately NOT
part of the daily workflow: the benchmark universe is the yardstick every
percentile is measured against, and a yardstick that silently changes under
the statistics is worse than a stale one. Pinning also means a Wikipedia
markup change breaks this script, never the daily refresh.

Symbols are normalized to Yahoo's convention (BRK.B -> BRK-B) so the list can
be handed straight to the fetcher.

Each index carries its OWN plausible-size bounds. They are not cosmetic: the
guard exists to catch a Wikipedia markup change, and a single shared range
wide enough to admit both a 503-row and a 603-row index would be too wide to
catch anything. The S&P 600 also turns over faster than the 500, so its pin
goes stale sooner and wants running more often.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Per-index source, pin path and plausible-size guard. If a markup change
# makes a parse return something wildly different, fail loudly rather than
# overwrite the pin with garbage.
INDEXES = {
    "sp500": {
        "label": "S&P 500",
        "out": "sp500.csv",
        "source": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "min": 480, "max": 520,      # 500 companies, ~503 share classes
    },
    "sp600": {
        "label": "S&P SmallCap 600",
        "out": "sp600.csv",
        "source": "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
        "min": 580, "max": 620,      # 600 companies, ~603 share classes
    },
}


def fetch(source: str) -> list[dict]:
    import pandas as pd

    tables = pd.read_html(source, storage_options={"User-Agent": "Mozilla/5.0"})
    frame = tables[0]
    required = {"Symbol", "Security", "GICS Sector"}
    missing = required - set(frame.columns)
    if missing:
        raise SystemExit(f"unexpected table columns; missing {missing}")

    rows = []
    for _, r in frame.iterrows():
        symbol = str(r["Symbol"]).strip().upper().replace(".", "-")
        if not symbol or not symbol.replace("-", "").isalnum():
            continue
        rows.append({
            "symbol": symbol,
            "name": str(r["Security"]).strip(),
            "gics_sector": str(r["GICS Sector"]).strip(),
        })
    rows.sort(key=lambda r: r["symbol"])
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", choices=sorted(INDEXES), default="sp500")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    spec = INDEXES[args.index]
    out = REPO_ROOT / "config" / "benchmark" / spec["out"]

    rows = fetch(spec["source"])
    if not spec["min"] <= len(rows) <= spec["max"]:
        raise SystemExit(
            f"parsed {len(rows)} {spec['label']} constituents, outside the "
            f"plausible {spec['min']}-{spec['max']} range — refusing to "
            f"overwrite the pin"
        )

    previous = set()
    if out.exists():
        with out.open() as fh:
            previous = {r["symbol"] for r in csv.DictReader(fh)}

    current = {r["symbol"] for r in rows}
    added = sorted(current - previous)
    removed = sorted(previous - current)

    print(f"{len(rows)} {spec['label']} constituents")
    from collections import Counter
    for sector, n in Counter(r["gics_sector"] for r in rows).most_common():
        print(f"  {sector:26} {n}")
    if previous:
        print(f"\nadded ({len(added)}): {' '.join(added) or '—'}")
        print(f"removed ({len(removed)}): {' '.join(removed) or '—'}")

    if args.dry_run:
        print("\n--- dry run, not written ---")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["symbol", "name", "gics_sector"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nwrote {out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
