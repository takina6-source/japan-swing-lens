from __future__ import annotations
import html
from .models import SetupState

STATE_LABELS = {
    SetupState.BREAKOUT: "🟢 新規ブレイク",
    SetupState.BREAKOUT_WATCH: "🟡 ブレイク直前",
    SetupState.SETUP_FORMING: "🔵 形成中",
    SetupState.PULLBACK: "🟣 押し目",
    SetupState.EXTENDED: "🟠 過熱",
    SetupState.FAILED: "🔴 失敗",
    SetupState.NOT_QUALIFIED: "⚪ 対象外",
}


def state_label(state: SetupState) -> str:
    return STATE_LABELS[state]


def method_card(name, result) -> str:
    c = result.counts
    total = max(sum(c.values()), 1)
    segments = "".join(
        f'<span class="seg {klass}" style="width:{c[key]/total*100:.2f}%"></span>'
        for key, klass in (("○", "pass"), ("△", "maybe"), ("×", "fail"), ("N/A", "na")) if c[key]
    )
    chips = (f'<span class="chip pass">○ 適合 <b>{c["○"]}</b></span>'
             f'<span class="chip maybe">△ 境界 <b>{c["△"]}</b></span>'
             f'<span class="chip fail">× 不適合 <b>{c["×"]}</b></span>'
             f'<span class="chip na">— 不明 <b>{c["N/A"]}</b></span>')
    return (f'<div class="method-card"><div class="method-head"><b>{html.escape(name)}</b>'
            f'<span>{html.escape(state_label(result.state))}</span></div>'
            f'<div class="condition-bar">{segments}</div><div class="chips">{chips}</div></div>')
