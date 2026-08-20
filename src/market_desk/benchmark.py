"""The S&P 500 benchmark cross-section — the population percentiles rank against.

A percentile is only as meaningful as the population behind it. Ranking a
holding against the couple of dozen names on a personal watchlist produces a
number that moves when *other watchlist names* have an ordinary week, and a
statistical test with too little power to detect the effect it is testing
for. This module supplies a proper population: the full S&P 500, fetched
lightly (prices + fundamentals, never candles or charts) purely to be ranked
against.

Two design decisions worth stating, because both look arbitrary and are not:

**The full index, no sampling.** Balancing sector counts by sampling down to
the smallest sector would discard information: within-sector ranks want the
maximum n per sector, and rank-based composites are unaffected by unequal
group sizes. Every GICS sector already carries at least 21 names, so
within-sector percentiles are computable everywhere without intervention.

**Sector-relative value and quality; universe-wide momentum.** Raw valuation
ratios are dominated by industry effects — banks always screen cheap on P/E,
software always expensive — so a cross-sector value rank partly measures
sector membership rather than cheapness. Ranking within sector first is the
standard industry-adjustment from the factor-construction literature, and it
retires the financials caveat this repo previously had to attach to bank
value-trap flags. Momentum gets no such adjustment: the standard
Jegadeesh-Titman construction is universe-wide, and price momentum does not
carry the same structural industry bias.

Sector labels come from Yahoo for every name, benchmark and tracked alike.
The pinned CSV's GICS labels define *membership* and document balance, but
mixing two taxonomies in the ranking itself would put a tracked name in one
bucket and its true peers in another.
"""
from __future__ import annotations

import csv
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

from .factors import (
    MomentumMetrics, _earnings_yield, _ebitda_yield, _fcf_yield, _pct_rank,
    momentum_metrics,
)
from .fetch import Fundamentals, _clean, _positive

REPO_ROOT = Path(__file__).resolve().parents[2]
CONSTITUENTS = REPO_ROOT / "config" / "benchmark" / "sp500.csv"

# The last successful fetch, committed. Serves three purposes: same-day reuse
# so a local refresh is not a five-minute wait, a fallback when Yahoo throttles
# 500 consecutive info calls, and the source for historical decile
# breakpoints. Valuation and quality fields move quarterly, so a snapshot a
# few days stale is harmless — and far better than dropping the whole
# benchmark leg and silently reverting every percentile to watchlist-relative.
SNAPSHOT = REPO_ROOT / "history" / "benchmark_snapshot.json"

# Two years is comfortably more than the 273 sessions a 12-1 momentum window
# needs, and a fraction of the download weight of the display universe's 5y.
BENCHMARK_PERIOD = "2y"

# A sector needs enough members before a within-sector percentile beats a
# universe-wide one. Every S&P sector clears this by a wide margin; the guard
# exists for tracked names whose Yahoo sector has no benchmark counterpart.
MIN_SECTOR_MEMBERS = 15


@dataclass
class BenchmarkMember:
    """One benchmark name, reduced to what ranking needs."""
    symbol: str
    sector: Optional[str] = None
    momentum: Optional[float] = None          # 12-1 formation return
    ebitda_yield: Optional[float] = None
    fcf_yield: Optional[float] = None
    earnings_yield: Optional[float] = None
    roe: Optional[float] = None
    roa: Optional[float] = None
    operating_margin: Optional[float] = None
    low_leverage: Optional[float] = None      # negated debt/equity


@dataclass
class BenchmarkPopulation:
    """The fetched cross-section, ready to rank against."""
    members: dict[str, BenchmarkMember] = field(default_factory=dict)
    as_of: Optional[str] = None
    failures: dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def available(self) -> bool:
        return self.error is None and len(self.members) >= 100

    def by_sector(self) -> dict[str, list[BenchmarkMember]]:
        out: dict[str, list[BenchmarkMember]] = {}
        for m in self.members.values():
            if m.sector:
                out.setdefault(m.sector, []).append(m)
        return out


def save_population(pop: BenchmarkPopulation, path: Optional[Path] = None) -> None:
    import json
    from dataclasses import asdict

    path = Path(path) if path else SNAPSHOT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "as_of": pop.as_of,
        "members": {k: asdict(v) for k, v in pop.members.items()},
    }, separators=(",", ":")))


def load_snapshot(path: Optional[Path] = None) -> Optional[BenchmarkPopulation]:
    import json

    path = Path(path) if path else SNAPSHOT
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
    except Exception:                                 # noqa: BLE001
        return None
    members = {k: BenchmarkMember(**v) for k, v in (raw.get("members") or {}).items()}
    if not members:
        return None
    return BenchmarkPopulation(members=members, as_of=raw.get("as_of"))


def get_population(session: Optional[str] = None,
                   force: bool = False,
                   limit: Optional[int] = None) -> BenchmarkPopulation:
    """The benchmark cross-section, fetching only when the snapshot is stale.

    ``session`` is the latest trading date the display universe saw. When the
    snapshot already covers it there is nothing to gain from refetching 500
    names. A fetch failure falls back to the snapshot rather than dropping the
    benchmark leg, because silently reverting every percentile to a
    watchlist-relative one would change what the numbers mean without saying so.
    """
    snapshot = load_snapshot()
    if not force and snapshot and session and snapshot.as_of == session:
        print(f"benchmark: snapshot already current ({snapshot.as_of})")
        return snapshot

    try:
        fresh = fetch_population(limit=limit)
    except Exception as exc:                          # noqa: BLE001
        fresh = BenchmarkPopulation(error=f"{type(exc).__name__}: {exc}")

    if fresh.available:
        save_population(fresh)
        return fresh

    if snapshot:
        print(f"::warning::benchmark fetch failed ({fresh.error or 'too few members'}); "
              f"using snapshot from {snapshot.as_of}")
        snapshot.error = None
        return snapshot
    return fresh


def load_constituents(path: Optional[Path] = None) -> list[dict]:
    path = Path(path) if path else CONSTITUENTS
    if not path.exists():
        return []
    with path.open() as fh:
        return list(csv.DictReader(fh))


def _member_from(symbol: str, closes: Sequence[float],
                 f: Optional[Fundamentals]) -> BenchmarkMember:
    m = BenchmarkMember(symbol=symbol)
    if closes:
        m.momentum = momentum_metrics(closes).mom_12_1
    if f is None:
        return m
    m.sector = f.sector
    m.ebitda_yield = _ebitda_yield(f)
    m.fcf_yield = _fcf_yield(f)
    m.earnings_yield = _earnings_yield(f)
    m.roe = f.return_on_equity
    m.roa = f.return_on_assets
    m.operating_margin = f.operating_margin
    m.low_leverage = -f.debt_to_equity if f.debt_to_equity is not None else None
    return m


def fetch_population(symbols: Optional[Sequence[str]] = None,
                     period: str = BENCHMARK_PERIOD,
                     pause: float = 0.15,
                     limit: Optional[int] = None) -> BenchmarkPopulation:
    """Download the benchmark cross-section: batched prices, per-name info."""
    from .fetch import fetch_fundamentals, fetch_history

    if symbols is None:
        rows = load_constituents()
        if not rows:
            return BenchmarkPopulation(error="no pinned constituent list")
        symbols = [r["symbol"] for r in rows]
    symbols = list(symbols)[:limit] if limit else list(symbols)

    print(f"benchmark: fetching {len(symbols)} constituents")
    bars, failures = fetch_history(symbols, period=period)
    print(f"  prices: {len(bars)} ok, {len(failures)} failed")

    got = [s for s in symbols if s in bars]
    fundamentals = fetch_fundamentals(got, pause=pause)

    members: dict[str, BenchmarkMember] = {}
    for symbol in got:
        closes = [b.close for b in bars[symbol]]
        members[symbol] = _member_from(symbol, closes, fundamentals.get(symbol))

    with_sector = sum(1 for m in members.values() if m.sector)
    print(f"  members: {len(members)} ({with_sector} with a sector)")

    as_of = max((bars[s][-1].date for s in got), default=None)
    return BenchmarkPopulation(members=members, as_of=as_of, failures=failures)


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BenchmarkRank:
    """One name's standing against the benchmark population."""
    value: Optional[float]
    percentile: Optional[float]
    population: str            # "S&P 500" or a sector name
    n: int


def _values(members: Iterable[BenchmarkMember], attr: str) -> list[float]:
    return [v for v in (getattr(m, attr) for m in members) if v is not None]


class BenchmarkRanker:
    """Ranks arbitrary values against the fetched population.

    Built once per refresh; every tracked name is ranked through it, so the
    population is identical for all of them.
    """

    def __init__(self, population: BenchmarkPopulation,
                 min_sector: int = MIN_SECTOR_MEMBERS):
        self.population = population
        self.min_sector = min_sector
        self._all = list(population.members.values())
        self._by_sector = population.by_sector()

    @property
    def n(self) -> int:
        return len(self._all)

    def sector_size(self, sector: Optional[str]) -> int:
        return len(self._by_sector.get(sector or "", []))

    def rank_universe(self, value: Optional[float], attr: str) -> BenchmarkRank:
        pop = _values(self._all, attr)
        return BenchmarkRank(
            value=value,
            percentile=_pct_rank(value, pop) if value is not None else None,
            population="S&P 500",
            n=len(pop),
        )

    def rank_sector(self, value: Optional[float], attr: str,
                    sector: Optional[str]) -> BenchmarkRank:
        """Rank within sector, falling back to the whole index when thin.

        The fallback names itself in ``population`` so a reader always knows
        which comparison produced the number.
        """
        members = self._by_sector.get(sector or "", [])
        if sector and len(members) >= self.min_sector:
            pop = _values(members, attr)
            if len(pop) >= 2:
                return BenchmarkRank(
                    value=value,
                    percentile=_pct_rank(value, pop) if value is not None else None,
                    population=sector,
                    n=len(pop),
                )
        return self.rank_universe(value, attr)

    def composite_sector(self, inputs: dict[str, Optional[float]],
                         sector: Optional[str]) -> tuple[Optional[float], tuple[str, ...], str, int]:
        """Mean of available sector-relative percentiles.

        Returns (score, metrics_used, population_label, n). Missing metrics
        drop out rather than being imputed — the same rule the watchlist-
        relative composites follow.
        """
        ranks: list[float] = []
        used: list[str] = []
        label, n = "S&P 500", self.n
        for attr, value in inputs.items():
            if value is None:
                continue
            r = self.rank_sector(value, attr, sector)
            if r.percentile is None:
                continue
            ranks.append(r.percentile)
            used.append(attr)
            label, n = r.population, r.n
        if not ranks:
            return None, (), label, n
        return sum(ranks) / len(ranks), tuple(used), label, n


VALUE_ATTRS = ("ebitda_yield", "fcf_yield", "earnings_yield")
QUALITY_ATTRS = ("roe", "roa", "operating_margin", "low_leverage")


def decile_breakpoints(population: BenchmarkPopulation) -> dict[str, list[float]]:
    """Deciles per factor input — a compact snapshot of the distribution.

    History stores these rather than 503 rows per day: placing a tracked name
    historically needs the shape of the distribution, not every member of it.
    """
    out: dict[str, list[float]] = {}
    members = list(population.members.values())
    for attr in ("momentum",) + VALUE_ATTRS + QUALITY_ATTRS:
        vals = sorted(_values(members, attr))
        if len(vals) < 20:
            continue
        out[attr] = [
            round(vals[min(int(q / 10 * len(vals)), len(vals) - 1)], 6)
            for q in range(1, 10)
        ]
    return out
