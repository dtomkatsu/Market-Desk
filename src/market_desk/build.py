"""Assemble the dashboard payloads written to ``docs/data/``.

Layout, and why it is split this way:

* ``meta.json``       — one small file: fetch date, provenance, failures,
                        the Census-Forecaster pin. Cheap to poll.
* ``index.json``      — one summary row per symbol. This is what the
                        table and every screen/sort reads, so it must
                        stay small enough to load instantly.
* ``symbols/<SYM>.json`` — full daily series, indicators, volume
                        analytics, valuation detail, forecast, macro
                        signals. Fetched only when a symbol is opened.

The split is the difference between a dashboard that paints in a blink
and one that downloads several megabytes of candles nobody looked at.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import __version__
from .config import Universe
from .factors import UNIVERSE_CAVEAT, FactorView
from .fetch import FetchResult
from .forecast import SymbolForecast
from .indicators import (
    annualized_vol, atr, bollinger, ema, macd, max_drawdown,
    pct_change, range_position, rsi, sma,
)
from .macro import MacroOverlay, summarize_ticker
from .portfolio import analyze as analyze_portfolio, load_holdings
from .valuation import ValuationView, describe_rank
from .volume import (
    accumulation_distribution, dollar_volume, obv, price_volume_divergence,
    relative_volume, rolling_vwap, up_down_volume_ratio, volume_trend,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "docs" / "data"
NOTES_DIR = REPO_ROOT / "analysis"

# Trading-session counts for the standard lookbacks.
SESSIONS = {"1w": 5, "1m": 21, "3m": 63, "6m": 126, "1y": 252}


def _round(value: Optional[float], places: int = 4) -> Optional[float]:
    """Round for the wire. None stays None — never coerced to 0."""
    if value is None:
        return None
    try:
        return round(float(value), places)
    except (TypeError, ValueError):
        return None


def _series(values, places: int = 4) -> list:
    return [_round(v, places) for v in values]


def build_symbol_payload(symbol: str, result: FetchResult,
                         valuation: Optional[ValuationView],
                         forecast: Optional[SymbolForecast],
                         overlay: MacroOverlay,
                         universe: Universe,
                         factors: Optional[FactorView] = None) -> dict:
    """Everything the detail view needs for one symbol."""
    bars = result.bars[symbol]
    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    volumes = [b.volume for b in bars]
    dates = [b.date for b in bars]

    macd_line, macd_signal, macd_hist = macd(closes)
    bb_lower, bb_mid, bb_upper = bollinger(closes)
    rvol = relative_volume(volumes, universe.settings.rvol_window)
    divergence = price_volume_divergence(closes, volumes)
    fundamentals = result.fundamentals.get(symbol)

    payload = {
        "symbol": symbol,
        "name": fundamentals.name if fundamentals else None,
        "tier": universe.tier_of(symbol),
        "fetch_date": result.fetch_date,
        "candles": [
            {"t": d, "o": _round(o, 4), "h": _round(h, 4),
             "l": _round(l, 4), "c": _round(c, 4), "v": _round(v, 0)}
            for d, o, h, l, c, v in zip(
                dates, [b.open for b in bars], highs, lows, closes, volumes)
        ],
        "indicators": {
            "sma20": _series(sma(closes, 20)),
            "sma50": _series(sma(closes, 50)),
            "sma200": _series(sma(closes, 200)),
            "ema12": _series(ema(closes, 12)),
            "rsi14": _series(rsi(closes), 2),
            "macd": _series(macd_line),
            "macd_signal": _series(macd_signal),
            "macd_hist": _series(macd_hist),
            "bb_lower": _series(bb_lower),
            "bb_mid": _series(bb_mid),
            "bb_upper": _series(bb_upper),
            "atr14": _series(atr(highs, lows, closes), 4),
        },
        "volume_analytics": {
            "rvol": _series(rvol, 3),
            "obv": _series(obv(closes, volumes), 0),
            "vwap20": _series(rolling_vwap(highs, lows, closes, volumes), 4),
            "ad_line": _series(accumulation_distribution(highs, lows, closes, volumes), 0),
            "dollar_volume": _series(dollar_volume(closes, volumes), 0),
            "up_down_ratio": _round(up_down_volume_ratio(closes, volumes), 3),
            "volume_trend": _round(volume_trend(volumes), 3),
            "divergence": {
                "verdict": divergence.verdict,
                "detail": divergence.detail,
                "price_change": _round(divergence.price_change),
                "volume_ratio": _round(divergence.volume_ratio, 3),
            },
        },
        "fundamentals": (
            {k: _round(v, 6) if isinstance(v, (int, float)) else v
             for k, v in asdict(fundamentals).items()}
            if fundamentals else None
        ),
        "valuation": None,
        "forecast": None,
        "macro_signals": summarize_ticker(overlay, symbol),
        "factors": None,
    }

    if factors:
        m = factors.momentum
        payload["factors"] = {
            "is_fund": factors.is_fund,
            "universe_n": factors.universe_n,
            "momentum": {
                "mom_12_1": _round(m.mom_12_1, 5),
                "mom_6_1": _round(m.mom_6_1, 5),
                "mom_3_1": _round(m.mom_3_1, 5),
                "ret_1m": _round(m.ret_1m, 5),
                "rank": _round(factors.momentum_rank, 3),
            },
            "value": {
                "ev_ebitda": _round(factors.ev_ebitda, 3),
                "ebitda_yield": _round(factors.ebitda_yield, 5),
                "fcf_yield": _round(factors.fcf_yield, 5),
                "earnings_yield": _round(factors.earnings_yield, 5),
                "score": _round(factors.value_score, 3),
                "metrics_used": list(factors.value_metrics_used),
                "low_confidence": factors.value_low_confidence,
            },
            "quality": {
                "roe": _round(factors.roe, 5),
                "roa": _round(factors.roa, 5),
                "operating_margin": _round(factors.operating_margin, 5),
                "debt_to_equity": _round(factors.debt_to_equity, 4),
                "score": _round(factors.quality_score, 3),
                "metrics_used": list(factors.quality_metrics_used),
                "low_confidence": factors.quality_low_confidence,
            },
            "value_trap": factors.value_trap,
            "reversal_tension": factors.reversal_tension,
            "notes": list(factors.notes),
            "caveat": UNIVERSE_CAVEAT,
        }

    if valuation:
        payload["valuation"] = {
            "peer_group": valuation.peer_group,
            "peer_count": valuation.peer_count,
            "trailing_pe": _round(valuation.trailing_pe, 3),
            "forward_pe": _round(valuation.forward_pe, 3),
            "earnings_yield": _round(valuation.earnings_yield, 5),
            "pe_vs_forward": _round(valuation.pe_vs_forward, 3),
            "peg_ratio": _round(valuation.peg_ratio, 3),
            "notes": list(valuation.notes),
            "ranks": {
                name: (None if rank is None else {
                    "value": _round(rank.value, 3),
                    "percentile": _round(rank.percentile, 3),
                    "peer_group": rank.peer_group,
                    "peer_count": rank.peer_count,
                    "median": _round(rank.median, 3),
                })
                for name, rank in (
                    ("trailing_pe", valuation.pe_rank),
                    ("forward_pe", valuation.forward_pe_rank),
                    ("price_to_book", valuation.pb_rank),
                    ("price_to_sales", valuation.ps_rank),
                )
            },
            "summary": describe_rank(valuation.pe_rank),
        }

    if forecast:
        payload["forecast"] = {
            "calibrated": forecast.calibrated,
            "band_multiplier": _round(forecast.band_multiplier, 3),
            "monthly_vol": _round(forecast.monthly_vol, 5),
            "months_used": forecast.months_used,
            "error": forecast.error,
            "horizons": [
                {"months": h.months, "target_date": h.target_date,
                 "value": _round(h.value, 3), "lo90": _round(h.lo90, 3),
                 "hi90": _round(h.hi90, 3)}
                for h in forecast.horizons
            ],
        }

    return payload


def build_index_row(symbol: str, result: FetchResult,
                    valuation: Optional[ValuationView],
                    universe: Universe,
                    factors: Optional[FactorView] = None) -> dict:
    """The compact summary row — everything the table sorts and screens on."""
    bars = result.bars[symbol]
    closes = [b.close for b in bars]
    volumes = [b.volume for b in bars]
    f = result.fundamentals.get(symbol)

    rvol = relative_volume(volumes, universe.settings.rvol_window)
    last_rvol = next((v for v in reversed(rvol) if v is not None), None)
    sma50 = sma(closes, 50)
    sma200 = sma(closes, 200)
    divergence = price_volume_divergence(closes, volumes)

    row = {
        "symbol": symbol,
        "name": f.name if f else None,
        "tier": universe.tier_of(symbol),
        "sector": f.sector if f else None,
        "quote_type": f.quote_type if f else None,
        "last": _round(closes[-1], 4),
        "last_date": bars[-1].date,
        "change_1d": _round(pct_change(closes, 1), 5),
        "change_1w": _round(pct_change(closes, SESSIONS["1w"]), 5),
        "change_1m": _round(pct_change(closes, SESSIONS["1m"]), 5),
        "change_3m": _round(pct_change(closes, SESSIONS["3m"]), 5),
        "change_6m": _round(pct_change(closes, SESSIONS["6m"]), 5),
        "change_1y": _round(pct_change(closes, SESSIONS["1y"]), 5),
        "volume": _round(volumes[-1], 0),
        "avg_volume_20d": _round(sum(volumes[-20:]) / min(20, len(volumes)), 0),
        "rvol": _round(last_rvol, 3),
        "dollar_volume": _round(closes[-1] * volumes[-1], 0),
        "volume_trend": _round(volume_trend(volumes), 3),
        "up_down_ratio": _round(up_down_volume_ratio(closes, volumes), 3),
        "divergence": divergence.verdict,
        "annualized_vol": _round(annualized_vol(closes), 4),
        "max_drawdown_5y": _round(max_drawdown(closes), 4),
        "above_sma50": None if sma50[-1] is None else closes[-1] > sma50[-1],
        "above_sma200": None if sma200[-1] is None else closes[-1] > sma200[-1],
        "rsi14": _round(next((v for v in reversed(rsi(closes)) if v is not None), None), 2),
        "market_cap": _round(f.market_cap, 0) if f else None,
        "trailing_pe": _round(f.trailing_pe, 3) if f else None,
        "forward_pe": _round(f.forward_pe, 3) if f else None,
        "trailing_eps": _round(f.trailing_eps, 3) if f else None,
        "price_to_book": _round(f.price_to_book, 3) if f else None,
        "dividend_yield": _round(f.dividend_yield, 5) if f else None,
        "beta": _round(f.beta, 3) if f else None,
    }

    if f and f.fifty_two_week_low and f.fifty_two_week_high:
        row["range_52w_position"] = _round(
            range_position(closes[-1], f.fifty_two_week_low, f.fifty_two_week_high), 3
        )
    else:
        row["range_52w_position"] = None

    if factors:
        m = factors.momentum
        row["mom_12_1"] = _round(m.mom_12_1, 5)
        row["mom_6_1"] = _round(m.mom_6_1, 5)
        row["ret_1m"] = _round(m.ret_1m, 5)
        row["mom_rank"] = _round(factors.momentum_rank, 3)
        row["ev_ebitda"] = _round(factors.ev_ebitda, 3)
        row["fcf_yield"] = _round(factors.fcf_yield, 5)
        row["value_score"] = _round(factors.value_score, 3)
        row["roe"] = _round(factors.roe, 5)
        row["roa"] = _round(factors.roa, 5)
        row["op_margin"] = _round(factors.operating_margin, 5)
        row["debt_to_equity"] = _round(factors.debt_to_equity, 4)
        row["quality_score"] = _round(factors.quality_score, 3)
        row["value_trap"] = factors.value_trap
        row["reversal_tension"] = factors.reversal_tension
    else:
        for key in ("mom_12_1", "mom_6_1", "ret_1m", "mom_rank", "ev_ebitda",
                    "fcf_yield", "value_score", "roe", "roa", "op_margin",
                    "debt_to_equity", "quality_score"):
            row[key] = None
        row["value_trap"] = False
        row["reversal_tension"] = False

    if valuation:
        row["peer_group"] = valuation.peer_group
        row["pe_percentile"] = (
            _round(valuation.pe_rank.percentile, 3) if valuation.pe_rank else None
        )
        row["earnings_yield"] = _round(valuation.earnings_yield, 5)
    return row


def collect_notes(notes_dir: Optional[Path] = None, limit: int = 60) -> list[dict]:
    """Read the committed analysis notes for the dashboard's Analysis tab.

    Notes are markdown named ``YYYY-MM-DD.md``. They are small and there is
    at most one per trading day, so the whole recent history ships inline
    rather than as one fetch per note — the tab then renders instantly and
    works offline.
    """
    notes_dir = Path(notes_dir) if notes_dir else NOTES_DIR
    if not notes_dir.exists():
        return []
    out: list[dict] = []
    for path in sorted(notes_dir.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md"),
                       reverse=True)[:limit]:
        try:
            body = path.read_text()
        except OSError:
            continue
        out.append({"date": path.stem, "body": body})
    return out


def write_all(universe: Universe, result: FetchResult,
              valuations: dict[str, ValuationView],
              forecasts: dict[str, SymbolForecast],
              overlay: MacroOverlay,
              factor_views: Optional[dict[str, FactorView]] = None,
              forecaster_pin: Optional[str] = None,
              data_dir: Optional[Path] = None) -> dict:
    """Write every payload. Returns the meta dict for logging."""
    data_dir = Path(data_dir) if data_dir else DATA_DIR
    symbols_dir = data_dir / "symbols"
    symbols_dir.mkdir(parents=True, exist_ok=True)

    factor_views = factor_views or {}
    rows = []
    for symbol in sorted(result.bars):
        valuation = valuations.get(symbol)
        fv = factor_views.get(symbol)
        rows.append(build_index_row(symbol, result, valuation, universe, fv))
        payload = build_symbol_payload(
            symbol, result, valuation, forecasts.get(symbol), overlay, universe,
            factors=fv,
        )
        (symbols_dir / f"{symbol}.json").write_text(
            json.dumps(payload, separators=(",", ":"))
        )

    # Portfolio payload. include_local=False is a hard guarantee, not a
    # convention: dollar values and cost basis cannot reach docs/ even if
    # someone later forgets to strip them here.
    portfolio_payload = None
    holdings = load_holdings(include_local=False)
    if holdings.available and factor_views:
        sectors = {r["symbol"]: r.get("sector") for r in rows}
        pa = analyze_portfolio(holdings, factor_views, sectors)
        portfolio_payload = {
            "as_of": pa.as_of,
            "n_positions": pa.n_positions,
            "cash_weight": _round(pa.cash_weight, 4),
            "equity_weight": _round(pa.equity_weight, 4),
            "tilts": {
                "momentum": _round(pa.momentum_tilt, 3),
                "value": _round(pa.value_tilt, 3),
                "quality": _round(pa.quality_tilt, 3),
                "momentum_equal": _round(pa.momentum_tilt_equal, 3),
                "value_equal": _round(pa.value_tilt_equal, 3),
                "quality_equal": _round(pa.quality_tilt_equal, 3),
            },
            "concentration": {
                "hhi": _round(pa.hhi, 4),
                "top_weight": _round(pa.top_weight, 4),
                "effective_positions": _round(pa.effective_positions, 2),
            },
            "sector_weights": {k: _round(v, 4) for k, v in pa.sector_weights.items()},
            "trap_weight": _round(pa.trap_weight, 4),
            "reversal_weight": _round(pa.reversal_weight, 4),
            "unscored_weight": _round(pa.unscored_weight, 4),
            "positions": [
                {
                    "symbol": r["symbol"], "name": r["name"],
                    "weight": _round(r["weight"], 4),
                    "account_weight": _round(r["account_weight"], 4),
                    "sector": r["sector"],
                    "momentum_rank": _round(r["momentum_rank"], 3),
                    "value_score": _round(r["value_score"], 3),
                    "quality_score": _round(r["quality_score"], 3),
                    "value_trap": r["value_trap"],
                    "reversal_tension": r["reversal_tension"],
                    "scored": r["scored"],
                    "note": r.get("note"),
                }
                for r in pa.rows
            ],
            "notes": pa.notes,
            "cash": [
                {"symbol": c.symbol, "name": c.name, "exposure": _round(c.exposure, 4)}
                for c in holdings.cash
            ],
        }
    (data_dir / "portfolio.json").write_text(
        json.dumps(portfolio_payload or {"available": False}, separators=(",", ":"))
    )

    notes = collect_notes()
    (data_dir / "notes.json").write_text(
        json.dumps({"notes": notes}, separators=(",", ":"))
    )

    index = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fetch_date": result.fetch_date,
        "tiers": [
            {"key": t.key, "label": t.label, "note": t.note,
             "symbols": [s for s in t.symbols if s in result.bars]}
            for t in universe.tiers
        ],
        "rows": rows,
    }
    (data_dir / "index.json").write_text(json.dumps(index, separators=(",", ":")))

    meta = {
        "generated": index["generated"],
        "fetch_date": result.fetch_date,
        "market_desk_version": __version__,
        "symbols_ok": len(result.bars),
        "symbols_failed": result.failures,
        "forecasts_ok": sum(1 for f in forecasts.values() if f.horizons),
        "notes_count": len(notes),
        "portfolio": bool(portfolio_payload),
        "forecaster_pin": forecaster_pin,
        "factor_caveat": UNIVERSE_CAVEAT,
        "macro_overlay": {
            "available": overlay.available,
            "generated": overlay.generated,
            "q_fdr": overlay.q_fdr,
            "candidates_tested": overlay.candidates_tested,
            "limitations": overlay.limitations,
            "error": overlay.error,
        },
        "sources": {
            "prices": "Yahoo Finance via yfinance (split/dividend adjusted)",
            "fundamentals": "Yahoo Finance via yfinance",
            "forecasts": "census_forecaster.markets.trend (damped drift, walk-forward calibrated 90% band)",
            "macro_signals": "census_forecaster markets screen (Granger, BH-FDR)",
        },
        "disclaimer": (
            "Tracker context, not trading advice. Nothing here is a "
            "recommendation to buy or sell any security."
        ),
    }
    (data_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    return meta
