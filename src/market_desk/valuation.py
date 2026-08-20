"""Valuation analytics: P/E and the ratios that give it context.

The single most important rule here is that **a multiple is only
meaningful against a comparison set**. A 35× trailing P/E is expensive
for a utility and unremarkable for software. So nothing in this module
reports a raw multiple as a verdict; every judgment is a rank inside a
peer group, and the peer group is always named in the output so the
reader can see what the number was measured against.

Peer groups are sectors when a sector has enough members in the tracked
universe, and the whole universe otherwise. With a 25-name watchlist
most sectors will *not* clear that bar, which is a real limitation of
comparing against a small universe rather than the full market — it is
surfaced in the output as ``peer_group``, not hidden.

Funds and ETFs are excluded from every peer group: their reported P/E,
where one exists at all, is a holdings-weighted aggregate that is not
comparable to a single company's.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .fetch import Fundamentals


@dataclass(frozen=True)
class Rank:
    """Where one value sits inside its peer group."""
    value: float
    percentile: float          # 0-1; 0 = lowest value in the group
    peer_group: str
    peer_count: int
    median: float


@dataclass
class ValuationView:
    """The full valuation read for one symbol."""
    symbol: str
    peer_group: str
    peer_count: int
    trailing_pe: Optional[float] = None
    forward_pe: Optional[float] = None
    earnings_yield: Optional[float] = None      # 1 / trailing P/E
    pe_rank: Optional[Rank] = None
    forward_pe_rank: Optional[Rank] = None
    pb_rank: Optional[Rank] = None
    ps_rank: Optional[Rank] = None
    pe_vs_forward: Optional[float] = None       # forward ÷ trailing
    peg_ratio: Optional[float] = None
    notes: tuple[str, ...] = ()


def _percentile(value: float, population: list[float]) -> float:
    """Fraction of the population at or below ``value``."""
    if not population:
        return 0.5
    at_or_below = sum(1 for p in population if p <= value)
    return at_or_below / len(population)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def build_peer_groups(fundamentals: dict[str, Fundamentals],
                      min_peers: int = 4) -> dict[str, tuple[str, list[str]]]:
    """Map each company symbol to (peer_group_name, peer_symbols).

    Funds are mapped to a ``fund`` group with no peers, so downstream
    code has one uniform shape to handle rather than a None special case.
    """
    companies = {s: f for s, f in fundamentals.items() if not f.is_fund}

    by_sector: dict[str, list[str]] = {}
    for sym, f in companies.items():
        by_sector.setdefault(f.sector or "Unclassified", []).append(sym)

    out: dict[str, tuple[str, list[str]]] = {}
    all_companies = sorted(companies)
    for sym, f in companies.items():
        sector = f.sector or "Unclassified"
        peers = by_sector.get(sector, [])
        if sector != "Unclassified" and len(peers) >= min_peers:
            out[sym] = (sector, peers)
        else:
            # Too thin to be a real sector comparison; fall back to the
            # whole tracked universe and say so in the label.
            out[sym] = ("tracked universe", all_companies)

    for sym, f in fundamentals.items():
        if f.is_fund:
            out[sym] = ("fund", [])
    return out


def _rank_for(symbol: str, peers: list[str], group: str,
              values: dict[str, Optional[float]]) -> Optional[Rank]:
    own = values.get(symbol)
    if own is None:
        return None
    population = [v for s, v in values.items() if s in peers and v is not None]
    if len(population) < 2:
        return None
    return Rank(
        value=own,
        percentile=_percentile(own, population),
        peer_group=group,
        peer_count=len(population),
        median=_median(population),
    )


def build_valuations(fundamentals: dict[str, Fundamentals],
                     min_peers: int = 4) -> dict[str, ValuationView]:
    """Valuation view for every symbol."""
    groups = build_peer_groups(fundamentals, min_peers=min_peers)

    trailing = {s: f.trailing_pe for s, f in fundamentals.items()}
    forward = {s: f.forward_pe for s, f in fundamentals.items()}
    pb = {s: f.price_to_book for s, f in fundamentals.items()}
    ps = {s: f.price_to_sales for s, f in fundamentals.items()}

    out: dict[str, ValuationView] = {}
    for symbol, f in fundamentals.items():
        group, peers = groups.get(symbol, ("tracked universe", []))
        notes: list[str] = []

        if f.is_fund:
            notes.append(
                "Fund or ETF — a holdings-weighted P/E is not comparable to a "
                "single company's, so no valuation rank is computed."
            )
        elif f.trailing_pe is None:
            if f.trailing_eps is not None and f.trailing_eps <= 0:
                notes.append(
                    f"No trailing P/E: trailing EPS is {f.trailing_eps:.2f}. "
                    "The company is not profitable on a trailing basis."
                )
            else:
                notes.append("No trailing P/E reported by the data source.")

        earnings_yield = (1.0 / f.trailing_pe) if f.trailing_pe else None
        pe_vs_forward = (
            f.forward_pe / f.trailing_pe
            if (f.forward_pe and f.trailing_pe) else None
        )
        if pe_vs_forward is not None and pe_vs_forward < 0.9:
            notes.append(
                "Forward P/E is materially below trailing — the street expects "
                "earnings to grow into the multiple."
            )
        elif pe_vs_forward is not None and pe_vs_forward > 1.1:
            notes.append(
                "Forward P/E is above trailing — consensus looks for earnings "
                "to fall."
            )

        out[symbol] = ValuationView(
            symbol=symbol,
            peer_group=group,
            peer_count=len(peers),
            trailing_pe=f.trailing_pe,
            forward_pe=f.forward_pe,
            earnings_yield=earnings_yield,
            pe_rank=None if f.is_fund else _rank_for(symbol, peers, group, trailing),
            forward_pe_rank=None if f.is_fund else _rank_for(symbol, peers, group, forward),
            pb_rank=None if f.is_fund else _rank_for(symbol, peers, group, pb),
            ps_rank=None if f.is_fund else _rank_for(symbol, peers, group, ps),
            pe_vs_forward=pe_vs_forward,
            peg_ratio=f.peg_ratio,
            notes=tuple(notes),
        )
    return out


def describe_rank(rank: Optional[Rank], kind: str = "P/E") -> str:
    """One plain sentence for a rank, for the analysis note and tooltips."""
    if rank is None:
        return f"No comparable {kind}."
    pct = rank.percentile
    if pct <= 0.25:
        band = "in the cheapest quartile of"
    elif pct <= 0.5:
        band = "below the median of"
    elif pct <= 0.75:
        band = "above the median of"
    else:
        band = "in the most expensive quartile of"
    return (
        f"{kind} {rank.value:.1f} is {band} its {rank.peer_group} peer group "
        f"(n={rank.peer_count}, median {rank.median:.1f})."
    )
