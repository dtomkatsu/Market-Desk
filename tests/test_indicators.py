"""Indicator maths. The invariant that matters most is the None padding:
every series must stay index-aligned with the bars it was computed from."""
import math

import pytest

from market_desk.indicators import (
    annualized_vol, atr, bollinger, ema, macd, max_drawdown,
    pct_change, range_position, rsi, sma,
)


@pytest.fixture
def ramp():
    return [float(i) for i in range(1, 101)]


def test_sma_pads_then_averages(ramp):
    out = sma(ramp, 5)
    assert out[:4] == [None] * 4
    assert len(out) == len(ramp)
    assert out[4] == pytest.approx(3.0)      # mean(1..5)
    assert out[-1] == pytest.approx(98.0)    # mean(96..100)


def test_sma_window_larger_than_series():
    assert sma([1.0, 2.0], 10) == [None, None]


def test_ema_seeds_on_the_first_sma(ramp):
    out = ema(ramp, 5)
    assert out[:4] == [None] * 4
    assert out[4] == pytest.approx(3.0)
    # On a monotonic ramp the EMA trails the value but leads the SMA.
    assert sma(ramp, 5)[-1] < out[-1] < ramp[-1]


def test_rsi_saturates_on_a_pure_uptrend(ramp):
    out = rsi(ramp, 14)
    assert out[13] is None
    assert out[14] == pytest.approx(100.0)


def test_rsi_bottoms_on_a_pure_downtrend(ramp):
    out = rsi(list(reversed(ramp)), 14)
    assert out[14] == pytest.approx(0.0)


def test_rsi_stays_in_bounds():
    import random
    random.seed(7)
    values = [100.0]
    for _ in range(300):
        values.append(max(1.0, values[-1] * (1 + random.gauss(0, 0.02))))
    for v in rsi(values, 14):
        if v is not None:
            assert 0.0 <= v <= 100.0


def test_macd_histogram_is_line_minus_signal(ramp):
    line, signal, hist = macd(ramp)
    for i in range(len(ramp)):
        if line[i] is not None and signal[i] is not None:
            assert hist[i] == pytest.approx(line[i] - signal[i])
        else:
            assert hist[i] is None


def test_bollinger_brackets_the_mean(ramp):
    lower, mid, upper = bollinger(ramp, 20)
    assert lower[:19] == [None] * 19
    assert lower[-1] < mid[-1] < upper[-1]
    # A linear ramp has constant SD, so the band width is constant too.
    widths = [upper[i] - lower[i] for i in range(19, len(ramp))]
    assert max(widths) - min(widths) < 1e-9


def test_atr_is_positive_and_padded():
    highs = [10 + i for i in range(60)]
    lows = [8 + i for i in range(60)]
    closes = [9 + i for i in range(60)]
    out = atr(highs, lows, closes, 14)
    assert out[13] is None
    assert all(v > 0 for v in out[14:])


def test_pct_change_and_guards(ramp):
    assert pct_change(ramp, 1) == pytest.approx(100 / 99 - 1)
    assert pct_change(ramp, 500) is None          # not enough history
    assert pct_change(ramp, 0) is None
    assert pct_change([0.0, 5.0], 1) is None      # zero base, not a div error


def test_annualized_vol_of_a_flat_series_is_zero():
    assert annualized_vol([100.0] * 100, 60) == pytest.approx(0.0)


def test_annualized_vol_needs_enough_history():
    assert annualized_vol([100.0] * 10, 60) is None


def test_max_drawdown_finds_the_worst_trough():
    assert max_drawdown([100, 120, 60, 90]) == pytest.approx(0.5 / -1)
    assert max_drawdown([1, 2, 3]) == pytest.approx(0.0)


def test_range_position():
    assert range_position(50, 0, 100) == pytest.approx(0.5)
    assert range_position(150, 0, 100) == pytest.approx(1.0)   # clamped
    assert range_position(-5, 0, 100) == pytest.approx(0.0)
    assert range_position(5, 10, 10) is None                   # degenerate range
