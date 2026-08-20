"""The Census-Forecaster overlay: which tickers lead Hawaii's economy.

This is the substantive link between the two repos. Census-Forecaster
runs a pre-registered Granger lead-lag screen of market series against
Hawaii macro targets (unemployment, Honolulu CPI, home values), with
BH-FDR control across every pair × transform × lag actually tested and a
2020-exclusion robustness re-run. The survivors are bundled in the
package as ``selected_signals.json``.

What this module does with them is annotate, not predict. A ticker that
survives the screen gets a badge on the dashboard saying which Hawaii
series its past returns lead and by how many months. It does **not**
touch any price forecast — the upstream repo tested that direction
(``markets/fundamentals.py``) and found a clean efficient-markets null,
so importing macro state into a return forecast would be re-running a
failed experiment.

Three caveats travel with every signal, and the front end prints them:

* Granger causality is predictive precedence, not causation. Confounders
  survive this screen.
* Signals that fail ``robust_to_2020_exclusion`` are one-event artifacts
  of the COVID shock and are labeled as such rather than dropped, so the
  reader can see the difference.
* The arrow runs prices → Hawaii economy. It is not a claim that Hawaii
  data predicts these stocks.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class LeadSignal:
    """One surviving ticker → Hawaii-target relationship."""
    ticker: str
    target: str
    lead_months: Optional[int]
    granger_p: Optional[float]
    robust_to_2020: bool
    sign_matches_hypothesis: Optional[bool]
    transform: str


@dataclass
class MacroOverlay:
    generated: Optional[str] = None
    q_fdr: Optional[float] = None
    candidates_tested: Optional[int] = None
    limitations: str = ""
    signals_by_ticker: dict[str, list[LeadSignal]] = field(default_factory=dict)
    hypotheses: dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def available(self) -> bool:
        return self.error is None and bool(self.signals_by_ticker or self.hypotheses)


# The macro targets the upstream screen actually tests against, as they
# appear in selected_signals.json.
_TARGET_LABELS = {
    "HI_UNEMPLOYMENT": "Hawaii unemployment rate",
    "US_UNEMPLOYMENT": "US unemployment rate",
    "HI_VISITORS": "Hawaii visitor arrivals",
    "HI_ELECTRICITY": "Hawaii electricity price",
    "HONOLULU_CPI": "Honolulu CPI (all items)",
    "HONOLULU_CPI_RENT": "Honolulu rent CPI",
    "HONOLULU_ZHVI": "Honolulu home values (Zillow ZHVI)",
    "HONOLULU_ZORI": "Honolulu asking rents (Zillow ZORI)",
    "HONOLULU_SF_MEDIAN": "Honolulu single-family median price",
}


def target_label(target: str) -> str:
    """Human label for a macro target, with a sane fallback for new ones.

    Title-casing alone turns HI_ELECTRICITY into "Hi Electricity", which
    reads as a greeting. Expand the geography prefixes explicitly so an
    unmapped target added upstream still renders sensibly.
    """
    if target in _TARGET_LABELS:
        return _TARGET_LABELS[target]
    rest = target
    prefix = ""
    for code, name in (("HI_", "Hawaii "), ("US_", "US "), ("HONOLULU_", "Honolulu ")):
        if target.startswith(code):
            prefix, rest = name, target[len(code):]
            break
    return prefix + rest.replace("_", " ").lower()


def _signals_path() -> Optional[Path]:
    """Locate the bundled signals file inside the installed package."""
    try:
        import census_forecaster                      # type: ignore
    except Exception:                                 # noqa: BLE001
        return None
    root = Path(census_forecaster.__file__).resolve().parent
    candidate = root / "data" / "markets" / "selected_signals.json"
    return candidate if candidate.exists() else None


def _ticker_hypotheses() -> dict[str, str]:
    """Per-ticker lead hypotheses from the upstream pre-registered universe."""
    try:
        from census_forecaster.markets.universe import TICKERS  # type: ignore
    except Exception:                                 # noqa: BLE001
        return {}
    return {spec.symbol: spec.hypothesis for spec in TICKERS}


def load_overlay(min_robust_only: bool = False) -> MacroOverlay:
    """Read the bundled screen output.

    ``min_robust_only`` drops signals that did not survive the
    2020-exclusion re-run. Default False: the dashboard shows both and
    labels them, because "this only held during COVID" is itself worth
    seeing.
    """
    hypotheses = _ticker_hypotheses()
    path = _signals_path()
    if path is None:
        return MacroOverlay(
            hypotheses=hypotheses,
            error="census_forecaster not installed, or its bundled market signals are missing",
        )

    try:
        raw = json.loads(path.read_text())
    except Exception as exc:                          # noqa: BLE001
        return MacroOverlay(hypotheses=hypotheses, error=f"unreadable signals file: {exc}")

    by_ticker: dict[str, list[LeadSignal]] = {}
    for row in raw.get("signals") or []:
        ticker = row.get("ticker")
        if not ticker:
            continue
        robust = bool(row.get("robust_to_2020_exclusion"))
        if min_robust_only and not robust:
            continue
        by_ticker.setdefault(ticker, []).append(LeadSignal(
            ticker=ticker,
            target=row.get("target", ""),
            lead_months=row.get("best_xcorr_lead_months"),
            granger_p=row.get("granger_p"),
            robust_to_2020=robust,
            sign_matches_hypothesis=row.get("sign_matches_hypothesis"),
            transform=row.get("transform", ""),
        ))

    # Strongest (lowest Granger p) first, robust signals ahead of fragile
    # ones at equal strength.
    for signals in by_ticker.values():
        signals.sort(key=lambda s: (not s.robust_to_2020, s.granger_p if s.granger_p is not None else 1.0))

    return MacroOverlay(
        generated=raw.get("generated"),
        q_fdr=raw.get("q_fdr"),
        candidates_tested=raw.get("candidates_tested"),
        limitations=raw.get("limitations", ""),
        signals_by_ticker=by_ticker,
        hypotheses=hypotheses,
    )


def summarize_ticker(overlay: MacroOverlay, symbol: str,
                     max_signals: int = 3) -> list[dict]:
    """Compact per-ticker signal list for the dashboard payload."""
    out: list[dict] = []
    seen: set[str] = set()
    for sig in overlay.signals_by_ticker.get(symbol, []):
        # The upstream file carries one row per (target, granger_lags), so a
        # single relationship appears several times at different lag counts.
        # Keep only the strongest row per target — the list is already sorted
        # robust-first then by ascending p.
        if sig.target in seen:
            continue
        seen.add(sig.target)
        if len(out) >= max_signals:
            break
        out.append({
            "target": sig.target,
            "target_label": target_label(sig.target),
            "lead_months": sig.lead_months,
            "granger_p": sig.granger_p,
            "robust_to_2020": sig.robust_to_2020,
            "transform": sig.transform,
        })
    return out
