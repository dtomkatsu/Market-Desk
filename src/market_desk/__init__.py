"""Market-Desk — a stock tracking dashboard with valuation and volume analytics.

The pipeline is deliberately one-directional and file-based:

    config/watchlist.yml
        -> fetch.py      (daily OHLCV + fundamentals, via yfinance)
        -> indicators.py / volume.py / valuation.py  (pure transforms)
        -> forecast.py   (damped-drift bands, via Census-Forecaster)
        -> build.py      (writes docs/data/*.json)
        -> docs/         (static GitHub Pages front end)

Nothing in ``docs/`` calls an API at view time: the page reads committed
JSON, so the dashboard keeps working when a data source breaks and every
number on screen is reproducible from a commit.

Standing line, inherited from Census-Forecaster's markets subpackage:
this is tracker context, **not trading advice**.
"""

__version__ = "0.1.0"
