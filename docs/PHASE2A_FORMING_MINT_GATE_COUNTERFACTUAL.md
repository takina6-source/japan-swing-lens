# Phase 2A FORMING MINT Gate — Read-only Counterfactual Report

実施日: 2026-09-13 JST  
入力run: `smrun1:20260911:ae2c9fb95792da5856878c82`  
input SHA-256: `c8f1f12d672ff8a358078958caefc452309384501ef841d233158a2e78521b32`

## 1. Method

GitHub Actionsの初回Cutoverと同一の非公開Core input artifactを読み取り、空の一時DBへ同じrunを
replayした。run ID、input hash、identity内訳、phase内訳は公開seedと一致した。

本レポートは入力JSONと一時replay DBのSELECT/集計だけで作成した。repositoryのproduct data、
公開seed、Release、Pages、workflowは変更していない。将来リターンは使用していない。

## 2. Reproduced Baseline

| Scope/status | 件数 |
|---|---:|
| Scope | 965 |
| CURRENT | 964 |
| INSUFFICIENT_PRICE_HISTORY | 1 |

| Identity | 件数 |
|---|---:|
| LINK | 0 |
| MINT | 811 |
| AMBIGUOUS | 0 |
| NO_SETUP | 153 |

| Persistent phase | 件数 |
|---|---:|
| FORMING | 745 |
| WATCH | 59 |
| POST_BREAKOUT | 7 |

## 3. Pairwise Correlation in the 745 FORMING Setups

Jaccardは`both / either`、`P(B|A)`はA active時にBもactiveだった割合である。

| Pair | Both | Jaccard | P(B\|A) |
|---|---:|---:|---:|
| Minervini + Qullamaggie | 730 | 0.997 | 0.997 |
| Minervini + Darvas | 569 | 0.764 | 0.777 |
| Qullamaggie + Darvas | 568 | 0.763 | 0.778 |
| Minervini + Weinstein | 226 | 0.307 | 0.309 |
| Qullamaggie + Weinstein | 224 | 0.304 | 0.307 |
| Weinstein + Darvas | 196 | 0.318 | 0.848 |
| CAN SLIM + Darvas | 153 | 0.261 | 0.968 |
| Minervini + CAN SLIM | 150 | 0.203 | 0.205 |
| Qullamaggie + CAN SLIM | 150 | 0.203 | 0.205 |
| CAN SLIM + Weinstein | 67 | 0.208 | 0.424 |

MinerviniとQullamaggieはほぼ同じ集合を形成しており、現行aligned countの2票を独立確認と
みなす根拠はない。

## 4. Required-condition Qualification

現行MINT済みFORMING 745件について、raw active手法のREQUIRED conditionを再評価した。

| Qualified active count | 件数 |
|---:|---:|
| 0 | 403 |
| 1 | 162 |
| 2 | 95 |
| 3 | 44 |
| 4 | 38 |
| 5 | 3 |

565件（75.8%）はqualified activeが2未満だった。raw state上は2～5手法activeでも、
実際には各手法の必須条件にFAILが残っているケースが多数を占める。

qualified active 2以上の180件中、175件は3 evidence layersすべてを持ち、5件は2 layersだけだった。

## 5. Alternative Results

WATCH/BREAKOUT維持と明記した案は、FORMINGだけを追加条件で絞った。

| Policy | Total MINT | FORMING | WATCH | BREAKOUT | 現行MINTから除外 | NO_SETUPから逆転 |
|---|---:|---:|---:|---:|---:|---:|
| Baseline smt1 | 811 | 745 | 59 | 7 | 0 | 0 |
| raw count >= 3 | 671 | 605 | 59 | 7 | 140 | 0 |
| raw count >= 4 | 335 | 274 | 55 | 6 | 476 | 0 |
| raw count == 5 | 92 | 64 | 25 | 3 | 719 | 0 |
| FORMING raw >= 4、advanced維持 | 340 | 274 | 59 | 7 | 471 | 0 |
| Min/Q共有clusterを含む3 cluster、advanced維持 | 341 | 275 | 59 | 7 | 470 | 0 |
| Strong Min/Q + qualified corroboration、advanced維持 | 155 | 89 | 59 | 7 | 656 | 0 |
| WATCH/BREAKOUTのみ | 66 | 0 | 59 | 7 | 745 | 0 |
| qualified active >= 2、advanced維持 | 246 | 180 | 59 | 7 | 565 | 0 |
| **qualified active >= 2 + 3 layers、advanced維持** | **241** | **175** | **59** | **7** | **570** | **0** |

raw count 4/5の全state案は、実際のWATCH/BREAKOUTまで落とすため不採用。state-sensitive案は
advanced 66件を保護するが、必須FAILを除外できない。採用候補は必須条件と意味層を直接見る。

## 6. Aligned Distribution of the Proposed 241

| Raw aligned count | 件数 |
|---:|---:|
| 2 | 4 |
| 3 | 10 |
| 4 | 139 |
| 5 | 88 |

raw countは監査値として残るが、採否を単独では決めない。

## 7. Representative Records

### 7.1 Excluded: correlated raw two votes

1812 鹿島建設:

- Core: SETUP FORMING
- raw active: Minervini、Qullamaggie
- qualified active: 0
- Minervini: 8つのREQUIREDがFAIL
- Qullamaggie: momentum/trendのREQUIREDがFAIL、共通contractionだけPASS
- proposed decision: NO_SETUP / `NO_SETUP_QUALIFIED_ACTIVE_LT_2`

### 7.2 Excluded: raw five votes but one qualified

2160 ジーエヌアイグループ:

- raw active: 5
- qualified active: Weinsteinのみ
- proposed decision: NO_SETUP / `NO_SETUP_QUALIFIED_ACTIVE_LT_2`

raw 5票でも自動合格にしないことを示す境界例である。

### 7.3 Excluded: qualified two but Entry Setup layer missing

3132 マクニカホールディングス:

- raw active: 4
- qualified active: Minervini、Weinstein
- evidence: Market / Trend、Stock Quality / Momentum
- missing: Entry Setup
- proposed decision: NO_SETUP / `NO_SETUP_MISSING_ENTRY_SETUP_EVIDENCE`

### 7.4 Retained: qualified two and all three layers

1431 Lib Work:

- raw active: 5
- qualified active: Weinstein、Darvas
- evidence: Market / Trend、Stock Quality / Momentum、Entry Setup
- proposed decision: MINT / `MINT_FORMING_REQUIRED_EVIDENCE_CONFIRMED`

### 7.5 Retained advanced states

59 WATCHと7 BREAKOUTは全件維持される。FORMINGの追加gateを理由にadvanced Core stateを
巻き戻さない。

## 8. Subgroup Check

| Subgroup | Baseline 811 | Proposed 241 |
|---|---:|---:|
| Liquid=false | 313 (38.6%) | 59 (24.5%) |
| Pivot fidelity=PROXY | 437 (53.9%) | 120 (49.8%) |
| Annual Earnings COMPLETE | 791 | 236 |
| Annual Earnings PARTIAL | 19 | 5 |
| Annual Earnings INSUFFICIENT | 1 | 0 |
| Coverage median | 92.5% | 92.5% |

新ruleはliquidity、Pivot fidelity、Annual Earnings completenessを直接条件にしていない。
比率変化は他のrequired evidenceとの共変動である。単一日のため、複数market dateで再確認する。

## 9. Conclusions

1. Phase 2A adapterと現行gateの実装ずれではない。
2. raw `SETUP FORMING`を必須条件成立と解釈したことが主因である。
3. `aligned >= 2`だけでなく、raw aligned count自体の意味がMINT用途には弱い。
4. thresholdを3へ上げても671件残り、問題の中心を解消しない。
5. REQUIRED conditionを再確認すると、現行FORMINGの75.8%はqualified 2未満になる。
6. qualified 2以上と3 evidence layersを要求する案は、advanced 66件を維持しつつ、
   FORMINGを745から175へ絞る。
7. これは1日の構造監査であり、収益性の検証ではない。実装後は複数market dateのshadowが必要。

