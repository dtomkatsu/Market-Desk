"""Daily OHLCV + fundamentals, via yfinance.

Two very different kinds of request live here and they fail differently:

* **Price history** is a single batched download for the whole universe.
  It is fast, well-supported, and either works or doesn't.
* **Fundamentals** (``Ticker.info``) is one scrape per symbol against an
  undocumented endpoint. Fields come and go, ETFs legitimately have no
  P/E, and a symbol can return a dict with nothing useful in it. Every
  field is therefore optional and the absence of one is recorded rather
  than defaulted — a P/E of 0.0 standing in for "unknown" would poison
  every rank it enters.

Nothing here is cached across days on purpose: the workflow runs once per
market day and wants that day's close.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

# yfinance drags pandas; import lazily so `import market_desk` stays cheap
# for the pure-transform modules and the tests that only exercise them.


@dataclass(frozen=True)
class Bar:
    """One daily session."""
    date: str          # ISO YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Fundamentals:
    """Point-in-time valuation snapshot. Every field may be None."""
    symbol: str
    name: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    quote_type: Optional[str] = None      # EQUITY | ETF | INDEX ...
    market_cap: Optional[float] = None
    trailing_pe: Optional[float] = None
    forward_pe: Optional[float] = None
    trailing_eps: Optional[float] = None
    forward_eps: Optional[float] = None
    peg_ratio: Optional[float] = None
    price_to_book: Optional[float] = None
    price_to_sales: Optional[float] = None
    profit_margin: Optional[float] = None
    revenue_growth: Optional[float] = None
    earnings_growth: Optional[float] = None
    dividend_yield: Optional[float] = None
    beta: Optional[float] = None
    # --- factor inputs ---
    enterprise_value: Optional[float] = None
    ev_ebitda: Optional[float] = None          # EV / EBITDA, from Yahoo directly
    free_cashflow: Optional[float] = None      # levered FCF, trailing
    operating_cashflow: Optional[float] = None
    return_on_equity: Optional[float] = None   # fraction
    return_on_assets: Optional[float] = None   # fraction; the ROIC proxy Yahoo gives us
    debt_to_equity: Optional[float] = None     # normalized to a ratio (Yahoo quotes percent)
    operating_margin: Optional[float] = None   # fraction
    gross_margin: Optional[float] = None       # fraction
    total_debt: Optional[float] = None
    total_cash: Optional[float] = None
    avg_volume_3m: Optional[float] = None
    shares_outstanding: Optional[float] = None
    fifty_two_week_high: Optional[float] = None
    fifty_two_week_low: Optional[float] = None

    @property
    def is_fund(self) -> bool:
        """ETFs and funds have no meaningful single-company P/E."""
        return (self.quote_type or "").upper() in {"ETF", "MUTUALFUND", "INDEX"}


@dataclass
class FetchResult:
    """What one refresh actually managed to retrieve."""
    fetch_date: str
    bars: dict[str, list[Bar]] = field(default_factory=dict)
    fundamentals: dict[str, Fundamentals] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)   # symbol -> reason
    catalysts: dict[str, dict] = field(default_factory=dict)  # symbol -> raw dates


def _clean(value) -> Optional[float]:
    """Coerce a yfinance field to a finite float, or None.

    yfinance returns absent fields inconsistently: missing key, None,
    the string 'Infinity', or NaN. All four mean "no value".
    """
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def _positive(value) -> Optional[float]:
    """A ratio that is only meaningful when positive.

    A negative trailing P/E means the company lost money. That is real
    information, but it is not a *valuation* — ranking it against
    positive multiples would place the biggest loss-maker at the
    "cheapest" end of the sort. Negative multiples are dropped here and
    the loss is carried by EPS instead, which keeps its sign.
    """
    out = _clean(value)
    if out is None or out <= 0:
        return None
    return out


def fetch_history(symbols: list[str], period: str = "5y") -> tuple[dict[str, list[Bar]], dict[str, str]]:
    """Batched daily OHLCV. Returns (bars_by_symbol, failures_by_symbol)."""
    import pandas as pd  # noqa: F401  (yfinance requires it; imported for clarity)
    import yfinance as yf

    bars: dict[str, list[Bar]] = {}
    failures: dict[str, str] = {}

    frame = yf.download(
        tickers=symbols,
        period=period,
        interval="1d",
        auto_adjust=True,      # split- and dividend-adjusted; what a chart should show
        group_by="ticker",
        threads=True,
        progress=False,
    )
    if frame is None or frame.empty:
        return {}, {s: "empty download" for s in symbols}

    for symbol in symbols:
        # With group_by="ticker" yfinance returns two-level columns for a
        # SINGLE symbol as well as for many, so there is no one-symbol special
        # case to make — an earlier `if len(symbols) > 1` here left the frame
        # un-indexed and every single-symbol call died on a missing "Close".
        # The flat-column branch is kept only as a defence against the shape
        # changing under us.
        try:
            sub = frame[symbol] if frame.columns.nlevels > 1 else frame
        except KeyError:
            failures[symbol] = "no rows in download"
            continue
        if "Close" not in sub.columns:
            failures[symbol] = "unexpected frame shape (no Close column)"
            continue

        sub = sub.dropna(subset=["Close"])
        if sub.empty:
            failures[symbol] = "no usable closes"
            continue

        rows: list[Bar] = []
        for ts, row in sub.iterrows():
            close = _clean(row.get("Close"))
            if close is None:
                continue
            rows.append(
                Bar(
                    date=ts.date().isoformat(),
                    # A session missing OHLC but carrying a close is a stale
                    # or halted print; flatten it to the close rather than
                    # dropping the day, so the series stays contiguous.
                    open=_clean(row.get("Open")) or close,
                    high=_clean(row.get("High")) or close,
                    low=_clean(row.get("Low")) or close,
                    close=close,
                    volume=_clean(row.get("Volume")) or 0.0,
                )
            )
        if rows:
            bars[symbol] = rows
        else:
            failures[symbol] = "no usable rows"

    return bars, failures


def fetch_fundamentals(symbols: list[str], pause: float = 0.2) -> dict[str, Fundamentals]:
    """Per-symbol valuation snapshot. Never raises; missing is missing."""
    import yfinance as yf

    out: dict[str, Fundamentals] = {}
    for symbol in symbols:
        try:
            info = yf.Ticker(symbol).info or {}
        except Exception as exc:                      # noqa: BLE001 - best effort by design
            out[symbol] = Fundamentals(symbol=symbol)
            print(f"  ! {symbol}: fundamentals unavailable ({type(exc).__name__}: {exc})")
            time.sleep(pause)
            continue

        # Dividend yield is a units trap. `dividendYield` is quoted in
        # PERCENT (NVDA 0.46 = 0.46%, MO 6.5 = 6.5%), so treating it as a
        # fraction reports a 0.46% payer as yielding 46%. Prefer
        # `trailingAnnualDividendYield`, which is a true fraction computed
        # from dividends actually paid — it also sidesteps a forward
        # `dividendRate` that is sometimes plainly wrong (yfinance reported
        # NVDA at $1.00/yr against an actual $0.04 in 2026-08). Fall back to
        # the percent field, scaled, when the trailing one is absent.
        div_yield = _clean(info.get("trailingAnnualDividendYield"))
        if div_yield is None:
            pct = _clean(info.get("dividendYield"))
            div_yield = pct / 100.0 if pct is not None else None

        out[symbol] = Fundamentals(
            symbol=symbol,
            name=info.get("shortName") or info.get("longName"),
            sector=info.get("sector"),
            industry=info.get("industry"),
            quote_type=info.get("quoteType"),
            market_cap=_clean(info.get("marketCap")),
            trailing_pe=_positive(info.get("trailingPE")),
            forward_pe=_positive(info.get("forwardPE")),
            trailing_eps=_clean(info.get("trailingEps")),
            forward_eps=_clean(info.get("forwardEps")),
            peg_ratio=_clean(info.get("trailingPegRatio") or info.get("pegRatio")),
            price_to_book=_positive(info.get("priceToBook")),
            price_to_sales=_positive(info.get("priceToSalesTrailing12Months")),
            profit_margin=_clean(info.get("profitMargins")),
            revenue_growth=_clean(info.get("revenueGrowth")),
            earnings_growth=_clean(info.get("earningsGrowth")),
            dividend_yield=div_yield,
            beta=_clean(info.get("beta")),
            enterprise_value=_clean(info.get("enterpriseValue")),
            ev_ebitda=_positive(info.get("enterpriseToEbitda")),
            free_cashflow=_clean(info.get("freeCashflow")),
            operating_cashflow=_clean(info.get("operatingCashflow")),
            return_on_equity=_clean(info.get("returnOnEquity")),
            return_on_assets=_clean(info.get("returnOnAssets")),
            # Yahoo quotes debtToEquity in percent (25.8 means 0.258); banks
            # and funds return None, which stays None.
            debt_to_equity=(lambda d: d / 100.0 if d is not None else None)(
                _clean(info.get("debtToEquity"))),
            operating_margin=_clean(info.get("operatingMargins")),
            gross_margin=_clean(info.get("grossMargins")),
            total_debt=_clean(info.get("totalDebt")),
            total_cash=_clean(info.get("totalCash")),
            avg_volume_3m=_clean(info.get("averageVolume")),
            shares_outstanding=_clean(info.get("sharesOutstanding")),
            fifty_two_week_high=_clean(info.get("fiftyTwoWeekHigh")),
            fifty_two_week_low=_clean(info.get("fiftyTwoWeekLow")),
        )
        time.sleep(pause)   # be a polite client of an unofficial endpoint
    return out


def fetch_earnings_dates(symbols: list[str], pause: float = 0.2) -> dict[str, dict]:
    """Announcement dates (past and scheduled) plus the ex-dividend date.

    Best-effort per symbol, like fundamentals: this is the same undocumented
    endpoint, and an ETF legitimately has none. A failure yields an empty
    record rather than aborting the refresh.
    """
    import yfinance as yf

    out: dict[str, dict] = {}
    for symbol in symbols:
        record: dict = {"earnings_dates": [], "ex_dividend": None}
        try:
            ticker = yf.Ticker(symbol)
            frame = ticker.earnings_dates
            if frame is not None and not frame.empty:
                # Keep the TIME, not just the date. Companies reporting after
                # the close move the *next* session, and a date alone cannot
                # tell the two cases apart — measuring the wrong bar makes an
                # after-close reporter look like it barely reacts to earnings.
                record["earnings_dates"] = sorted(
                    {ts.isoformat() for ts in frame.index}
                )
            cal = ticker.calendar or {}
            ex_div = cal.get("Ex-Dividend Date")
            if ex_div is not None:
                record["ex_dividend"] = getattr(ex_div, "isoformat", lambda: str(ex_div))()
        except Exception as exc:                      # noqa: BLE001
            print(f"  ! {symbol}: catalysts unavailable ({type(exc).__name__})")
        out[symbol] = record
        time.sleep(pause)
    return out


def fetch_all(symbols: list[str], period: str = "5y") -> FetchResult:
    """One full refresh."""
    print(f"fetching {len(symbols)} symbols, period={period}")
    bars, failures = fetch_history(symbols, period=period)
    print(f"  history: {len(bars)} ok, {len(failures)} failed")

    got = [s for s in symbols if s in bars]
    fundamentals = fetch_fundamentals(got)
    print(f"  fundamentals: {sum(1 for f in fundamentals.values() if f.trailing_pe)} with a trailing P/E")

    catalysts = fetch_earnings_dates(got)
    scheduled = sum(1 for c in catalysts.values() if c.get("earnings_dates"))
    print(f"  catalysts: {scheduled} symbols with earnings dates")

    return FetchResult(
        fetch_date=datetime.now(timezone.utc).date().isoformat(),
        bars=bars,
        fundamentals=fundamentals,
        failures=failures,
        catalysts=catalysts,
    )
