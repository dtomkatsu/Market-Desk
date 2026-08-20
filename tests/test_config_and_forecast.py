"""Config parsing and the daily→monthly fold that crosses into Census-Forecaster."""
from datetime import date

import pytest

from market_desk.config import Universe, _normalize, load_universe
from market_desk.fetch import Bar
from market_desk.forecast import _add_months, forecast_symbol, to_monthly


def write(tmp_path, text):
    p = tmp_path / "watchlist.yml"
    p.write_text(text)
    return p


def test_loads_the_repo_config():
    u = load_universe()
    assert u.symbols, "the shipped watchlist must not be empty"
    assert len(set(u.symbols)) == len(u.symbols), "symbols must be unique"


def test_symbol_normalization():
    assert _normalize(" brk.b ") == "BRK-B"   # Yahoo's share-class form
    assert _normalize("aapl") == "AAPL"


def test_first_tier_claims_a_duplicated_symbol(tmp_path):
    u = load_universe(write(tmp_path, """
tiers:
  a: {label: A, symbols: [SPY, QQQ]}
  b: {label: B, symbols: [QQQ, VTI]}
"""))
    assert u.tier_of("QQQ") == "a"
    assert u.symbols == ("SPY", "QQQ", "VTI")


def test_empty_config_raises_rather_than_tracking_nothing(tmp_path):
    with pytest.raises(ValueError):
        load_universe(write(tmp_path, "tiers: {}\n"))
    with pytest.raises(ValueError):
        load_universe(write(tmp_path, "tiers:\n  a: {label: A, symbols: []}\n"))


def test_missing_config_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_universe(tmp_path / "nope.yml")


def test_monthly_fold_takes_the_last_close_of_each_month():
    bars = [
        Bar("2025-01-05", 1, 1, 1, 10.0, 100),
        Bar("2025-01-30", 1, 1, 1, 12.0, 200),
        Bar("2025-02-27", 1, 1, 1, 15.0, 300),
    ]
    out = to_monthly(bars, drop_partial=False)
    assert out == [(2025, 1, 12.0, 300.0), (2025, 2, 15.0, 300.0)]


def test_monthly_fold_drops_the_incomplete_current_month():
    today = date.today()
    bars = [
        Bar("2024-01-31", 1, 1, 1, 10.0, 100),
        Bar(f"{today.year}-{today.month:02d}-02", 1, 1, 1, 99.0, 100),
    ]
    assert to_monthly(bars, drop_partial=True) == [(2024, 1, 10.0, 100.0)]
    assert len(to_monthly(bars, drop_partial=False)) == 2


def test_add_months_rolls_the_year():
    assert _add_months(date(2026, 8, 1), 6) == date(2027, 2, 1)
    assert _add_months(date(2026, 12, 1), 1) == date(2027, 1, 1)
    assert _add_months(date(2026, 1, 1), 12) == date(2027, 1, 1)


def test_short_history_reports_why_rather_than_crashing():
    bars = [Bar(f"2025-{m:02d}-15", 1, 1, 1, 100.0 + m, 100) for m in range(1, 7)]
    fc = forecast_symbol("TEST", bars)
    assert fc.horizons == []
    assert "need 24" in (fc.error or "")
