"""Factor machinery. The constructions under test are the ones the
literature specifies: 12-1 momentum that skips the reversal month,
multi-ratio value, quality composites, and the trap interactions."""
import math

import pytest

from market_desk.factors import (
    MIN_CROSS_SECTION, SESSIONS_1M, SESSIONS_12M,
    build_factor_views, momentum_metrics,
)
from market_desk.fetch import Fundamentals


def drift_series(daily_log_return, n=300, start=100.0):
    return [start * math.exp(daily_log_return * i) for i in range(n)]


# ---------------- momentum construction ----------------

def test_mom_12_1_skips_the_most_recent_month():
    # Flat for a year, then a +30% spike in the last month only. The 12-1
    # window must NOT see the spike; ret_1m must carry all of it.
    closes = [100.0] * 300
    for i in range(SESSIONS_1M):
        closes[-1 - i] = 130.0
    m = momentum_metrics(closes)
    assert m.mom_12_1 == pytest.approx(0.0)
    assert m.ret_1m == pytest.approx(0.30)


def test_mom_12_1_measures_the_formation_window_exactly():
    closes = drift_series(0.001)
    m = momentum_metrics(closes)
    assert m.mom_12_1 == pytest.approx(
        math.exp(0.001 * (SESSIONS_12M - SESSIONS_1M)) - 1)


def test_momentum_needs_enough_history():
    m = momentum_metrics([100.0] * 100)
    assert m.mom_12_1 is None
    assert m.mom_6_1 is None
    assert m.mom_3_1 is not None       # 63+1 sessions exist
    assert m.ret_1m is not None


# ---------------- cross-section fixtures ----------------

def company(symbol, sector="Technology", **kw):
    defaults = dict(quote_type="EQUITY", market_cap=1e10, trailing_pe=20.0,
                    ev_ebitda=12.0, free_cashflow=5e8,
                    return_on_equity=0.20, return_on_assets=0.10,
                    operating_margin=0.25, debt_to_equity=0.5)
    defaults.update(kw)
    return Fundamentals(symbol=symbol, sector=sector, **defaults)


@pytest.fixture
def universe():
    fundamentals = {
        # ordered cheap+good .. expensive+junk
        "CHP": company("CHP", trailing_pe=8.0, ev_ebitda=5.0, free_cashflow=1.5e9,
                       return_on_equity=0.30, return_on_assets=0.15,
                       operating_margin=0.35, debt_to_equity=0.2),
        "MID": company("MID"),
        "EXP": company("EXP", trailing_pe=45.0, ev_ebitda=30.0, free_cashflow=1e8,
                       return_on_equity=0.25, return_on_assets=0.12,
                       operating_margin=0.30, debt_to_equity=0.4),
        "TRP": company("TRP", trailing_pe=5.0, ev_ebitda=3.0, free_cashflow=2e9,
                       return_on_equity=0.02, return_on_assets=0.01,
                       operating_margin=0.02, debt_to_equity=3.0),
        "BNK": company("BNK", sector="Financial Services", ev_ebitda=None,
                       free_cashflow=None, debt_to_equity=None,
                       trailing_pe=10.0),
        "ETF": Fundamentals(symbol="ETF", quote_type="ETF"),
    }
    closes = {
        "CHP": drift_series(0.002),      # strong uptrend
        "MID": drift_series(0.0005),
        "EXP": drift_series(0.001),
        "TRP": drift_series(-0.002),     # falling knife
        "BNK": drift_series(0.0),
        "ETF": drift_series(0.0015),
    }
    return closes, fundamentals


def test_momentum_ranks_order_the_cross_section(universe):
    closes, fundamentals = universe
    views = build_factor_views(closes, fundamentals)
    assert views["CHP"].momentum_rank == pytest.approx(1.0)
    assert views["TRP"].momentum_rank == pytest.approx(0.0)
    assert 0.0 < views["MID"].momentum_rank < 1.0


def test_funds_get_raw_momentum_but_no_ranks(universe):
    closes, fundamentals = universe
    view = build_factor_views(closes, fundamentals)["ETF"]
    assert view.momentum.mom_12_1 is not None
    assert view.momentum_rank is None
    assert view.value_score is None
    assert view.quality_score is None
    assert any("do not enter the company cross-section" in n for n in view.notes)


def test_value_trap_is_cheap_plus_junk(universe):
    closes, fundamentals = universe
    views = build_factor_views(closes, fundamentals)
    trap = views["TRP"]
    assert trap.value_score is not None and trap.value_score >= 2 / 3
    assert trap.quality_score is not None and trap.quality_score <= 1 / 3
    assert trap.value_trap is True
    assert any("Value-trap" in n for n in trap.notes)
    # Cheap AND good is not a trap.
    assert views["CHP"].value_trap is False


def test_trap_flag_on_a_financial_carries_the_sector_caveat():
    fundamentals = {s: company(s) for s in ("AAA", "BBB", "CCC", "DDD")}
    fundamentals["FIN"] = company(
        "FIN", sector="Financial Services", ev_ebitda=None, free_cashflow=None,
        debt_to_equity=None, trailing_pe=4.0,
        return_on_equity=0.03, return_on_assets=0.005, operating_margin=0.05)
    closes = {s: drift_series(0.001) for s in fundamentals}
    view = build_factor_views(closes, fundamentals)["FIN"]
    assert view.value_trap is True
    assert any("Financials caveat" in n for n in view.notes)


def test_bank_value_score_skips_ev_ebitda_with_a_note(universe):
    closes, fundamentals = universe
    view = build_factor_views(closes, fundamentals)["BNK"]
    assert view.value_score is not None
    assert "ebitda_yield" not in view.value_metrics_used
    assert "earnings_yield" in view.value_metrics_used
    assert any("banks have no" in n for n in view.notes)


def test_reversal_tension_flags_a_last_month_fighting_the_signal():
    fundamentals = {s: company(s) for s in ("AAA", "BBB", "CCC", "DDD", "EEE")}
    closes = {s: drift_series(0.001) for s in fundamentals}
    # AAA: solid 12-1 uptrend, then a -10% last month.
    aaa = drift_series(0.001)
    for i in range(SESSIONS_1M):
        aaa[-1 - i] = aaa[-1 - SESSIONS_1M] * 0.90
    closes["AAA"] = aaa
    views = build_factor_views(closes, fundamentals)
    assert views["AAA"].reversal_tension is True
    assert views["BBB"].reversal_tension is False


def test_thin_cross_section_withholds_composites():
    fundamentals = {s: company(s) for s in ("AAA", "BBB")}   # < MIN_CROSS_SECTION
    closes = {s: drift_series(0.001) for s in fundamentals}
    views = build_factor_views(closes, fundamentals)
    assert len(fundamentals) < MIN_CROSS_SECTION
    for v in views.values():
        assert v.value_score is None
        assert v.quality_score is None
        assert any("too few" in n for n in v.notes)


def test_missing_metric_drops_out_never_imputed():
    fundamentals = {s: company(s) for s in ("AAA", "BBB", "CCC", "DDD")}
    fundamentals["NOF"] = company("NOF", free_cashflow=None)
    closes = {s: drift_series(0.001) for s in fundamentals}
    view = build_factor_views(closes, fundamentals)["NOF"]
    assert "fcf_yield" not in view.value_metrics_used
    assert view.value_score is not None      # still scored on what it has
