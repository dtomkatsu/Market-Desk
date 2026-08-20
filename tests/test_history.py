"""Factor history. The invariant that matters most is no look-ahead: a
backfilled momentum value at date T must depend only on bars up to T."""
import math

import pytest

from market_desk.factors import SESSIONS_12M, SESSIONS_1M, momentum_metrics
from market_desk.fetch import Bar, Fundamentals
from market_desk.history import (
    HistoryRow, backfill_momentum, drift, load_history, series_by_symbol,
    snapshot_today, write_history,
)


def make_bars(n=400, seed=1.0, slope=0.0008):
    bars = []
    price = 100.0 * seed
    for i in range(n):
        price *= math.exp(slope) * (1 + 0.01 * math.sin(i / 6))
        y, m, d = 2023 + i // 336, 1 + (i // 28) % 12, 1 + i % 28
        bars.append(Bar(date=f"{y}-{m:02d}-{d:02d}", open=price, high=price * 1.01,
                        low=price * 0.99, close=price, volume=1e6))
    return bars


@pytest.fixture
def universe():
    bars = {"AAA": make_bars(seed=1.0, slope=0.002),
            "BBB": make_bars(seed=1.5, slope=0.0005),
            "CCC": make_bars(seed=0.8, slope=-0.001),
            "ETF": make_bars(seed=1.2, slope=0.001)}
    fundamentals = {
        "AAA": Fundamentals("AAA", quote_type="EQUITY"),
        "BBB": Fundamentals("BBB", quote_type="EQUITY"),
        "CCC": Fundamentals("CCC", quote_type="EQUITY"),
        "ETF": Fundamentals("ETF", quote_type="ETF"),
    }
    return bars, fundamentals


def test_backfill_has_no_lookahead(universe):
    """Every backfilled value must equal a recomputation from truncated bars."""
    bars, fundamentals = universe
    rows = backfill_momentum(bars, fundamentals, step=21)
    assert rows

    closes = {s: [b.close for b in bs] for s, bs in bars.items()}
    dates = {s: {b.date: i for i, b in enumerate(bs)} for s, bs in bars.items()}
    checked = 0
    for row in rows:
        i = dates[row.symbol].get(row.date)
        if i is None:
            continue
        expected = momentum_metrics(closes[row.symbol][:i + 1]).mom_12_1
        assert expected is not None
        assert row.mom_12_1 == pytest.approx(expected, abs=1e-5)
        checked += 1
    assert checked > 10


def test_backfill_excludes_funds_from_ranks(universe):
    bars, fundamentals = universe
    rows = backfill_momentum(bars, fundamentals, step=21)
    etf_rows = [r for r in rows if r.symbol == "ETF"]
    assert etf_rows, "the fund should still get a raw momentum value"
    assert all(r.mom_rank is None for r in etf_rows), "funds must not be ranked"
    assert any(r.mom_rank is not None for r in rows if r.symbol == "AAA")


def test_backfill_never_carries_value_or_quality(universe):
    """Those fields have no trustworthy history and must stay empty."""
    bars, fundamentals = universe
    for row in backfill_momentum(bars, fundamentals, step=21):
        assert row.value_score is None
        assert row.quality_score is None
        assert row.source == "backfill"


def test_backfill_needs_a_full_formation_window(universe):
    bars, fundamentals = universe
    rows = backfill_momentum(bars, fundamentals, step=21)
    earliest = min(r.date for r in rows)
    first_valid = bars["AAA"][SESSIONS_12M + SESSIONS_1M].date
    assert earliest >= first_valid


def test_write_is_deduplicated_and_live_wins(tmp_path):
    path = tmp_path / "factors.jsonl"
    rows = [
        HistoryRow("2026-01-02", "AAA", "backfill", mom_12_1=0.1, mom_rank=0.5),
        HistoryRow("2026-01-02", "AAA", "live", mom_12_1=0.1, mom_rank=0.5,
                   value_score=0.7, quality_score=0.6),
        HistoryRow("2026-01-03", "AAA", "backfill", mom_12_1=0.2),
    ]
    assert write_history(rows, path) == 2
    back = load_history(path)
    same_day = [r for r in back if r.date == "2026-01-02"]
    assert len(same_day) == 1
    # The live row carries value/quality; the backfill cannot, so it must win.
    assert same_day[0].source == "live"
    assert same_day[0].value_score == 0.7


def test_write_is_idempotent(tmp_path):
    path = tmp_path / "factors.jsonl"
    rows = [HistoryRow("2026-01-02", "AAA", "live", mom_rank=0.5)]
    write_history(rows, path)
    first = path.read_text()
    write_history(load_history(path), path)
    assert path.read_text() == first


def test_corrupt_line_does_not_kill_the_load(tmp_path):
    path = tmp_path / "factors.jsonl"
    path.write_text('{"date":"2026-01-02","symbol":"AAA","source":"live"}\n'
                    '{ this is not json\n'
                    '{"date":"2026-01-03","symbol":"AAA","source":"live"}\n')
    rows = load_history(path)
    assert len(rows) == 2


def test_snapshot_carries_value_and_quality():
    class FV:
        def __init__(self):
            self.momentum = type("M", (), {"mom_12_1": 0.3})()
            self.momentum_rank = 0.8
            self.value_score = 0.6
            self.quality_score = 0.4
            self.value_trap = True
            self.reversal_tension = False
    rows = snapshot_today("2026-08-19", {"AAA": FV()})
    assert rows[0].source == "live"
    assert rows[0].value_score == 0.6
    assert rows[0].value_trap is True


def test_series_and_drift():
    rows = [
        HistoryRow("2026-01-02", "AAA", "backfill", mom_rank=0.2),
        HistoryRow("2026-02-02", "AAA", "backfill", mom_rank=0.5),
        HistoryRow("2026-03-02", "AAA", "live", mom_rank=0.9, value_score=0.4),
    ]
    series = series_by_symbol(rows)["AAA"]
    assert [p["d"] for p in series] == ["2026-01-02", "2026-02-02", "2026-03-02"]
    d = drift(series, "m")
    assert d["from"] == 0.2 and d["to"] == 0.9
    assert d["change"] == pytest.approx(0.7)
    # One value point is not a trend.
    assert drift(series, "v") is None
