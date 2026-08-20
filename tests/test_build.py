"""Payload assembly, on synthetic bars — no network, so this runs in CI."""
import json
import math

import pytest

from market_desk.build import build_index_row, build_symbol_payload, write_all
from market_desk.config import Settings, Tier, Universe
from market_desk.fetch import Bar, FetchResult, Fundamentals
from market_desk.macro import MacroOverlay
from market_desk.valuation import build_valuations


def make_bars(n=400, start=100.0):
    bars = []
    price = start
    for i in range(n):
        price *= 1 + 0.0004 + 0.01 * math.sin(i / 9)
        day = 1 + (i % 28)
        month = 1 + ((i // 28) % 12)
        year = 2023 + (i // 336)
        bars.append(Bar(
            date=f"{year}-{month:02d}-{day:02d}",
            open=price * 0.995, high=price * 1.01,
            low=price * 0.99, close=price, volume=1e6 + i * 1000,
        ))
    return bars


@pytest.fixture
def fixture_result():
    return FetchResult(
        fetch_date="2026-08-19",
        bars={"AAA": make_bars(), "ETF": make_bars(start=50.0)},
        fundamentals={
            "AAA": Fundamentals(symbol="AAA", name="Alpha Inc", sector="Technology",
                                quote_type="EQUITY", trailing_pe=25.0, forward_pe=20.0,
                                trailing_eps=4.0, market_cap=1e11,
                                fifty_two_week_low=80.0, fifty_two_week_high=160.0),
            "ETF": Fundamentals(symbol="ETF", name="Broad ETF", quote_type="ETF"),
        },
    )


@pytest.fixture
def universe():
    return Universe(
        tiers=(Tier("core", "Core", "", ("ETF",)), Tier("watchlist", "Watchlist", "", ("AAA",))),
        settings=Settings(),
    )


def test_index_row_has_the_screening_fields(fixture_result, universe):
    vals = build_valuations(fixture_result.fundamentals)
    row = build_index_row("AAA", fixture_result, vals["AAA"], universe)
    for key in ("symbol", "last", "change_1d", "change_1y", "volume", "rvol",
                "trailing_pe", "divergence", "rsi14", "market_cap"):
        assert key in row
    assert row["tier"] == "watchlist"
    assert 0.0 <= row["range_52w_position"] <= 1.0


def test_missing_values_stay_none_never_zero(fixture_result, universe):
    vals = build_valuations(fixture_result.fundamentals)
    row = build_index_row("ETF", fixture_result, vals["ETF"], universe)
    # An ETF has no P/E. Reporting 0.0 would sort it as the cheapest name
    # on the board, which is the exact failure this guards.
    assert row["trailing_pe"] is None
    assert row["pe_percentile"] is None


def test_symbol_payload_series_align_with_candles(fixture_result, universe):
    vals = build_valuations(fixture_result.fundamentals)
    payload = build_symbol_payload("AAA", fixture_result, vals["AAA"], None,
                                   MacroOverlay(), universe)
    n = len(payload["candles"])
    for name, series in payload["indicators"].items():
        assert len(series) == n, f"{name} is not index-aligned with the candles"
    for name, series in payload["volume_analytics"].items():
        if isinstance(series, list):
            assert len(series) == n, f"{name} is not index-aligned with the candles"


def test_payload_is_json_serializable(fixture_result, universe):
    vals = build_valuations(fixture_result.fundamentals)
    payload = build_symbol_payload("AAA", fixture_result, vals["AAA"], None,
                                   MacroOverlay(), universe)
    json.loads(json.dumps(payload))


def test_write_all_emits_the_three_artifacts(tmp_path, fixture_result, universe):
    vals = build_valuations(fixture_result.fundamentals)
    meta = write_all(universe, fixture_result, vals, {}, MacroOverlay(),
                     forecaster_pin="deadbeef", data_dir=tmp_path)

    index = json.loads((tmp_path / "index.json").read_text())
    assert {r["symbol"] for r in index["rows"]} == {"AAA", "ETF"}
    assert [t["key"] for t in index["tiers"]] == ["core", "watchlist"]

    assert (tmp_path / "symbols" / "AAA.json").exists()
    assert meta["symbols_ok"] == 2
    assert meta["forecaster_pin"] == "deadbeef"
    assert "not trading advice" in meta["disclaimer"]


def test_tiers_only_list_symbols_that_actually_fetched(tmp_path, universe):
    partial = FetchResult(fetch_date="2026-08-19", bars={"AAA": make_bars()},
                          fundamentals={}, failures={"ETF": "delisted"})
    write_all(universe, partial, {}, {}, MacroOverlay(), data_dir=tmp_path)
    index = json.loads((tmp_path / "index.json").read_text())
    core = next(t for t in index["tiers"] if t["key"] == "core")
    # A symbol that failed must not leave a dead row in the sidebar.
    assert core["symbols"] == []
