"""Valuation ranking. The rule under test throughout: a multiple is only
reported against a named peer group, and funds never enter one."""
import pytest

from market_desk.fetch import Fundamentals, _positive
from market_desk.valuation import (
    build_peer_groups, build_valuations, describe_rank,
)


def company(symbol, sector, pe=None, fwd=None, pb=None, eps=1.0):
    return Fundamentals(symbol=symbol, sector=sector, quote_type="EQUITY",
                        trailing_pe=pe, forward_pe=fwd, price_to_book=pb,
                        trailing_eps=eps)


@pytest.fixture
def tech_and_banks():
    return {
        "AAA": company("AAA", "Technology", pe=20, fwd=18, pb=5),
        "BBB": company("BBB", "Technology", pe=30, fwd=28, pb=8),
        "CCC": company("CCC", "Technology", pe=40, fwd=38, pb=10),
        "DDD": company("DDD", "Technology", pe=50, fwd=48, pb=12),
        "BNK": company("BNK", "Financial Services", pe=10, fwd=11, pb=1.2),
        "ETF": Fundamentals(symbol="ETF", quote_type="ETF", trailing_pe=22),
    }


def test_sector_becomes_a_peer_group_once_it_has_enough_members(tech_and_banks):
    groups = build_peer_groups(tech_and_banks, min_peers=4)
    assert groups["AAA"][0] == "Technology"
    assert len(groups["AAA"][1]) == 4


def test_thin_sector_falls_back_to_the_whole_universe(tech_and_banks):
    groups = build_peer_groups(tech_and_banks, min_peers=4)
    # One bank is not a sector comparison; the label must say so rather than
    # silently ranking it against three names it has nothing to do with.
    assert groups["BNK"][0] == "tracked universe"


def test_funds_are_excluded_from_every_peer_group(tech_and_banks):
    groups = build_peer_groups(tech_and_banks, min_peers=4)
    assert groups["ETF"] == ("fund", [])
    for symbol, (_, peers) in groups.items():
        assert "ETF" not in peers


def test_percentile_orders_cheapest_to_priciest(tech_and_banks):
    views = build_valuations(tech_and_banks, min_peers=4)
    assert views["AAA"].pe_rank.percentile < views["DDD"].pe_rank.percentile
    assert views["DDD"].pe_rank.percentile == pytest.approx(1.0)
    assert views["AAA"].pe_rank.median == pytest.approx(35.0)


def test_fund_gets_a_note_and_no_rank(tech_and_banks):
    view = build_valuations(tech_and_banks, min_peers=4)["ETF"]
    assert view.pe_rank is None
    assert any("Fund or ETF" in n for n in view.notes)


def test_loss_maker_explains_its_missing_pe():
    data = {"LOSS": Fundamentals(symbol="LOSS", sector="Technology",
                                 quote_type="EQUITY", trailing_pe=None,
                                 trailing_eps=-3.2)}
    view = build_valuations(data)["LOSS"]
    assert view.trailing_pe is None
    assert any("not profitable" in n for n in view.notes)


def test_negative_multiples_are_dropped_not_ranked():
    # A -12 P/E must never sort as "cheapest". _positive is the gate.
    assert _positive(-12.0) is None
    assert _positive(0.0) is None
    assert _positive(12.0) == 12.0


def test_earnings_yield_is_the_reciprocal(tech_and_banks):
    view = build_valuations(tech_and_banks, min_peers=4)["AAA"]
    assert view.earnings_yield == pytest.approx(1 / 20)


def test_forward_below_trailing_is_flagged_as_growth():
    data = {
        "GRW": company("GRW", "Technology", pe=50, fwd=25),
        "FLT": company("FLT", "Technology", pe=20, fwd=30),
    }
    views = build_valuations(data)
    assert any("grow into the multiple" in n for n in views["GRW"].notes)
    assert any("earnings to fall" in n for n in views["FLT"].notes)


def test_describe_rank_names_the_peer_group(tech_and_banks):
    views = build_valuations(tech_and_banks, min_peers=4)
    text = describe_rank(views["AAA"].pe_rank)
    assert "Technology" in text and "n=4" in text
    assert describe_rank(None) == "No comparable P/E."


def test_rank_needs_at_least_two_comparables():
    data = {"ONE": company("ONE", "Technology", pe=15)}
    assert build_valuations(data)["ONE"].pe_rank is None
