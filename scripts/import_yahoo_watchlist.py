#!/usr/bin/env python3
"""Merge a Yahoo Finance watchlist into config/watchlist.yml.

Export from Yahoo: My Portfolio → open the list → the ⋯ menu → Export.
That gives a CSV whose first column is the symbol; the rest is a price
snapshot from the moment you exported, which is discarded here.

    python scripts/import_yahoo_watchlist.py ~/Downloads/quotes.csv
    python scripts/import_yahoo_watchlist.py --symbols NVDA AMD TSM
    python scripts/import_yahoo_watchlist.py quotes.csv --tier core --replace

The config is rewritten in place with its comments intact: only the
symbol list of the target tier is touched, because the file's comments
are the documentation and a YAML round-trip would strip them.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG = REPO_ROOT / "config" / "watchlist.yml"

# Yahoo exports vary by list type; these are the headers seen in the wild.
SYMBOL_HEADERS = {"symbol", "ticker", "symbols"}
VALID = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def normalize(symbol: str) -> str:
    return symbol.strip().upper().replace(".", "-")


def read_csv_symbols(path: Path) -> list[str]:
    """Pull symbols out of a Yahoo export, tolerating layout differences."""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        return []

    header = [c.strip().lower() for c in rows[0]]
    col = next((i for i, name in enumerate(header) if name in SYMBOL_HEADERS), None)

    if col is None:
        # No recognizable header — assume the first column is symbols and
        # keep row 0, which is data rather than a header in that case.
        col, body = 0, rows
    else:
        body = rows[1:]

    out: list[str] = []
    for row in body:
        if len(row) <= col:
            continue
        sym = normalize(row[col])
        if VALID.match(sym) and sym not in out:
            out.append(sym)
    return out


def replace_tier_symbols(text: str, tier: str, symbols: list[str]) -> str:
    """Rewrite one tier's `symbols:` block, leaving every comment in place."""
    lines = text.splitlines()

    tier_re = re.compile(rf"^(\s+){re.escape(tier)}:\s*$")
    tier_line = next((i for i, l in enumerate(lines) if tier_re.match(l)), None)
    if tier_line is None:
        raise SystemExit(
            f"tier '{tier}' not found in {CONFIG}. "
            f"Add it there first, or pass --tier with an existing name."
        )
    tier_indent = len(tier_re.match(lines[tier_line]).group(1))

    # Find `symbols:` inside this tier, stopping at the next key at the
    # tier's own indent (that would be a sibling tier).
    sym_line = None
    for i in range(tier_line + 1, len(lines)):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(lines[i]) - len(lines[i].lstrip())
        if indent <= tier_indent:
            break
        if stripped.startswith("symbols:"):
            sym_line = i
            break
    if sym_line is None:
        raise SystemExit(f"tier '{tier}' has no `symbols:` key")

    sym_indent = len(lines[sym_line]) - len(lines[sym_line].lstrip())

    end = sym_line + 1
    while end < len(lines):
        stripped = lines[end].strip()
        indent = len(lines[end]) - len(lines[end].lstrip())
        if stripped.startswith("-") and indent > sym_indent:
            end += 1
            continue
        if not stripped:                      # a blank line inside the list
            end += 1
            continue
        break

    block = [lines[sym_line]] + [f"{' ' * (sym_indent + 2)}- {s}" for s in symbols]
    return "\n".join(lines[:sym_line] + block + lines[end:]) + "\n"


def existing_symbols(text: str, tier: str) -> list[str]:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from market_desk.config import load_universe
    universe = load_universe(CONFIG)
    for t in universe.tiers:
        if t.key == tier:
            return list(t.symbols)
    return []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="?", type=Path, help="Yahoo Finance export CSV")
    ap.add_argument("--symbols", nargs="*", default=[],
                    help="symbols to add directly, instead of (or alongside) a CSV")
    ap.add_argument("--tier", default="watchlist", help="target tier (default: watchlist)")
    ap.add_argument("--replace", action="store_true",
                    help="replace the tier's symbols instead of merging into them")
    ap.add_argument("--dry-run", action="store_true", help="print the result, write nothing")
    args = ap.parse_args(argv)

    incoming: list[str] = []
    if args.csv:
        if not args.csv.exists():
            raise SystemExit(f"no such file: {args.csv}")
        incoming += read_csv_symbols(args.csv)
    incoming += [normalize(s) for s in args.symbols]
    incoming = [s for s in dict.fromkeys(incoming) if VALID.match(s)]

    if not incoming:
        raise SystemExit("no usable symbols found — pass a Yahoo CSV or --symbols")

    text = CONFIG.read_text()
    current = [] if args.replace else existing_symbols(text, args.tier)
    merged = list(dict.fromkeys(current + incoming))
    added = [s for s in incoming if s not in current]

    updated = replace_tier_symbols(text, args.tier, merged)

    print(f"tier '{args.tier}': {len(current)} existing + {len(added)} new = {len(merged)}")
    if added:
        print("  added: " + " ".join(added))
    else:
        print("  (nothing new)")

    if args.dry_run:
        print("\n--- dry run, not written ---")
        return 0

    CONFIG.write_text(updated)
    print(f"\nwrote {CONFIG.relative_to(REPO_ROOT)}")
    print("Next: python scripts/refresh.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
