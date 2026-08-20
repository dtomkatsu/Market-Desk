"""Factor history: how each name's standing has drifted over time.

Two very different provenances live in this file, and conflating them
would be dishonest, so they are labeled:

* **Momentum is reconstructed exactly.** The 12-1 formation return at any
  past date depends only on prices at or before that date, so it can be
  recomputed from the bundled price history with no look-ahead. Backfilled
  rows are marked ``source="backfill"``.

* **Value and quality accumulate forward only.** Yahoo's ``info`` fields
  (trailing P/E, ROE, margins) are point-in-time snapshots with no history.
  Quarterly statements *are* available for roughly five quarters, but they
  are the figures **as restated today**, not as they were known then —
  rebuilding a 2025 valuation from them would quietly leak information that
  did not exist at the time. Rather than manufacture a plausible-looking
  series, value and quality start the day this file starts and grow from
  there. Rows are marked ``source="live"``.

One caveat applies to the whole file and is repeated in the payload: the
cross-section is **today's** watchlist. A backfilled rank from 2025 ranks
names selected in 2026, so it carries selection bias — it answers "how did
the things I now track compare back then", not "what would I have seen".
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

from .factors import SESSIONS_1M, SESSIONS_12M, _pct_rank, momentum_metrics
from .fetch import Bar, Fundamentals

REPO_ROOT = Path(__file__).resolve().parents[2]
HISTORY_PATH = REPO_ROOT / "history" / "factors.jsonl"

# Backfill cadence. Momentum moves slowly and a daily series would be ~250
# rows per symbol per year for no extra insight; one sample every ~21
# sessions is a monthly read.
BACKFILL_STEP = 21


@dataclass(frozen=True)
class HistoryRow:
    date: str
    symbol: str
    source: str                      # "backfill" | "live"
    mom_12_1: Optional[float] = None
    mom_rank: Optional[float] = None
    value_score: Optional[float] = None
    quality_score: Optional[float] = None
    value_trap: bool = False
    reversal_tension: bool = False

    def to_json(self) -> dict:
        out = {"date": self.date, "symbol": self.symbol, "source": self.source}
        for key in ("mom_12_1", "mom_rank", "value_score", "quality_score"):
            val = getattr(self, key)
            if val is not None:
                out[key] = round(val, 5)
        if self.value_trap:
            out["value_trap"] = True
        if self.reversal_tension:
            out["reversal_tension"] = True
        return out


def load_history(path: Optional[Path] = None) -> list[HistoryRow]:
    path = Path(path) if path else HISTORY_PATH
    if not path.exists():
        return []
    rows: list[HistoryRow] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue                 # a torn write should not kill the run
        rows.append(HistoryRow(
            date=d.get("date", ""), symbol=d.get("symbol", ""),
            source=d.get("source", "live"),
            mom_12_1=d.get("mom_12_1"), mom_rank=d.get("mom_rank"),
            value_score=d.get("value_score"), quality_score=d.get("quality_score"),
            value_trap=bool(d.get("value_trap")),
            reversal_tension=bool(d.get("reversal_tension")),
        ))
    return rows


def write_history(rows: Iterable[HistoryRow], path: Optional[Path] = None) -> int:
    """Rewrite the file, de-duplicated on (date, symbol) and sorted.

    A live row always wins over a backfilled one for the same day: it
    carries value and quality, which the backfill cannot.
    """
    path = Path(path) if path else HISTORY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    best: dict[tuple[str, str], HistoryRow] = {}
    for row in rows:
        if not row.date or not row.symbol:
            continue
        key = (row.date, row.symbol)
        existing = best.get(key)
        if existing is None or (existing.source == "backfill" and row.source == "live"):
            best[key] = row

    ordered = sorted(best.values(), key=lambda r: (r.date, r.symbol))
    path.write_text("".join(json.dumps(r.to_json(), separators=(",", ":")) + "\n"
                            for r in ordered))
    return len(ordered)


def backfill_momentum(bars_by_symbol: dict[str, Sequence[Bar]],
                      fundamentals: dict[str, Fundamentals],
                      step: int = BACKFILL_STEP) -> list[HistoryRow]:
    """Reconstruct momentum and its cross-sectional rank at past dates.

    At each sampled index the formation window is computed from the bars up
    to that index only — ``closes[:i + 1]`` — so nothing after the sample
    date can influence it.
    """
    if not bars_by_symbol:
        return []

    # Sample on the calendar of the longest series so every symbol is
    # ranked against the others on the same dates.
    longest = max(bars_by_symbol.values(), key=len)
    dates = [b.date for b in longest]

    # Only companies enter the cross-section, matching factors.py.
    companies = {s for s, f in fundamentals.items() if not f.is_fund}

    by_date: dict[str, dict[str, float]] = {}
    closes_by_symbol = {s: [b.close for b in bars] for s, bars in bars_by_symbol.items()}
    index_by_symbol = {s: {b.date: i for i, b in enumerate(bars)}
                       for s, bars in bars_by_symbol.items()}

    start = SESSIONS_12M + SESSIONS_1M      # first index with a full window
    for i in range(start, len(dates), step):
        date = dates[i]
        snapshot: dict[str, float] = {}
        for symbol, closes in closes_by_symbol.items():
            idx = index_by_symbol[symbol].get(date)
            if idx is None or idx < start:
                continue                     # this name has no bar that day
            m = momentum_metrics(closes[:idx + 1])
            if m.mom_12_1 is not None:
                snapshot[symbol] = m.mom_12_1
        if snapshot:
            by_date[date] = snapshot

    rows: list[HistoryRow] = []
    for date, snapshot in by_date.items():
        population = [v for s, v in snapshot.items() if s in companies]
        for symbol, value in snapshot.items():
            rank = _pct_rank(value, population) if symbol in companies else None
            rows.append(HistoryRow(
                date=date, symbol=symbol, source="backfill",
                mom_12_1=value, mom_rank=rank,
            ))
    return rows


def snapshot_today(date: str, factor_views: dict) -> list[HistoryRow]:
    """Today's live row per symbol — the only source of value/quality."""
    rows: list[HistoryRow] = []
    for symbol, fv in factor_views.items():
        rows.append(HistoryRow(
            date=date, symbol=symbol, source="live",
            mom_12_1=fv.momentum.mom_12_1,
            mom_rank=fv.momentum_rank,
            value_score=fv.value_score,
            quality_score=fv.quality_score,
            value_trap=fv.value_trap,
            reversal_tension=fv.reversal_tension,
        ))
    return rows


def series_by_symbol(rows: Sequence[HistoryRow]) -> dict[str, list[dict]]:
    """Compact per-symbol series for the dashboard payload."""
    out: dict[str, list[dict]] = {}
    for row in sorted(rows, key=lambda r: r.date):
        point = {"d": row.date, "s": row.source}
        if row.mom_rank is not None:
            point["m"] = round(row.mom_rank, 3)
        if row.value_score is not None:
            point["v"] = round(row.value_score, 3)
        if row.quality_score is not None:
            point["q"] = round(row.quality_score, 3)
        out.setdefault(row.symbol, []).append(point)
    return out


def drift(series: Sequence[dict], key: str, lookback: int = 6) -> Optional[dict]:
    """Change in one factor over the last ``lookback`` samples that have it."""
    points = [p for p in series if key in p]
    if len(points) < 2:
        return None
    recent = points[-lookback:]
    first, last = recent[0], recent[-1]
    return {
        "from": first[key], "to": last[key],
        "change": round(last[key] - first[key], 3),
        "from_date": first["d"], "to_date": last["d"],
        "n": len(recent),
    }
