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


# ---------------- analysis notes ----------------

def test_collect_notes_reads_dated_markdown(tmp_path):
    from market_desk.build import collect_notes
    (tmp_path / "2026-08-17.md").write_text("# older\n")
    (tmp_path / "2026-08-19.md").write_text("# newest\n")
    (tmp_path / "2026-08-18.md").write_text("# middle\n")
    (tmp_path / "README.md").write_text("not a note")
    (tmp_path / "notes.txt").write_text("also not a note")

    notes = collect_notes(tmp_path)
    # Newest first, and only YYYY-MM-DD.md files.
    assert [n["date"] for n in notes] == ["2026-08-19", "2026-08-18", "2026-08-17"]
    assert notes[0]["body"] == "# newest\n"


def test_collect_notes_handles_a_missing_directory(tmp_path):
    from market_desk.build import collect_notes
    assert collect_notes(tmp_path / "nope") == []


def test_write_all_emits_notes_json(tmp_path, fixture_result, universe):
    import json as _json
    from market_desk.build import write_all
    meta = write_all(universe, fixture_result, {}, {}, MacroOverlay(), data_dir=tmp_path)
    payload = _json.loads((tmp_path / "notes.json").read_text())
    assert "notes" in payload
    assert meta["notes_count"] == len(payload["notes"])


# ---------------- portfolio ----------------

def test_portfolio_scores_holdings_against_the_universe_not_each_other(tmp_path):
    """A 3-position book has no cross-section; ranks must come from outside."""
    import yaml as _yaml
    from market_desk.factors import FactorView, MomentumMetrics
    from market_desk.portfolio import analyze, load_holdings

    (tmp_path / "h.yml").write_text(_yaml.safe_dump({
        "as_of": "2026-08-19",
        "positions": [
            {"symbol": "AAA", "exposure": 0.40},
            {"symbol": "BBB", "exposure": 0.20},
        ],
        "cash": [{"symbol": "CASH", "exposure": 0.40}],
    }))
    holdings = load_holdings(tmp_path / "h.yml")
    assert holdings.available
    assert holdings.cash_weight == pytest.approx(0.40)

    views = {
        "AAA": FactorView("AAA", False, 20, MomentumMetrics(0.5), 0.9,
                          value_score=0.8, quality_score=0.2),
        "BBB": FactorView("BBB", False, 20, MomentumMetrics(0.1), 0.3,
                          value_score=0.4, quality_score=0.6),
    }
    a = analyze(holdings, views, {"AAA": "Tech", "BBB": "Tech"})

    # Weights renormalize onto the equity sleeve (0.4/0.6 and 0.2/0.6), so
    # cash does not drag every tilt toward zero.
    assert a.momentum_tilt == pytest.approx((2 / 3) * 0.9 + (1 / 3) * 0.3)
    # Equal-weight ignores sizing entirely.
    assert a.momentum_tilt_equal == pytest.approx((0.9 + 0.3) / 2)
    assert a.cash_weight == pytest.approx(0.40)


def test_portfolio_reports_concentration(tmp_path):
    import yaml as _yaml
    from market_desk.portfolio import analyze, load_holdings
    (tmp_path / "h.yml").write_text(_yaml.safe_dump({
        "positions": [{"symbol": "BIG", "exposure": 0.9},
                      {"symbol": "SMALL", "exposure": 0.1}],
    }))
    a = analyze(load_holdings(tmp_path / "h.yml"), {}, {})
    assert a.top_weight == pytest.approx(0.9)
    assert a.hhi == pytest.approx(0.82)
    assert a.effective_positions == pytest.approx(1 / 0.82)
    assert any("Concentrated" in n for n in a.notes)


def test_portfolio_flags_unscored_holdings(tmp_path):
    import yaml as _yaml
    from market_desk.portfolio import analyze, load_holdings
    (tmp_path / "h.yml").write_text(_yaml.safe_dump({
        "positions": [{"symbol": "UNKNOWN", "exposure": 1.0}],
    }))
    a = analyze(load_holdings(tmp_path / "h.yml"), {}, {})
    # Held but absent from the tracked cross-section — reported, not silent.
    assert a.unscored_weight == pytest.approx(1.0)
    assert a.momentum_tilt is None


def test_missing_holdings_file_is_not_an_error(tmp_path):
    from market_desk.portfolio import load_holdings
    h = load_holdings(tmp_path / "absent.yml")
    assert h.available is False
    assert "no holdings file" in (h.error or "")


def test_portfolio_payload_never_carries_dollar_figures(tmp_path, fixture_result, universe):
    """The published payload must be weights-only, by construction.

    build.py loads holdings with include_local=False so cost basis and
    market value cannot reach docs/ even if someone later forgets to strip
    them here. This asserts the guarantee rather than trusting it.
    """
    import json as _json
    import yaml as _yaml
    from market_desk.build import write_all
    from market_desk.factors import FactorView, MomentumMetrics
    from market_desk import portfolio as pf

    pub = tmp_path / "holdings.yml"
    loc = tmp_path / "holdings.local.yml"
    pub.write_text(_yaml.safe_dump({
        "as_of": "2026-08-19",
        "positions": [{"symbol": "AAA", "exposure": 0.6},
                      {"symbol": "ETF", "exposure": 0.4}],
    }))
    loc.write_text(_yaml.safe_dump({
        "positions": [{"symbol": "AAA", "market_value": 123456.78,
                       "cost_basis": 99999.11}],
        "totals": {"market_value": 222222.22},
    }))

    original_pub, original_loc = pf.PUBLIC_HOLDINGS, pf.LOCAL_HOLDINGS
    pf.PUBLIC_HOLDINGS, pf.LOCAL_HOLDINGS = pub, loc
    try:
        views = {
            "AAA": FactorView("AAA", False, 20, MomentumMetrics(0.5), 0.8,
                              value_score=0.7, quality_score=0.6),
            "ETF": FactorView("ETF", True, 20, MomentumMetrics(0.1)),
        }
        write_all(universe, fixture_result, {}, {}, MacroOverlay(),
                  factor_views=views, data_dir=tmp_path)
        blob = (tmp_path / "portfolio.json").read_text()
        payload = _json.loads(blob)
    finally:
        pf.PUBLIC_HOLDINGS, pf.LOCAL_HOLDINGS = original_pub, original_loc

    assert payload["n_positions"] == 2
    for forbidden in ("123456.78", "99999.11", "222222.22",
                      "market_value", "cost_basis"):
        assert forbidden not in blob, f"{forbidden} leaked into the public payload"


def test_portfolio_payload_absent_without_holdings(tmp_path, fixture_result, universe):
    import json as _json
    from market_desk.build import write_all
    from market_desk import portfolio as pf

    original = pf.PUBLIC_HOLDINGS
    pf.PUBLIC_HOLDINGS = tmp_path / "nope.yml"
    try:
        write_all(universe, fixture_result, {}, {}, MacroOverlay(), data_dir=tmp_path)
    finally:
        pf.PUBLIC_HOLDINGS = original
    assert _json.loads((tmp_path / "portfolio.json").read_text()) == {"available": False}


def test_portfolio_does_not_confuse_value_with_quality(tmp_path):
    """Regression: a loop variable named `val` shadowed the value score, so
    every position's value_score was silently overwritten with its quality
    score. The two must stay distinct."""
    import yaml as _yaml
    from market_desk.factors import FactorView, MomentumMetrics
    from market_desk.portfolio import analyze, load_holdings

    (tmp_path / "h.yml").write_text(_yaml.safe_dump({
        "positions": [{"symbol": "AAA", "exposure": 1.0}],
    }))
    views = {
        "AAA": FactorView("AAA", False, 20, MomentumMetrics(0.4), 0.7,
                          value_score=0.20, quality_score=0.90),
    }
    a = analyze(load_holdings(tmp_path / "h.yml"), views, {"AAA": "Tech"})
    row = a.rows[0]
    assert row["value_score"] == pytest.approx(0.20)
    assert row["quality_score"] == pytest.approx(0.90)
    assert a.value_tilt == pytest.approx(0.20)
    assert a.quality_tilt == pytest.approx(0.90)


def test_portfolio_prefers_benchmark_scores_when_present(tmp_path):
    import yaml as _yaml
    from market_desk.factors import FactorView, MomentumMetrics
    from market_desk.portfolio import analyze, load_holdings

    (tmp_path / "h.yml").write_text(_yaml.safe_dump({
        "positions": [{"symbol": "AAA", "exposure": 1.0}],
    }))
    fv = FactorView("AAA", False, 20, MomentumMetrics(0.4), 0.10,
                    value_score=0.10, quality_score=0.10)
    fv.bench_universe_n = 503
    fv.bench_momentum_rank = 0.80
    fv.bench_value_score = 0.60
    fv.bench_quality_score = 0.70
    fv.bench_value_population = "Industrials"

    a = analyze(load_holdings(tmp_path / "h.yml"), {"AAA": fv}, {"AAA": "Industrials"})
    assert a.population == "S&P 500"
    assert a.momentum_tilt == pytest.approx(0.80), "benchmark rank must win"
    assert a.value_tilt == pytest.approx(0.60)
    assert a.rows[0]["value_peer_group"] == "Industrials"
    assert any("S&P 500" in n for n in a.notes)
