from pathlib import Path


DASHBOARD = Path(__file__).parents[1] / "public" / "dashboard"


def test_beginner_view_keeps_original_investment_terms():
    script = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    expected_pairs = [
        ("株価の勢い", "Momentum"),
        ("上昇開始の一致度", "Breakout Consensus"),
        ("総合合致度", "Confluence"),
        ("判定材料の充足率", "Coverage"),
        ("データ信頼度", "Confidence"),
        ("上昇開始の基準値", "Pivot"),
        ("売買しやすさ", "Liquidity"),
    ]
    for japanese, original in expected_pairs:
        assert japanese in script
        assert original in script


def test_dashboard_has_help_interpretations_and_view_switch():
    script = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
    css = (DASHBOARD / "upgrade.css").read_text(encoding="utf-8")

    assert "TERM_HELP" in script
    assert 'class="term-help"' in script
    assert "つまり：" in script
    assert '>かんたん</button>' in script
    assert '>詳細</button>' in script
    assert 'id="termSheet"' in html
    assert ".simple-view .expert-only" in css


def test_dashboard_shell_cache_versions_match():
    html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
    service_worker = (DASHBOARD / "sw.js").read_text(encoding="utf-8")

    assert "app.js?v=12" in html
    assert "app.js?v=12" in service_worker
    assert "swing-lens-v12" in service_worker
