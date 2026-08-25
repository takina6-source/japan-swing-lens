from __future__ import annotations

import logging
import math
from pathlib import Path

import pandas as pd
import streamlit as st

from engine.analyzer import analyze, prepare_universe, rank
from engine.charts import price_chart
from engine.config import ROOT, load_config
from engine.data import DataService
from engine.database import Database
from engine.models import Layer, SetupState
from engine.ui_components import method_card, state_label
from engine.secrets_store import read_secret, save_secret


def _safe(v):
    return -1 if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def _fmt(v, digits=1):
    return "N/A" if _safe(v) < -0.5 else f"{float(v):,.{digits}f}"


def _yen_short(v):
    if v is None or _safe(v) < 0:
        return "N/A"
    value = float(v)
    if value >= 100_000_000:
        return f"{value / 100_000_000:,.1f}億円"
    if value >= 10_000:
        return f"{value / 10_000:,.0f}万円"
    return f"{value:,.0f}円"


def _display(v, unit):
    if v is None or (isinstance(v, float) and math.isnan(v)): return "N/A"
    if isinstance(v, float): return f"{v:,.2f}{unit}"
    return f"{v}{unit}" if unit and not str(v).endswith(unit) else str(v)


LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(filename=LOG_DIR / "engine.log", level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

st.set_page_config(page_title="日本株 Swing Lens", page_icon="📈", layout="wide")
st.markdown("""<style>
  .block-container{max-width:1350px;padding-top:1.4rem}
  .hero{background:linear-gradient(120deg,#15253d,#102c2a);padding:1.3rem 1.5rem;border-radius:18px;margin-bottom:1rem}
  .hero h1{margin:0;font-size:1.75rem}.hero p{margin:.35rem 0 0;color:#b9c6d8}
  .status{display:inline-block;padding:.22rem .55rem;border-radius:999px;background:#24334d;font-size:.8rem}
  .method-card{border:1px solid #2b3952;background:#111a2d;border-radius:14px;padding:.8rem 1rem;margin:.35rem 0 .7rem}
  .method-head{display:flex;justify-content:space-between;gap:.6rem;align-items:center}
  .method-head span{font-size:.82rem;color:#c8d4e5}.condition-bar{height:9px;display:flex;overflow:hidden;border-radius:99px;margin:.65rem 0;background:#364155}
  .seg.pass{background:#2CB67D}.seg.maybe{background:#FBBF24}.seg.fail{background:#EF4565}.seg.na{background:#718096}
  .chips{display:flex;gap:.38rem;flex-wrap:wrap}.chip{padding:.17rem .45rem;border-radius:7px;font-size:.76rem;background:#202b40}
  .chip.pass{color:#68e0ac}.chip.maybe{color:#ffd875}.chip.fail{color:#ff8da4}.chip.na{color:#aeb8c7}
  .plan-box{border:1px solid #2CB67D55;background:linear-gradient(135deg,#112629,#151d31);border-radius:16px;padding:1rem 1.1rem;margin:.6rem 0 1rem}
  @media(max-width:700px){.block-container{padding:.7rem}.hero h1{font-size:1.35rem}
    [data-testid="stDataFrame"]{font-size:.72rem}.desktop-note{display:none}}
</style>""", unsafe_allow_html=True)

CFG = load_config()
DB = Database(ROOT / "data" / "momentum.db")


def secret_key(name):
    return read_secret(name)


@st.cache_data(ttl=3600, show_spinner=False)
def run_scan(mode: str, scope: str, edinet_marker: str, logic_version: str):
    # logic_versionをキャッシュキーに含め、モデルや判定変更後に旧オブジェクトを再利用しない。
    service = DataService(DB)
    result = service.scan(mode, scope=scope, edinet_key=secret_key("EDINET_API_KEY"))
    if len(result) == 6:
        universe, raw, fundamentals, sources, errors, data_status = result
    else:
        universe, raw, fundamentals, sources, errors = result
        data_status = {"requested": len(universe), "available": len(raw),
                       "price": {"max_date": max(str(df.index[-1].date()) for df in raw.values())},
                       "universe_source": "DEMO", "fundamentals": len(fundamentals)}
    if not raw:
        raise RuntimeError("分析可能な株価データがありません")
    benchmark = service.benchmark(mode)
    prepared = prepare_universe(raw, benchmark)
    analyses = []
    for code, df in prepared.items():
        item = analyze(code, universe[code], df, fundamentals[code], sources[code], benchmark, CFG,
                       DB.load_setup_registry(code))
        DB.save_analysis(item, CFG["logic_version"])
        DB.save_signal_tracking(item, df, benchmark, CFG)
        analyses.append(item)
    return rank(analyses), prepared, errors, data_status


@st.cache_data(ttl=86400, show_spinner=False)
def jquants_free_check(code: str, key_marker: str):
    if key_marker == "none": return None
    from engine.data.jquants import JQuantsProvider
    end = pd.Timestamp.now(tz="Asia/Tokyo")
    frame = JQuantsProvider(secret_key("JQUANTS_API_KEY")).history(
        code, end - pd.Timedelta(days=180), end)
    if frame.empty: return None
    return {"date": str(frame.index[-1].date()), "close": float(frame.close.iloc[-1]),
            "source": "J-Quants Free（12週間遅延）"}


st.markdown("""<div class="hero"><h1>日本株 Swing Lens</h1>
<p>5つの順張りStrategy ConsensusとConnors押し目を分離し、Pivot根拠と検証履歴を残すランキング</p></div>""", unsafe_allow_html=True)

with st.sidebar:
    st.header("データと表示")
    mode = st.radio("運用モード", ["無料実用", "デモ"], help="無料実用はJPX公式銘柄一覧＋Yahoo日足＋EDINET財務を使用します。")
    scope = st.selectbox("分析範囲", ["主要500+Growth", "主要500", "全上場銘柄"],
                         help="初回は主要500+Growthがおすすめ。全上場銘柄は初回取得に時間がかかります。") if mode == "無料実用" else "デモ"
    with st.expander("無料EDINET財務を設定"):
        if secret_key("EDINET_API_KEY"):
            st.success("EDINET APIキー設定済み")
        edinet_input = st.text_input("EDINET APIキー", type="password", key="edinet_input",
                                     help="金融庁EDINETで無料発行したキー。Mac内だけに保存します。")
        if st.button("EDINETキーをMac内に保存", disabled=not bool(edinet_input), width="stretch"):
            save_secret("EDINET_API_KEY", edinet_input)
            st.cache_data.clear()
            st.success("保存しました")
    with st.expander("任意：J-Quants Free照合"):
        if secret_key("JQUANTS_API_KEY"):
            st.success("J-Quants APIキー設定済み")
        jq_input = st.text_input("J-Quants V2 APIキー", type="password", key="jq_input",
                                 help="無料プランの12週間遅延データを公式照合に使用します。ランキング株価には使いません。")
        if st.button("J-QuantsキーをMac内に保存", disabled=not bool(jq_input), width="stretch"):
            save_secret("JQUANTS_API_KEY", jq_input)
            st.cache_data.clear()
            st.success("保存しました")
    if st.button("データを更新", width="stretch"):
        st.cache_data.clear()
    st.caption(f"判定ロジック {CFG['logic_version']}")
    st.caption("投資助言ではありません。データ遅延・欠損・機械判定の限界があります。")

try:
    edinet_marker = "configured" if secret_key("EDINET_API_KEY") else "none"
    spinner = "JPX銘柄一覧とYahoo日足を更新しています。初回は数分かかる場合があります…" if mode == "無料実用" else "銘柄を分析しています…"
    with st.spinner(spinner):
        analyses, frames, fetch_errors, data_status = run_scan(mode, scope, edinet_marker, CFG["logic_version"])
except Exception as exc:
    st.error(f"分析を開始できませんでした: {exc}")
    st.info("デモを選ぶと、認証や通信なしで全機能を確認できます。")
    st.stop()

if fetch_errors:
    with st.expander(f"取得できなかったデータ（{len(fetch_errors)}件）"):
        st.write("一部失敗しても、取得済みデータの分析を継続しています。")
        for msg in fetch_errors: st.caption(msg)

regime = analyses[0].metrics["market_regime"]
if mode == "無料実用":
    latest = data_status.get("price", {}).get("max_date") or "N/A"
    coverage = data_status.get("available", 0) / max(data_status.get("requested", 1), 1) * 100
    if coverage < 90:
        st.warning(f"株価取得率が{coverage:.1f}%です。未取得銘柄は除外し、取得済み銘柄で継続しています。")
    st.caption(f"銘柄: JPX公式｜日足: Yahoo Finance（非公式）｜最終株価日: {latest}｜EDINET財務保有: {data_status.get('fundamentals',0)}銘柄")
col1, col2, col3, col4 = st.columns(4)
col1.metric("市場環境", regime)
col2.metric("分析銘柄", f"{len(analyses)}銘柄")
col3.metric("新規ブレイク", sum(a.state == SetupState.BREAKOUT for a in analyses))
col4.metric("ブレイク直前", sum(a.state == SetupState.BREAKOUT_WATCH for a in analyses))

st.subheader("今日のランキング")
f1, f2, f3 = st.columns([1.4, 1.2, 1])
view_filter = f1.selectbox("候補", ["全候補", "新規シグナル", "ブレイク直前", "セットアップ形成中", "複数手法一致", "Momentum上位"])
min_conf = f2.slider("Confluence（最低）", 0, 5, 0)
liquid_only = f3.checkbox("流動性基準を満たす")

filtered = analyses
state_map = {"新規シグナル": SetupState.BREAKOUT, "ブレイク直前": SetupState.BREAKOUT_WATCH,
             "セットアップ形成中": SetupState.SETUP_FORMING}
if view_filter in state_map: filtered = [a for a in filtered if a.state == state_map[view_filter]]
elif view_filter == "複数手法一致": filtered = [a for a in filtered if a.confluence >= 3]
elif view_filter == "Momentum上位": filtered = [a for a in filtered if _safe(a.metrics.get("momentum_percentile")) >= 80]
filtered = [a for a in filtered if a.confluence >= min_conf and (not liquid_only or a.metrics["liquid"])]

rows = []
for a in filtered:
    rows.append({"銘柄": f"{a.code} {a.name}", "状態": a.state.value,
                 "突破": f"{a.breakout_strategy_count}/5", "一致": f"{a.confluence}/5",
                 "Coverage": f"{a.coverage:.0f}%", "Confidence": a.confidence,
                 "Mom順位": _fmt(a.metrics.get("momentum_percentile"), 0),
                 "流動性": f"{a.metrics.get('liquidity_level', 'N/A')} / "
                         f"{_yen_short(a.metrics.get('trading_value_20d'))}",
                 "Minervini": state_label(a.strategies["Minervini"].state),
                 "Qulla": state_label(a.strategies["Qullamaggie"].state),
                 "CAN": state_label(a.strategies["CAN SLIM"].state),
                 "Stage": state_label(a.strategies["Weinstein"].state),
                 "Darvas": state_label(a.strategies["Darvas"].state),
                 "Connors": state_label(a.strategies["Connors"].state)})
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=min(430, 38 + 35 * max(len(rows), 1)))
if not filtered:
    st.info("現在のフィルターに合う候補はありません。")
    st.stop()

labels = {f"{a.code} {a.name}": a for a in filtered}
selected_label = st.selectbox("詳しく見る銘柄", list(labels), index=0)
a = labels[selected_label]
df = frames[a.code]

st.divider()
st.subheader(f"{a.code} {a.name}")
st.markdown(f'<span class="status">{a.state.value}</span>　データ: {a.source}　基準日: {a.as_of}', unsafe_allow_html=True)
s1, s2, s3, s4, s5, s6 = st.columns(6)
s1.metric("現在値", f"¥{a.metrics['price']:,.0f}")
s2.metric("Breakout Consensus", f"{a.breakout_strategy_count}/5")
s3.metric("Confluence", f"{a.confluence}/5")
s4.metric("Coverage / Confidence", f"{a.coverage:.0f}% / {a.confidence}")
pivots = [r.pivot for r in a.strategies.values() if r.pivot]
main_pivot = min(pivots, key=lambda p: abs(p-a.metrics["price"])) if pivots else None
s5.metric("Momentum順位", f"{_fmt(a.metrics.get('momentum_percentile'),0)} percentile")
s6.metric("最寄りPivot", f"¥{main_pivot:,.0f}" if main_pivot else "N/A")
if secret_key("JQUANTS_API_KEY") and mode == "無料実用":
    try:
        official = jquants_free_check(a.code, "configured")
        if official:
            overlap = df.loc[df.index <= pd.Timestamp(official["date"])]
            yahoo_close = float(overlap.close.iloc[-1]) if not overlap.empty else None
            difference = (yahoo_close / official["close"] - 1) * 100 if yahoo_close else None
            st.caption(f"公式遅延照合: {official['date']} J-Quants ¥{official['close']:,.0f} / Yahoo ¥{yahoo_close:,.0f} / 差 {_fmt(difference,2)}%")
    except Exception as exc:
        st.caption(f"J-Quants Free照合は今回取得できませんでした: {exc}")

st.subheader("Liquidity")
liquidity_level = a.metrics.get("liquidity_level", "N/A")
l1, l2, l3, l4 = st.columns(4)
l1.metric("Liquidity Level", liquidity_level)
l2.metric("20日平均売買代金", _yen_short(a.metrics.get("trading_value_20d")))
l3.metric("本日売買代金", _yen_short(a.metrics.get("trading_value")))
ratio = a.metrics.get("trading_value_ratio")
l4.metric("Trading Value Ratio", f"{ratio:.1f}x" if ratio is not None else "N/A")
st.caption(f"GOOD基準：{_yen_short(CFG['liquidity']['levels']['good'])} / 日。銘柄の強さとは別軸の売買執行評価です。")
if liquidity_level in ("LOW", "VERY LOW"):
    st.warning("流動性が低いため、スリッページ・価格急変・損切り時の不利約定に注意してください。")

st.subheader("売買シナリオ")
plan = getattr(a, "trade_plan", None)
if plan is None:
    # 起動中プロセスが旧キャッシュを保持していても、画面を落とさずその場で再計算する。
    from engine.trade_plan import build_trade_plan
    plan = build_trade_plan(a.state, float(a.metrics["price"]), a.strategies, df, CFG)
if plan and plan.entry_low:
    st.markdown(f'<div class="plan-box"><b>{plan.status}</b><br><small>{plan.basis}</small></div>', unsafe_allow_html=True)
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("条件付きエントリー帯", f"¥{plan.entry_low:,.0f}〜¥{plan.entry_high:,.0f}")
    p2.metric("損切り目安", f"¥{plan.stop:,.0f}", f"リスク {_fmt(plan.risk_pct,1)}%", delta_color="inverse")
    p3.metric("第1利確目安", f"¥{plan.target_1r:,.0f}", "1R・一部利確候補")
    p4.metric("基本利確目安", f"¥{plan.target_2r:,.0f}", "2R")
    st.caption(f"伸長時の参考: ¥{plan.target_extended:,.0f}（エントリー基準から+20%）。損切りは支持線と最大7%幅の近い方。")
    if plan.warning: st.warning(plan.warning)
else:
    st.info(f"現在は新規エントリー見送り：{plan.basis if plan else '算出可能なセットアップがありません'}")
st.caption("参考シナリオです。注文を自動送信せず、投資額・許容損失・決算またぎ等の個人事情は反映しません。")

overlays = st.multiselect("チャート表示", ["MA10", "MA20", "MA50", "MA150", "MA200", "Pivot", "Darvas Box", "売買シナリオ"],
                             default=["MA20", "MA50", "MA200", "Pivot", "売買シナリオ"])
d = a.strategies["Darvas"]
st.plotly_chart(price_chart(df, overlays, main_pivot, d.pivot, d.stop, plan), width="stretch",
                config={"displaylogo": False, "scrollZoom": False})

st.subheader("3層サマリー")
for layer in Layer:
    layer_conditions = [c for r in a.strategies.values() for c in r.conditions if c.layer == layer]
    counts = {v: sum(c.verdict.value == v for c in layer_conditions) for v in ("○", "△", "×", "N/A")}
    st.write(f"**{layer.value}**　{counts['○']}○ / {counts['△']}△ / {counts['×']}× / {counts['N/A']} N/A")

st.subheader("手法別の根拠")
method_cols = st.columns(2)
for idx, (name, result) in enumerate(a.strategies.items()):
    method_cols[idx % 2].markdown(method_card(name, result), unsafe_allow_html=True)
st.caption("バーは緑=適合、黄=境界、赤=不適合、灰=データ不足。件数は色別のチップで確認できます。")
for name, result in a.strategies.items():
    with st.expander(f"{name}｜{state_label(result.state)}｜条件を詳しく見る", expanded=name in ("Minervini", "Qullamaggie")):
        if name == "Connors":
            st.caption("短期Pullback手法です。他のブレイクアウト系手法と異なるため、不一致を悪材料として減点しません。")
        table = []
        for c in result.conditions:
            value = _display(c.value, c.unit)
            ref = _display(c.reference, c.unit)
            table.append({"判定": c.verdict.value, "区分": c.role.value, "層": c.layer.value,
                          "条件": c.label, "実際値": value, "基準": ref,
                          "忠実度": c.fidelity.value, "注記": c.note})
        st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
        if result.pivot:
            st.caption(f"Pivot ¥{result.pivot:,.0f}｜{result.pivot_type}｜{result.pivot_basis}｜"
                       f"{result.pivot_fidelity.value}｜形成 {result.pivot_formed_date or 'N/A'}｜"
                       f"距離 {_fmt(result.distance_to_pivot_pct,1)}%")

with st.expander("データ出所・判定上の注意"):
    st.markdown("""
- 無料実用モードは、銘柄一覧をJPX公式、日足・指数をYahoo Finance、財務を金融庁EDINETから取得します。
- Yahoo Financeは非公式ライブラリ経由です。取得失敗時はSQLiteの前回データで継続し、鮮度を表示します。
- EDINET財務は有価証券報告書等が中心で、決算短信より更新が遅い場合があります。未取得項目はN/Aです。
- VCP、Base、Darvas Box、Stage 2、Qullamaggie setupは、目視要素を含むため **PROXY** と明示した機械判定です。
- 取得できない条件は不適合にせず **N/A** として保存します。総合100点は使用していません。
""")
