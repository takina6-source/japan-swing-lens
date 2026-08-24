from engine.analyzer import analyze, prepare_universe
from engine.config import load_config
from engine.data.demo import make_demo_history
from engine.models import SetupState
from engine.trade_plan import build_trade_plan


def _analysis(code="7011"):
    cfg = load_config()
    benchmark = make_demo_history("TOPIX")
    prepared = prepare_universe({code: make_demo_history(code)}, benchmark)
    return analyze(code, code, prepared[code], {"eps_growth": 30, "sales_growth": 25},
                   "TEST", benchmark, cfg), prepared[code], cfg


def test_plan_has_ordered_prices_and_capped_risk():
    result, _, _ = _analysis()
    plan = result.trade_plan
    if plan.entry_low is not None:
        assert plan.entry_low <= plan.entry_high
        assert plan.stop < plan.entry_high
        assert plan.target_1r < plan.target_2r
        assert 0 < plan.risk_pct <= 7.01


def test_extended_stock_is_explicitly_skipped():
    result, frame, cfg = _analysis()
    plan = build_trade_plan(SetupState.EXTENDED, float(frame.close.iloc[-1]),
                            result.strategies, frame, cfg)
    assert plan.status == "見送り"
    assert plan.entry_low is None


def test_trade_plan_is_attached_to_analysis():
    result, _, _ = _analysis("7203")
    assert result.trade_plan is not None
    assert result.trade_plan.basis

