#!/usr/bin/env python3
"""Refresh the pinned S&P 500 constituent list.

    python scripts/refresh_constituents.py            # rewrite the CSV
    python scripts/refresh_constituents.py --dry-run  # show the diff only

Run this manually and review the diff in the commit. It is deliberately NOT
part of the daily workflow: the benchmark universe is the yardstick every
percentile is measured against, and a yardstick that silently changes under
the statistics is worse than a stale one. Pinning also means a Wikipedia
markup change breaks this script, never the daily refresh.

Symbols are normalized to Yahoo's convention (BRK.B -> BRK-B) so the list can
be handed straight to the fetcher.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "config" / "benchmark" / "sp500.csv"
SOURCE = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# A plausible-size guard: the index is 500 companies (~503 share classes).
# If a markup change makes the parse return something wildly different, fail
# loudly rather than overwrite the pin with garbage.
MIN_EXPECTED = 480
MAX_EXPECTED = 520


def fetch() -> list[dict]:
    import pandas as pd

    tables = pd.read_html(SOURCE, storage_options={"User-Agent": "Mozilla/5.0"})
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
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    rows = fetch()
    if not MIN_EXPECTED <= len(rows) <= MAX_EXPECTED:
        raise SystemExit(
            f"parsed {len(rows)} constituents, outside the plausible "
            f"{MIN_EXPECTED}-{MAX_EXPECTED} range — refusing to overwrite the pin"
        )

    previous = set()
    if OUT.exists():
        with OUT.open() as fh:
            previous = {r["symbol"] for r in csv.DictReader(fh)}

    current = {r["symbol"] for r in rows}
    added = sorted(current - previous)
    removed = sorted(previous - current)

    print(f"{len(rows)} constituents")
    from collections import Counter
    for sector, n in Counter(r["gics_sector"] for r in rows).most_common():
        print(f"  {sector:26} {n}")
    if previous:
        print(f"\nadded ({len(added)}): {' '.join(added) or '—'}")
        print(f"removed ({len(removed)}): {' '.join(removed) or '—'}")

    if args.dry_run:
        print("\n--- dry run, not written ---")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["symbol", "name", "gics_sector"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nwrote {OUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
