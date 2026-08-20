"""Volume analytics — the half of the dashboard that reads participation."""
import pytest

from market_desk.volume import (
    accumulation_distribution, dollar_volume, obv, price_volume_divergence,
    relative_volume, rolling_vwap, up_down_volume_ratio, volume_trend,
)


def series(n=200, slope=0.0, vol=1e6, vol_tail=None, tail=20):
    closes = [100.0 * (1 + slope) ** i for i in range(n)]
    volumes = [vol] * n
    if vol_tail is not None:
        volumes[-tail:] = [vol_tail] * tail
    return closes, volumes


def test_relative_volume_excludes_the_current_bar():
    volumes = [100.0] * 30
    volumes[25] = 300.0
    out = relative_volume(volumes, window=20)
    # The spike must read 3x, not be diluted by counting itself in the base.
    assert out[25] == pytest.approx(3.0)


def test_relative_volume_pads_the_warmup():
    out = relative_volume([100.0] * 30, window=20)
    assert out[:20] == [None] * 20
    assert out[20] == pytest.approx(1.0)


def test_obv_tracks_direction():
    closes = [10, 11, 12, 11]
    volumes = [100.0, 200.0, 300.0, 400.0]
    assert obv(closes, volumes) == [0.0, 200.0, 500.0, 100.0]


def test_obv_ignores_unchanged_closes():
    assert obv([10, 10, 10], [5.0, 5.0, 5.0]) == [0.0, 0.0, 0.0]


def test_dollar_volume_multiplies():
    assert dollar_volume([2.0, 4.0], [10.0, 5.0]) == [20.0, 20.0]


def test_vwap_sits_inside_the_price_range():
    n = 40
    highs = [11.0] * n
    lows = [9.0] * n
    closes = [10.0] * n
    volumes = [100.0] * n
    out = rolling_vwap(highs, lows, closes, volumes, window=20)
    assert out[:19] == [None] * 19
    assert out[-1] == pytest.approx(10.0)


def test_vwap_is_volume_weighted_not_a_plain_mean():
    highs = [10.0, 20.0]
    lows = [10.0, 20.0]
    closes = [10.0, 20.0]
    volumes = [1.0, 9.0]        # the 20 print carries 90% of the volume
    out = rolling_vwap(highs, lows, closes, volumes, window=2)
    assert out[-1] == pytest.approx(19.0)


def test_accumulation_rises_on_closes_at_the_high():
    n = 10
    highs = [10.0] * n
    lows = [8.0] * n
    closes = [10.0] * n          # every close at the high = pure accumulation
    volumes = [100.0] * n
    out = accumulation_distribution(highs, lows, closes, volumes)
    assert out[-1] == pytest.approx(1000.0)
    assert all(out[i] <= out[i + 1] for i in range(n - 1))


def test_accumulation_handles_a_zero_range_bar():
    # A halted or limit-locked session has high == low; it must not divide by zero.
    out = accumulation_distribution([5.0], [5.0], [5.0], [100.0])
    assert out == [0.0]


def test_up_down_ratio_returns_none_without_down_sessions():
    closes = [100 + i for i in range(60)]
    assert up_down_volume_ratio(closes, [1e6] * 60, window=50) is None


def test_up_down_ratio_measures_asymmetry():
    closes, volumes = [], []
    price = 100.0
    for i in range(61):
        closes.append(price)
        price += 1 if i % 2 == 0 else -1
        volumes.append(2e6 if i % 2 == 1 else 1e6)
    ratio = up_down_volume_ratio(closes, volumes, window=50)
    assert ratio is not None and ratio > 0


def test_volume_trend_detects_expansion():
    _, volumes = series(vol=1e6, vol_tail=2e6, tail=20)
    assert volume_trend(volumes, short=20, long=60) > 1.3


def test_volume_trend_needs_the_long_window():
    assert volume_trend([1e6] * 30, short=20, long=60) is None


@pytest.mark.parametrize("slope,vol_tail,expected", [
    (0.002, 3e6, "confirmed"),    # rally, expanding volume
    (0.002, 3e5, "weak"),         # rally, contracting volume
    (-0.002, 3e6, "washout"),     # decline, heavy volume
    (-0.002, 3e5, "quiet"),       # decline, light volume
    (0.0, 1e6, "quiet"),          # no move at all
])
def test_divergence_classification(slope, vol_tail, expected):
    closes, volumes = series(slope=slope, vol_tail=vol_tail)
    assert price_volume_divergence(closes, volumes).verdict == expected


def test_divergence_labels_an_ordinary_move_mixed():
    closes, volumes = series(slope=0.002, vol_tail=1e6)
    d = price_volume_divergence(closes, volumes)
    assert d.verdict == "mixed"
    # The old bug: this branch claimed "no clear price move" on a real move.
    assert "no clear price move" not in d.detail
    assert d.price_change > 0.02


def test_divergence_is_quiet_without_enough_history():
    d = price_volume_divergence([100.0] * 10, [1e6] * 10)
    assert d.verdict == "quiet"
    assert d.price_change is None
