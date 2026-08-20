"""The S&P 500 benchmark cross-section.

The behaviours that matter: sector-relative ranking actually ranks within
sector, thin sectors fall back and say so, and the snapshot cache prevents a
throttled fetch from silently reverting every percentile to a
watchlist-relative one.
"""
import json

import pytest

from market_desk.benchmark import (
    MIN_SECTOR_MEMBERS, QUALITY_ATTRS, VALUE_ATTRS, BenchmarkMember,
    BenchmarkPopulation, BenchmarkRanker, decile_breakpoints, get_population,
    load_constituents, load_snapshot, save_population,
)


def population(sector_counts, base=0.0):
    """Build a population with known, monotone values per sector."""
    members = {}
    i = 0
    for sector, n in sector_counts.items():
        for k in range(n):
            sym = f"{sector[:3].upper()}{k:03d}"
            members[sym] = BenchmarkMember(
                symbol=sym, sector=sector,
                momentum=base + i * 0.001,
                earnings_yield=0.01 + k * 0.001,
                fcf_yield=0.01 + k * 0.001,
                ebitda_yield=0.01 + k * 0.001,
                roe=0.05 + k * 0.002, roa=0.02 + k * 0.001,
                operating_margin=0.05 + k * 0.002,
                low_leverage=-(1.0 - k * 0.01),
            )
            i += 1
    return BenchmarkPopulation(members=members, as_of="2026-08-19")


# ---------------- pinned constituent list ----------------

def test_pinned_list_is_present_and_balanced():
    rows = load_constituents()
    assert len(rows) >= 480, "the pinned S&P list should be roughly 500 names"
    from collections import Counter
    sizes = Counter(r["gics_sector"] for r in rows)
    assert len(sizes) >= 10, "every GICS sector should be represented"
    # The design depends on every sector being deep enough to rank within.
    assert min(sizes.values()) >= 15, f"thinnest sector: {sizes.most_common()[-1]}"


def test_symbols_are_in_yahoo_form():
    rows = load_constituents()
    assert all("." not in r["symbol"] for r in rows), "BRK.B must be BRK-B"


# ---------------- ranking ----------------

def test_sector_ranking_uses_the_sector_not_the_index():
    """A name mid-pack in its sector but extreme index-wide ranks mid-pack."""
    pop = population({"Utilities": 30, "Technology": 60})
    r = BenchmarkRanker(pop)
    # Utilities all have low earnings_yield indices 0..29; take the middle one.
    mid = pop.members["UTI015"]
    sector_rank = r.rank_sector(mid.earnings_yield, "earnings_yield", "Utilities")
    assert sector_rank.population == "Utilities"
    assert sector_rank.n == 30
    assert 0.3 < sector_rank.percentile < 0.7


def test_thin_sector_falls_back_and_names_the_fallback():
    pop = population({"Technology": 60, "Tiny": 3})
    r = BenchmarkRanker(pop)
    tiny = pop.members["TIN001"]
    rank = r.rank_sector(tiny.earnings_yield, "earnings_yield", "Tiny")
    assert rank.population == "S&P 500", "a 3-name sector must not be a peer group"
    assert rank.n > 3


def test_momentum_ranks_against_the_whole_index():
    pop = population({"Utilities": 30, "Technology": 60})
    r = BenchmarkRanker(pop)
    top = max(pop.members.values(), key=lambda m: m.momentum)
    rank = r.rank_universe(top.momentum, "momentum")
    assert rank.population == "S&P 500"
    assert rank.percentile == pytest.approx(1.0)
    assert rank.n == 90


def test_composite_averages_available_metrics_only():
    pop = population({"Technology": 60})
    r = BenchmarkRanker(pop)
    m = pop.members["TEC030"]
    full, used, label, n = r.composite_sector(
        {a: getattr(m, a) for a in VALUE_ATTRS}, "Technology")
    partial, used2, _, _ = r.composite_sector(
        {"earnings_yield": m.earnings_yield, "fcf_yield": None,
         "ebitda_yield": None}, "Technology")
    assert len(used) == 3 and len(used2) == 1
    assert label == "Technology" and n == 60
    assert partial is not None            # missing metrics drop, never impute


def test_composite_is_none_without_any_metric():
    pop = population({"Technology": 60})
    r = BenchmarkRanker(pop)
    score, used, _, _ = r.composite_sector(
        {a: None for a in VALUE_ATTRS}, "Technology")
    assert score is None and used == ()


# ---------------- snapshot cache ----------------

def test_snapshot_roundtrip(tmp_path):
    pop = population({"Technology": 20})
    path = tmp_path / "snap.json"
    save_population(pop, path)
    back = load_snapshot(path)
    assert back is not None
    assert len(back.members) == 20
    assert back.as_of == "2026-08-19"
    assert back.members["TEC000"].sector == "Technology"


def test_missing_snapshot_returns_none(tmp_path):
    assert load_snapshot(tmp_path / "absent.json") is None


def test_corrupt_snapshot_returns_none(tmp_path):
    path = tmp_path / "snap.json"
    path.write_text("{ not json")
    assert load_snapshot(path) is None


def test_population_availability_floor():
    assert population({"Technology": 20}).available is False
    assert population({"Technology": 150}).available is True


# ---------------- breakpoints ----------------

def test_deciles_are_monotone_and_compact():
    pop = population({"Technology": 100})
    bp = decile_breakpoints(pop)
    assert "momentum" in bp
    for attr, cuts in bp.items():
        assert len(cuts) == 9, f"{attr} should give 9 decile cuts"
        assert cuts == sorted(cuts), f"{attr} deciles must be monotone"


def test_deciles_skip_sparse_factors():
    pop = population({"Technology": 10})     # below the 20-value floor
    assert decile_breakpoints(pop) == {}
