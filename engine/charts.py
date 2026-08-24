from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def price_chart(df: pd.DataFrame, overlays: list[str], pivot: float | None = None,
                darvas_top: float | None = None, darvas_bottom: float | None = None,
                trade_plan=None):
    view = df.tail(260)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=.04,
                        row_heights=[.76, .24])
    fig.add_trace(go.Candlestick(x=view.index, open=view.open, high=view.high,
                                 low=view.low, close=view.close, name="株価"), row=1, col=1)
    colors = {"MA10": "#7DD3FC", "MA20": "#F9A8D4", "MA50": "#FBBF24",
              "MA150": "#A78BFA", "MA200": "#F87171"}
    for label in overlays:
        key = label.lower()
        if key in view:
            fig.add_trace(go.Scatter(x=view.index, y=view[key], name=label,
                                     line={"width": 1.4, "color": colors[label]}), row=1, col=1)
    if "Pivot" in overlays and pivot:
        fig.add_hline(y=pivot, line_dash="dash", line_color="#2CB67D", annotation_text="Pivot", row=1, col=1)
    if "Darvas Box" in overlays and darvas_top and darvas_bottom:
        fig.add_hrect(y0=darvas_bottom, y1=darvas_top, fillcolor="#60A5FA", opacity=.10,
                      line_width=1, annotation_text="Darvas Box", row=1, col=1)
    if "売買シナリオ" in overlays and trade_plan and trade_plan.stop:
        fig.add_hline(y=trade_plan.stop, line_dash="dot", line_color="#EF4565",
                      annotation_text="損切り目安", row=1, col=1)
        fig.add_hrect(y0=trade_plan.entry_low, y1=trade_plan.entry_high,
                      fillcolor="#2CB67D", opacity=.10, line_width=0,
                      annotation_text="Entry帯", row=1, col=1)
        fig.add_hline(y=trade_plan.target_2r, line_dash="dot", line_color="#FBBF24",
                      annotation_text="2R利確目安", row=1, col=1)
    colors_v = ["#2CB67D" if c >= o else "#EF4565" for c, o in zip(view.close, view.open)]
    fig.add_trace(go.Bar(x=view.index, y=view.volume, marker_color=colors_v, name="出来高"), row=2, col=1)
    fig.update_layout(height=610, margin=dict(l=5, r=5, t=25, b=5), xaxis_rangeslider_visible=False,
                      hovermode="x unified", legend=dict(orientation="h"), template="plotly_dark")
    return fig
