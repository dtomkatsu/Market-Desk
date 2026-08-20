"""Scheduled catalysts — the "when" half, and the only genuinely known one.

Volatility regimes say a stock is *currently* unsettled. Earnings dates say
something stronger: a specific future date on which a move is unusually
likely, known weeks ahead. That is the most actionable timing fact available
without predicting anything.

The measure that makes it useful is per-stock, not assumed. For each past
announcement, take the first session's absolute move and compare its median
against the median of all non-earnings sessions. The ratio is how much that
name's earnings day actually amplifies its ordinary movement — and it varies
enormously. A 2026-08 pass over one watchlist found XYL at 5.1x and TT at
4.1x, but NXT at **0.6x**: its earnings days are *quieter* than its typical
day, because the stock is driven by something other than quarterly results.
Assuming "earnings = big move" would have been wrong for that name.

Medians, not means: a single outlier announcement should not set the
expectation, and earnings reactions are exactly where outliers live.

What is deliberately absent: options-implied moves. Yahoo's chains for this
kind of universe are too thin to trust — a 2026-08 probe returned nine
contracts for XYL with an implied volatility of 0.00001. Silently wrong data
is worse than missing data, so this module uses realized history only.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional, Sequence

from .fetch import Bar


@dataclass(frozen=True)
class EarningsReaction:
    """How this name has actually behaved on its announcement days."""
    n_events: int
    median_move: float             # |move| on the reaction session
    baseline_move: float           # |move| on ordinary sessions
    amplification: float           # median ÷ baseline
    largest_move: float

    @property
    def meaningful(self) -> bool:
        """Whether earnings genuinely move this name more than a normal day."""
        return self.n_events >= 4 and self.amplification >= 1.25


@dataclass
class Catalysts:
    symbol: str
    next_earnings: Optional[str] = None
    days_until: Optional[int] = None
    past_earnings: list[str] = field(default_factory=list)
    reaction: Optional[EarningsReaction] = None
    ex_dividend: Optional[str] = None
    error: Optional[str] = None


def _abs_moves(bars: Sequence[Bar]) -> dict[str, float]:
    out: dict[str, float] = {}
    for i in range(1, len(bars)):
        prev, cur = bars[i - 1].close, bars[i].close
        if prev > 0:
            out[bars[i].date] = abs(cur / prev - 1)
    return out


# US equity market hours, Eastern. Announcements land before the open or
# after the close; the rare intraday one is treated as same-session.
MARKET_OPEN_HOUR = 9
MARKET_CLOSE_HOUR = 16


def announcement_date(stamp: str) -> tuple[str, bool]:
    """Split an announcement timestamp into (date, reacts_next_session).

    A company reporting at 06:00 moves that day's session; one reporting at
    16:00 moves the NEXT session, because the market has already closed.
    Ignoring the hour and always taking the announcement date measures an
    ordinary day for every after-close reporter — which made NVDA and MSFT
    look like earnings barely moved them (0.7x and 0.8x) when the reaction
    was simply on the following bar.
    """
    if "T" not in stamp:
        return stamp, False          # date only: assume pre-market
    day, rest = stamp.split("T", 1)
    try:
        hour = int(rest[:2])
    except (ValueError, IndexError):
        return day, False
    return day, hour >= MARKET_CLOSE_HOUR


def measure_reaction(bars: Sequence[Bar],
                     earnings_dates: Sequence[str]) -> Optional[EarningsReaction]:
    """Compare announcement-session moves against ordinary sessions."""
    moves = _abs_moves(bars)
    if not moves:
        return None
    sessions = sorted(moves)

    reaction_days: set[str] = set()
    for stamp in earnings_dates:
        day, next_session = announcement_date(stamp)
        candidates = [d for d in sessions if d >= day]
        if not candidates:
            continue
        if next_session:
            # Skip the announcement session itself; the news lands after it.
            after = [d for d in candidates if d > day]
            if not after:
                continue
            reaction_days.add(after[0])
        else:
            reaction_days.add(candidates[0])

    event_moves = [moves[d] for d in reaction_days if d in moves]
    other_moves = [v for d, v in moves.items() if d not in reaction_days]
    if len(event_moves) < 3 or len(other_moves) < 30:
        return None

    median = statistics.median(event_moves)
    baseline = statistics.median(other_moves)
    if baseline <= 0:
        return None

    return EarningsReaction(
        n_events=len(event_moves),
        median_move=median,
        baseline_move=baseline,
        amplification=median / baseline,
        largest_move=max(event_moves),
    )


def build_catalysts(symbol: str, bars: Sequence[Bar],
                    earnings_dates: Sequence[str],
                    ex_dividend: Optional[str] = None,
                    today: Optional[date] = None) -> Catalysts:
    today = today or date.today()
    last_session = bars[-1].date if bars else today.isoformat()

    # "Future" means after the last bar we hold, not after the wall clock:
    # a date between the two would otherwise look upcoming while its
    # reaction is already in the price history.
    dated = [(announcement_date(d)[0], d) for d in earnings_dates]
    future = sorted(day for day, _ in dated if day > last_session)
    past = sorted(raw for day, raw in dated if day <= last_session)

    next_earnings = future[0] if future else None
    days_until = None
    if next_earnings:
        try:
            days_until = (date.fromisoformat(next_earnings) - today).days
        except ValueError:
            days_until = None

    return Catalysts(
        symbol=symbol,
        next_earnings=next_earnings,
        days_until=days_until,
        past_earnings=past,
        reaction=measure_reaction(bars, past),
        ex_dividend=ex_dividend,
    )


def describe(cat: Catalysts) -> str:
    """One plain sentence for the dashboard and the analysis note."""
    if not cat.next_earnings:
        return "No scheduled earnings date reported."
    when = (f"in {cat.days_until} days" if cat.days_until is not None
            and cat.days_until >= 0 else "soon")
    if cat.reaction is None:
        return f"Reports {cat.next_earnings} ({when}); too few past events to size the reaction."
    r = cat.reaction
    if r.meaningful:
        return (f"Reports {cat.next_earnings} ({when}). Past announcement days moved "
                f"a median {r.median_move * 100:.1f}%, {r.amplification:.1f}x an "
                f"ordinary session ({r.n_events} events).")
    return (f"Reports {cat.next_earnings} ({when}). Past announcement days moved a "
            f"median {r.median_move * 100:.1f}% — only {r.amplification:.1f}x an "
            f"ordinary session, so earnings are not an unusual event for this name.")
