from pathlib import Path


DASHBOARD = Path(__file__).parents[1] / "public" / "dashboard"


def _source(name: str) -> str:
    return (DASHBOARD / name).read_text(encoding="utf-8")


def test_navigation_defaults_to_ranking_and_supports_direct_history_route():
    html = _source("index.html")
    script = _source("research.js")

    assert 'id="rankingView" role="tabpanel"' in html
    assert 'id="researchView" class="research-shell" role="tabpanel"' in html
    assert 'id="researchView" class="research-shell" role="tabpanel" aria-labelledby="researchTab" hidden' in html
    assert "location.hash.toLowerCase() === '#research'" in script
    assert "history.pushState" in script
    assert "addEventListener('popstate'" in script
    assert "addEventListener('hashchange'" in script
    assert "if (research && !state.loaded" in script


def test_research_mode_is_simple_by_default_and_switchable():
    script = _source("research.js")

    assert "mode: 'simple'" in script
    assert 'data-research-mode="simple"' in script
    assert 'data-research-mode="detail"' in script
    assert "state.mode = mode.dataset.researchMode === 'detail' ? 'detail' : 'simple'" in script
    assert "つまり：" not in script


def test_research_fetch_is_isolated_retryable_and_partial_safe():
    script = _source("research.js")

    for name in ("index", "hypotheses", "families", "checkpoints",
                 "intraday_diagnostics", "storage_metrics", "performance_metrics"):
        assert f"'{name}'" in script
    assert "Promise.allSettled" in script
    assert "一部のResearchデータを表示できません" in script
    assert "Researchデータを読み込めませんでした" in script
    assert "銘柄ランキングはそのまま利用できます" in script
    assert 'class="research-retry"' in script
    assert "return 'N/A'" in script
    assert "'未計算'" in script


def test_hypotheses_and_checkpoint_contracts_are_visible_without_frontend_decisions():
    script = _source("research.js")

    for hypothesis in ("H1", "H2", "H3", "H4"):
        assert f"{hypothesis}:" in script
    for condition in ("events", "paired_events", "unique_stocks", "unique_dates",
                      "group_3", "group_4", "group_4plus", "group_5"):
        assert condition in script
    assert "途中経過の確認" in script
    assert "一度だけ行う正式検証" in script
    assert "収集が最も遅い条件" in script
    assert "Registry記録" in script
    assert "正式検証済み・Family Close待ち" in script
    assert "Family Close前のため、最終結論として扱えません" in script
    assert "p < 0.05" not in script


def test_data_quality_h3_entry_and_diagnostics_explanations_exist():
    script = _source("research.js")

    for label in ("LIVE_FORWARD", "RECONSTRUCTED_LEGACY", "NOT_ELIGIBLE",
                  "DISCOVERY", "HOLDOUT", "POST_CONFIRMATION"):
        assert label in script
    assert "Legacyが多くても、正式検証が進んだようには表示しません" in script
    assert "LIVE_FORWARD かつ HOLDOUT" in script
    assert "DIFFERENCEとして固定済み" in script
    assert "Difference" in script and "Equivalence" in script
    assert "HIGH Costで方向が逆転" in script
    for model in ("T+1 Open", "T Close Reference", "Pivot Stop", "PATH_AMBIGUOUS"):
        assert model in script
    for metric in ("Overnight Gap", "Gap / ATR", "Gap / R",
                   "Breakout Day Range / ATR", "Intraday False Breakout rate"):
        assert metric in script


def test_mobile_and_accessibility_contracts():
    html = _source("index.html")
    css = _source("research.css")
    script = _source("research.js")

    assert 'role="tablist"' in html
    assert 'aria-selected="true"' in html
    assert 'aria-controls="researchView"' in html
    assert 'aria-expanded="false"' in script
    assert "ArrowLeft" in script and "ArrowRight" in script
    assert "min-height:44px" in css
    assert "@media(max-width:650px)" in css
    assert ".checkpoint-grid{grid-template-columns:1fr}" in css
    assert "overflow-wrap:anywhere" in css
