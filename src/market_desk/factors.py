"""Cross-sectional factor analysis: momentum, value, quality.

Each factor is implemented the way the empirical literature says it
works, including the documented traps:

* **Momentum** — stocks that beat their peers over the past 3-12 months
  tend to keep outperforming over the next 1-3. The standard academic
  construction is **12-1 momentum**: the return from twelve months ago
  to ONE month ago, skipping the most recent month, because short-horizon
  (days-to-weeks) returns mean-revert. The skipped month's return is
  reported separately as ``ret_1m`` so the reversal window is visible
  rather than silently mixed into the signal.
* **Value** — cheap-versus-fundamentals outperforms over long horizons,
  but a low P/E alone walks straight into value traps (cheap because
  dying). The empirical fix is combining multiple ratios, weighted
  toward EV/EBITDA and free-cash-flow yield rather than P/E alone. The
  composite here rank-averages EBITDA yield (1 / EV-EBITDA), FCF yield,
  and earnings yield — whichever of the three a company actually
  reports.
* **Quality** — profitable, low-debt, stable businesses outperform junk,
  especially in downturns. Screened here on ROE, ROA (the closest thing
  to ROIC Yahoo's snapshot provides — labeled as the proxy it is),
  operating margin, and leverage (inverse debt/equity).

The **value-trap flag** is the interaction the literature warns about:
a name in the cheap third on value while in the bottom third on quality
gets flagged rather than celebrated.

Honesty constraints, enforced in code and repeated in every output:

* Ranks are cross-sectional **within the tracked universe** — a couple
  of dozen names, not the whole market the academic evidence ranks. A
  90th-percentile momentum here means "top of this watchlist", nothing
  more. Outputs carry ``universe_n`` so the reader can see how thin the
  comparison is, and composites are suppressed entirely below
  ``MIN_CROSS_SECTION``.
* Funds are excluded from every cross-section (their raw momentum is
  still reported — price momentum is well-defined for an ETF — but they
  do not rank against companies).
* Banks legitimately report no EBITDA, FCF, or debt/equity; value
  studies exclude financials from EV/EBITDA sorts for this reason. A
  missing metric simply drops out of that name's composite; it is never
  imputed.
* None of this is a trading signal. These are descriptive screens of
  published data, and every consumer of this module repeats that.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from .fetch import Fundamentals

# Trading-session windows for the momentum construction.
SESSIONS_1M = 21
SESSIONS_3M = 63
SESSIONS_6M = 126
SESSIONS_12M = 252

# Below this many companies a percentile is closer to a coin flip than a
# cross-section; composites are withheld rather than reported thin.
MIN_CROSS_SECTION = 5


# ---------------------------------------------------------------------------
# Momentum (price-only; computable for every symbol, funds included)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MomentumMetrics:
    """Formation-period returns, as fractions."""
    mom_12_1: Optional[float] = None   # t-12m .. t-1m — the academic standard
    mom_6_1: Optional[float] = None    # t-6m  .. t-1m
    mom_3_1: Optional[float] = None    # t-3m  .. t-1m
    ret_1m: Optional[float] = None     # the skipped month — the reversal window


def momentum_metrics(closes: Sequence[float]) -> MomentumMetrics:
    """Formation returns from a daily close series.

    Every formation window ends one month ago (``closes[-1-21]``), never
    at the last bar: including the most recent month is the documented
    way to contaminate a momentum signal with short-term reversal.
    """
    def window(start_sessions: int) -> Optional[float]:
        # return over [-start_sessions, -SESSIONS_1M]
        if len(closes) <= start_sessions:
            return None
        start = closes[-1 - start_sessions]
        end = closes[-1 - SESSIONS_1M]
        if start <= 0:
            return None
        return end / start - 1.0

    ret_1m = None
    if len(closes) > SESSIONS_1M and closes[-1 - SESSIONS_1M] > 0:
        ret_1m = closes[-1] / closes[-1 - SESSIONS_1M] - 1.0

    return MomentumMetrics(
        mom_12_1=window(SESSIONS_12M),
        mom_6_1=window(SESSIONS_6M),
        mom_3_1=window(SESSIONS_3M),
        ret_1m=ret_1m,
    )


# ---------------------------------------------------------------------------
# Per-symbol factor view
# ---------------------------------------------------------------------------

@dataclass
class FactorView:
    symbol: str
    is_fund: bool
    universe_n: int                       # companies in the cross-section

    momentum: MomentumMetrics = field(default_factory=MomentumMetrics)
    momentum_rank: Optional[float] = None       # pct rank of mom_12_1, companies only

    # value inputs (higher yield = cheaper, so all three point the same way)
    ev_ebitda: Optional[float] = None
    ebitda_yield: Optional[float] = None        # 1 / EV-EBITDA
    fcf_yield: Optional[float] = None           # FCF / market cap
    earnings_yield: Optional[float] = None      # 1 / trailing P/E
    value_score: Optional[float] = None         # mean of available yield ranks; 1 = cheapest
    value_metrics_used: tuple[str, ...] = ()

    # quality inputs
    roe: Optional[float] = None
    roa: Optional[float] = None                 # the ROIC proxy
    operating_margin: Optional[float] = None
    debt_to_equity: Optional[float] = None
    quality_score: Optional[float] = None       # mean of available ranks; 1 = highest quality
    quality_metrics_used: tuple[str, ...] = ()

    value_trap: bool = False
    reversal_tension: bool = False              # last month fought the 12-1 signal
    notes: tuple[str, ...] = ()


def _pct_rank(value: Optional[float], population: list[float]) -> Optional[float]:
    """Fraction of the population strictly below ``value``, midpoint-adjusted.

    Midpoint handling ((below + 0.5*ties-excluding-self) style is overkill
    at this N; strictly-below over n-1 peers keeps 0 and 1 reachable and
    is monotone, which is all a composite needs.)
    """
    if value is None:
        return None
    others = [p for p in population if p is not None]
    if len(others) < 2:
        return None
    below = sum(1 for p in others if p < value)
    ties = sum(1 for p in others if p == value) - 1   # exclude self
    return (below + 0.5 * max(ties, 0)) / (len(others) - 1)


def _fcf_yield(f: Fundamentals) -> Optional[float]:
    if f.free_cashflow is None or not f.market_cap:
        return None
    return f.free_cashflow / f.market_cap


def _ebitda_yield(f: Fundamentals) -> Optional[float]:
    if f.ev_ebitda is None or f.ev_ebitda <= 0:
        return None
    return 1.0 / f.ev_ebitda


def _earnings_yield(f: Fundamentals) -> Optional[float]:
    if f.trailing_pe is None or f.trailing_pe <= 0:
        return None
    return 1.0 / f.trailing_pe


def build_factor_views(
    closes_by_symbol: dict[str, Sequence[float]],
    fundamentals: dict[str, Fundamentals],
) -> dict[str, FactorView]:
    """Factor views for every symbol, ranked within the company cross-section."""
    companies = {s: f for s, f in fundamentals.items() if not f.is_fund}
    n = len(companies)
    thin = n < MIN_CROSS_SECTION

    # ---- population vectors (companies only) ----
    momentum_all = {s: momentum_metrics(closes_by_symbol.get(s, []))
                    for s in closes_by_symbol}
    mom_pop = [momentum_all[s].mom_12_1 for s in companies if s in momentum_all]

    value_inputs = {
        s: {
            "ebitda_yield": _ebitda_yield(f),
            "fcf_yield": _fcf_yield(f),
            "earnings_yield": _earnings_yield(f),
        }
        for s, f in companies.items()
    }
    value_pops = {
        metric: [value_inputs[s][metric] for s in companies]
        for metric in ("ebitda_yield", "fcf_yield", "earnings_yield")
    }

    quality_inputs = {
        s: {
            "roe": f.return_on_equity,
            "roa": f.return_on_assets,
            "operating_margin": f.operating_margin,
            # leverage enters inverted so that "higher rank = better" holds
            # for every quality metric uniformly
            "low_leverage": (-f.debt_to_equity if f.debt_to_equity is not None else None),
        }
        for s, f in companies.items()
    }
    quality_pops = {
        metric: [quality_inputs[s][metric] for s in companies]
        for metric in ("roe", "roa", "operating_margin", "low_leverage")
    }

    def composite(inputs: dict[str, Optional[float]],
                  pops: dict[str, list[float]]) -> tuple[Optional[float], tuple[str, ...]]:
        if thin:
            return None, ()
        ranks, used = [], []
        for metric, value in inputs.items():
            r = _pct_rank(value, pops[metric])
            if r is not None:
                ranks.append(r)
                used.append(metric)
        if not ranks:
            return None, ()
        return sum(ranks) / len(ranks), tuple(used)

    out: dict[str, FactorView] = {}
    for symbol in closes_by_symbol:
        f = fundamentals.get(symbol)
        is_fund = f.is_fund if f else True
        mom = momentum_all[symbol]
        view = FactorView(symbol=symbol, is_fund=is_fund, universe_n=n, momentum=mom)

        notes: list[str] = []
        if thin:
            notes.append(
                f"Only {n} companies in the tracked universe — too few for a "
                f"meaningful cross-section (minimum {MIN_CROSS_SECTION}); "
                "composites withheld."
            )

        if not is_fund and not thin:
            view.momentum_rank = _pct_rank(mom.mom_12_1, mom_pop)

            view.ebitda_yield = value_inputs[symbol]["ebitda_yield"]
            view.fcf_yield = value_inputs[symbol]["fcf_yield"]
            view.earnings_yield = value_inputs[symbol]["earnings_yield"]
            view.ev_ebitda = f.ev_ebitda
            view.value_score, view.value_metrics_used = composite(
                value_inputs[symbol], value_pops)

            view.roe = f.return_on_equity
            view.roa = f.return_on_assets
            view.operating_margin = f.operating_margin
            view.debt_to_equity = f.debt_to_equity
            view.quality_score, view.quality_metrics_used = composite(
                quality_inputs[symbol], quality_pops)

            # -- the documented interactions --
            if (view.value_score is not None and view.quality_score is not None
                    and view.value_score >= 2 / 3 and view.quality_score <= 1 / 3):
                view.value_trap = True
                notes.append(
                    "Value-trap flag: screens cheap (top third on value) while "
                    "sitting in the bottom third on quality — the classic "
                    "cheap-because-dying profile. Cheapness alone is not the "
                    "signal here."
                )
                if (f.sector or "") == "Financial Services":
                    notes.append(
                        "Financials caveat on that flag: banks run structurally "
                        "low ROA and high leverage by business model, so a "
                        "cross-sector quality rank penalizes them mechanically. "
                        "Part of this flag is that artifact, not a verdict on "
                        "the bank."
                    )
            if (mom.mom_12_1 is not None and mom.ret_1m is not None
                    and abs(mom.ret_1m) > 0.05
                    and (mom.ret_1m > 0) != (mom.mom_12_1 > 0)):
                view.reversal_tension = True
                notes.append(
                    "The most recent month moved against the 12-1 formation "
                    "return. Short-horizon returns mean-revert, which is why "
                    "the formation window skips them — read the 12-1 number, "
                    "not the last month."
                )
            if view.value_metrics_used and "ebitda_yield" not in view.value_metrics_used:
                if (f.sector or "") == "Financial Services":
                    notes.append(
                        "No EV/EBITDA — normal for a financial (banks have no "
                        "meaningful EBITDA), which is why value studies exclude "
                        "financials from EV/EBITDA sorts. Value score uses "
                        f"{', '.join(view.value_metrics_used)} only."
                    )
                else:
                    notes.append(
                        "No EV/EBITDA reported; value score uses "
                        f"{', '.join(view.value_metrics_used)} only."
                    )
        elif is_fund:
            notes.append(
                "Fund/ETF: raw price momentum is reported, but funds do not "
                "enter the company cross-section — value and quality are "
                "single-company constructs."
            )

        view.notes = tuple(notes)
        out[symbol] = view
    return out


UNIVERSE_CAVEAT = (
    "Factor ranks are cross-sectional within this tracked universe only — a "
    "couple of dozen names, not the market-wide cross-section the academic "
    "factor evidence is built on. Composite scores are rank averages in [0,1]. "
    "Descriptive screens, not trading signals."
)
