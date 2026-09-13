# Phase 2A FORMING MINT Gate Design Result

Status: **READY_FOR_HUMAN_REVIEW**

## Result

初回Cutover固定入力を読み取り専用で再現し、FORMING MINTの大量発行はPhase 2A adapterの
集計バグではなく、Core手法stateの弱い`SETUP FORMING`をそのまま独立票として使用したことが
原因だと確認した。

採用候補は次である。

- FORMING: REQUIRED conditionにFAILがないqualified active strategyを2つ以上要求する。
- FORMING: qualified strategyの必須証拠がMarket/Trend、Quality/Momentum、Entry Setupの
  3 layersすべてを覆うことを要求する。
- WATCH/BREAKOUT: 現行のMINT条件を維持する。
- version: `sm1/csu1`維持、`smt2/idr2/smo2`を新設する。
- 既存811 UID: 削除・再採番・遡及変更しない。
- Gate C: Migration/Activation設計と新ruleの複数日shadowが終わるまで保留する。

固定入力counterfactualは、現行811 MINTから241 MINTへ変化する。

| Phase | 現行 | 採用候補 |
|---|---:|---:|
| FORMING | 745 | 175 |
| WATCH | 59 | 59 |
| POST_BREAKOUT | 7 | 7 |
| 合計 | 811 | 241 |

現行NO_SETUPから新規MINTへ逆転する銘柄は0件。既存MINTから570件が新規則では除外される。

## Deliverables

- `docs/PHASE2A_FORMING_MINT_GATE_DESIGN.md`
- `docs/PHASE2A_FORMING_MINT_GATE_HANDOFF.md`
- `docs/PHASE2A_FORMING_MINT_GATE_COUNTERFACTUAL.md`
- `docs/PHASE2A_FORMING_MINT_GATE_DESIGN_RESULT.md`

## Boundary

設計文書以外は変更していない。product code、test、config、DB、seed、Release、workflow、Pagesを
変更しておらず、commit/pushも行っていない。Human Review Gate承認は実装承認を兼ねない。

