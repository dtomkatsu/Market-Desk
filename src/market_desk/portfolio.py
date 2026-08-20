"""Portfolio-level analysis over the positions you actually hold.

This is the factor machinery in `factors.py` applied to a real book rather
than to a watchlist. The distinction that makes it honest:

**Holdings are scored against the tracked universe, never against each
other.** A five-position book has no usable cross-section — rank five names
among themselves and someone is always in the "cheapest quintile" by
construction, which says nothing. So every position carries the percentile
it earned in the full tracked cross-section, and the portfolio number is the
weight-average of those.

What this computes:

* **Weighted factor tilt** — momentum, value and quality exposure as the
  weight-average of each holding's universe percentile, plus the same for a
  hypothetical equal-weight book so concentration effects are visible.
* **Concentration** — HHI and top-position weight, on the equity sleeve.
* **Sector concentration** — weight by sector, which is where a small book
  usually carries its real risk.
* **Cash drag** — cash is excluded from factor math but reported, because a
  book that is 18% money-market has a materially different profile than the
  equity sleeve alone suggests.
* **Flag exposure** — what fraction of the book sits in names carrying a
  value-trap or reversal-tension flag.

What it deliberately does NOT do: suggest trades, size positions, or score
the portfolio as good or bad. It describes exposure. Allocation decisions
are the reader's.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from .factors import FactorView

REPO_ROOT = Path(__file__).resolve().parents[2]
# Weights only, committed — what the published dashboard reads.
PUBLIC_HOLDINGS = REPO_ROOT / "config" / "holdings.yml"
# Dollar values and cost basis, gitignored — overlaid only for local answers.
LOCAL_HOLDINGS = REPO_ROOT / "config" / "holdings.local.yml"


@dataclass(frozen=True)
class Position:
    symbol: str
    name: Optional[str] = None
    exposure: Optional[float] = None          # fraction of the whole account
    cost_basis: Optional[float] = None
    market_value: Optional[float] = None
    unrealized_gain_loss: Optional[float] = None
    unrealized_gain_loss_pct: Optional[float] = None


@dataclass
class Holdings:
    account: Optional[str] = None
    as_of: Optional[str] = None
    positions: list[Position] = field(default_factory=list)
    cash: list[Position] = field(default_factory=list)
    totals: dict = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def available(self) -> bool:
        return self.error is None and bool(self.positions)

    @property
    def cash_weight(self) -> float:
        return sum(c.exposure or 0.0 for c in self.cash)

    @property
    def equity_weight(self) -> float:
        return sum(p.exposure or 0.0 for p in self.positions)


def _read(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception:                                 # noqa: BLE001
        return None


def load_holdings(path: Optional[Path] = None,
                  include_local: bool = True) -> Holdings:
    """Read positions: public weights, optionally overlaid with local detail.

    ``include_local=False`` is what the payload builder uses — it guarantees
    no dollar figure can reach ``docs/`` even by accident, rather than
    relying on the writer to remember to strip them.
    """
    if path is not None:
        raw = _read(Path(path))
        if raw is None:
            return Holdings(error=f"no holdings file at {Path(path).name}")
    else:
        raw = _read(PUBLIC_HOLDINGS)
        if raw is None:
            return Holdings(error="no config/holdings.yml")
        if include_local:
            local = _read(LOCAL_HOLDINGS)
            if local:
                # Local detail is keyed by symbol and merged field-wise, so a
                # position present publicly but absent locally keeps its
                # weight rather than vanishing.
                detail = {p.get("symbol"): p for p in (local.get("positions") or [])}
                for pos in raw.get("positions") or []:
                    extra = detail.get(pos.get("symbol"))
                    if extra:
                        pos.update({k: v for k, v in extra.items() if k != "symbol"})
                raw["totals"] = local.get("totals") or raw.get("totals") or {}
                raw["account"] = local.get("account") or raw.get("account")

    def parse(rows) -> list[Position]:
        out = []
        for r in rows or []:
            sym = str(r.get("symbol") or "").strip().upper()
            if not sym:
                continue
            out.append(Position(
                symbol=sym,
                name=r.get("name"),
                exposure=r.get("exposure"),
                cost_basis=r.get("cost_basis"),
                market_value=r.get("market_value"),
                unrealized_gain_loss=r.get("unrealized_gain_loss"),
                unrealized_gain_loss_pct=r.get("unrealized_gain_loss_pct"),
            ))
        return out

    return Holdings(
        account=raw.get("account"),
        as_of=str(raw.get("as_of")) if raw.get("as_of") else None,
        positions=parse(raw.get("positions")),
        cash=parse(raw.get("cash")),
        totals=raw.get("totals") or {},
    )


def _preferred(fv) -> tuple[Optional[float], Optional[float], Optional[float], bool, str]:
    """Pick the statistically meaningful scores for a holding.

    Benchmark ranks win when present: a percentile against ~500 names with
    sector-relative value and quality is a far better measurement than one
    against a couple of dozen watchlist names. The watchlist scores stay in
    the payload for continuity with recorded history, but the portfolio
    tilt should describe the better measurement.
    """
    if fv.bench_universe_n is not None:
        return (fv.bench_momentum_rank, fv.bench_value_score,
                fv.bench_quality_score, fv.bench_value_trap, "S&P 500")
    return (fv.momentum_rank, fv.value_score, fv.quality_score,
            fv.value_trap, "tracked universe")


@dataclass
class PortfolioAnalysis:
    as_of: Optional[str] = None
    account: Optional[str] = None
    n_positions: int = 0
    cash_weight: float = 0.0
    equity_weight: float = 0.0

    # Weight-average of each holding's percentile in the tracked cross-section.
    momentum_tilt: Optional[float] = None
    value_tilt: Optional[float] = None
    quality_tilt: Optional[float] = None

    # Same, equal-weighted — the gap shows what concentration is doing.
    momentum_tilt_equal: Optional[float] = None
    value_tilt_equal: Optional[float] = None
    quality_tilt_equal: Optional[float] = None

    hhi: Optional[float] = None                 # 0..1 on the equity sleeve
    top_weight: Optional[float] = None
    effective_positions: Optional[float] = None  # 1/HHI

    sector_weights: dict[str, float] = field(default_factory=dict)
    trap_weight: float = 0.0
    reversal_weight: float = 0.0
    unscored_weight: float = 0.0                # held but not in the universe

    rows: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    population: str = "tracked universe"


def analyze(holdings: Holdings,
            factor_views: dict[str, FactorView],
            sectors: Optional[dict[str, str]] = None) -> PortfolioAnalysis:
    """Portfolio exposure from positions + the universe-wide factor views."""
    sectors = sectors or {}
    out = PortfolioAnalysis(
        as_of=holdings.as_of,
        account=holdings.account,
        n_positions=len(holdings.positions),
        cash_weight=holdings.cash_weight,
        equity_weight=holdings.equity_weight,
    )
    if not holdings.positions:
        out.notes.append("No positions to analyze.")
        return out

    equity_total = holdings.equity_weight or 1.0

    # Weights are renormalized onto the equity sleeve: a factor tilt is a
    # statement about the stocks, and leaving cash in the denominator would
    # drag every tilt toward zero for a reason that has nothing to do with
    # the stocks. Cash is reported separately instead.
    scored_w = 0.0
    acc = {"momentum": 0.0, "value": 0.0, "quality": 0.0}
    acc_eq = {"momentum": 0.0, "value": 0.0, "quality": 0.0}
    counted = {"momentum": 0.0, "value": 0.0, "quality": 0.0}
    counted_n = {"momentum": 0, "value": 0, "quality": 0}

    for pos in holdings.positions:
        w = (pos.exposure or 0.0) / equity_total
        fv = factor_views.get(pos.symbol)
        sector = sectors.get(pos.symbol) or "Unclassified"
        out.sector_weights[sector] = out.sector_weights.get(sector, 0.0) + w

        row = {
            "symbol": pos.symbol,
            "name": pos.name,
            "weight": w,
            "account_weight": pos.exposure,
            "sector": sector,
            "market_value": pos.market_value,
            "cost_basis": pos.cost_basis,
            "unrealized_gain_loss_pct": pos.unrealized_gain_loss_pct,
            "momentum_rank": None, "value_score": None, "quality_score": None,
            "value_trap": False, "reversal_tension": False, "scored": False,
        }

        if fv is None or fv.is_fund:
            out.unscored_weight += w
            row["note"] = "not in the tracked cross-section — no factor ranks"
            out.rows.append(row)
            continue

        row["scored"] = True
        scored_w += w
        mom, val, qual, trap, population = _preferred(fv)
        out.population = population
        pairs = (("momentum", mom), ("value", val), ("quality", qual))
        # NOTE the loop variable name: reusing `val` here shadows the value
        # score unpacked above, and every position's value_score silently
        # became its quality_score.
        for key, score in pairs:
            if score is None:
                continue
            acc[key] += w * score
            acc_eq[key] += score
            counted[key] += w
            counted_n[key] += 1
        row["momentum_rank"] = mom
        row["value_score"] = val
        row["quality_score"] = qual
        row["value_trap"] = trap
        row["reversal_tension"] = fv.reversal_tension
        row["population"] = population
        row["value_peer_group"] = fv.bench_value_population
        if trap:
            out.trap_weight += w
        if fv.reversal_tension:
            out.reversal_weight += w
        out.rows.append(row)

    # Each tilt is normalized by the weight that actually carried that metric,
    # so a holding missing a value score does not silently drag the value tilt
    # toward zero.
    for key, attr in (("momentum", "momentum_tilt"),
                      ("value", "value_tilt"),
                      ("quality", "quality_tilt")):
        if counted[key] > 0:
            setattr(out, attr, acc[key] / counted[key])
        if counted_n[key] > 0:
            setattr(out, attr + "_equal", acc_eq[key] / counted_n[key])

    weights = [(p.exposure or 0.0) / equity_total for p in holdings.positions]
    out.hhi = sum(w * w for w in weights)
    out.top_weight = max(weights) if weights else None
    out.effective_positions = (1.0 / out.hhi) if out.hhi else None

    out.rows.sort(key=lambda r: -(r["weight"] or 0))
    out.notes.extend(_notes(out, holdings))
    return out


def _notes(a: PortfolioAnalysis, holdings: Holdings) -> list[str]:
    notes: list[str] = []

    if a.effective_positions is not None and a.effective_positions < 5:
        notes.append(
            f"Concentrated: {a.n_positions} positions, but an effective count of "
            f"{a.effective_positions:.1f} once weights are accounted for "
            f"(largest is {a.top_weight:.0%} of the equity sleeve). "
            "Single-name news dominates this book."
        )
    if a.cash_weight > 0.10:
        notes.append(
            f"Cash is {a.cash_weight:.0%} of the account. Factor tilts below "
            "describe the equity sleeve only — the whole-account exposure to "
            "each factor is that much lower."
        )
    top_sector = max(a.sector_weights.items(), key=lambda kv: kv[1], default=None)
    if top_sector and top_sector[1] > 0.35:
        notes.append(
            f"{top_sector[0]} is {top_sector[1]:.0%} of the equity sleeve — "
            "sector risk, not stock risk, is the dominant exposure here."
        )
    if a.trap_weight > 0:
        notes.append(
            f"{a.trap_weight:.0%} of the equity sleeve sits in names carrying a "
            "value-trap flag (cheap on multiples, bottom-third on quality). "
            "The flag is a caution, not a verdict."
        )
    if a.unscored_weight > 0.05:
        notes.append(
            f"{a.unscored_weight:.0%} of the sleeve has no factor ranks — those "
            "names are held but not in the tracked cross-section, or are funds."
        )
    if a.momentum_tilt is not None and a.momentum_tilt_equal is not None:
        gap = a.momentum_tilt - a.momentum_tilt_equal
        if abs(gap) > 0.12:
            direction = "toward" if gap > 0 else "away from"
            notes.append(
                f"Position sizing tilts the book {direction} momentum relative "
                f"to holding the same names equally weighted "
                f"({a.momentum_tilt:.2f} vs {a.momentum_tilt_equal:.2f})."
            )

    if a.population == "S&P 500":
        notes.append(
            "Percentiles are measured against the S&P 500 cross-section — "
            "momentum against the whole index, value and quality within each "
            "holding's own sector, so a utility is compared with utilities "
            "rather than with software. Descriptive only — this is not advice, "
            "and nothing here is a recommendation to change any position."
        )
    else:
        notes.append(
            "Percentiles are cross-sectional within the tracked universe only "
            "— a couple of dozen names, not market-wide factor exposures. "
            "Descriptive only — this is not advice, and nothing here is a "
            "recommendation to change any position."
        )
    return notes
