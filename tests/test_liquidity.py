from engine.config import load_config
from engine.liquidity import liquidity_level, trading_value_ratio


def test_liquidity_level_boundaries():
    cfg = load_config()
    assert liquidity_level(1_500_000_000, cfg) == "VERY HIGH"
    assert liquidity_level(500_000_000, cfg) == "HIGH"
    assert liquidity_level(300_000_000, cfg) == "GOOD"
    assert liquidity_level(80_000_000, cfg) == "LOW"
    assert liquidity_level(20_000_000, cfg) == "VERY LOW"


def test_trading_value_ratio_keeps_normal_liquidity_separate():
    assert trading_value_ratio(200_000_000, 40_000_000) == 5.0
    assert trading_value_ratio(1, 0) is None
