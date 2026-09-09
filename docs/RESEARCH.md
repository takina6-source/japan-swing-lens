# Research Layer Phase 1

## 目的と境界

Research Layerは、既存Coreが抽出した銘柄を別角度から将来検証する追加レイヤーです。
Coreの6手法、Consensus、WATCH / BREAKOUT、Pivot、Ranking、Coverage、Confidence、
Liquidity、およびExperimental 3手法の判定には入力も加点もしません。Phase 1では新しい
外部データや分足データも取得しません。

独立Versionは`2026.09-research-v1`です。Hypothesis Registry、Family Registry、Research
Event、ValidationSubject、Research Control、履歴、Checkpoint結果をResearch専用表へ保存します。
イベント・対象・Control membership・Freeze済み定義は追記専用です。定義変更は既存Versionの
上書きではなくv2と新Familyを必要とします。

## Freezeとデータ区分

- Family: `JSL_RESEARCH_CONFIRM_V1`
- Freeze date: 2026-09-09
- Freeze時点で参照済み: 2026-09-08まで
- 共通Holdout start: 2026-09-10
- Family close: 2027-01-26（JPX休業日を除く、共通Holdout開始から90取引日目）
- H1-v1〜H4-v1のorigin: `DATA_INFORMED`

`RECONSTRUCTED_LEGACY`は探索（DISCOVERY）にだけ使用できます。正式確認のCheckpoint A/Bには
`LIVE_FORWARD`かつ`HOLDOUT`の対象だけを含めます。必要情報がなく安全に再構築できないものは
`NOT_ELIGIBLE`です。Family終了後の新規対象は`POST_CONFIRMATION`となり、v1の正式結果には
追加しません。

## H1〜H4

| ID | 主な問い | Primary | SESOI | Entry / Control |
|---|---|---|---:|---|
| H1-v1 | 4/5+ Alignmentは3/5より優れるか | 10日Matched excess差 | +0.50pp | Core Signal Date / Core Matched |
| H2-v1 | 5/5 WATCHからBREAKOUT後も優れるか | 10日Net Matched excess | +0.75pp | T+1 Open / Breakout時点の新Control |
| H3-v1 | Pivot StopとT+1 Openに実務差があるか | paired 10日Net return差 | ±0.30pp | 同一Event内paired |
| H4-v1 | 5/5は4/5より失敗が少ないか | 5日Failed Breakout率差 | -8pp | Signal群同士 |

探索Candidateは日付クラスタBootstrapのtwo-sided 90% CIとサンプル多様性で判定し、p値で
昇格させません。正式評価はCheckpoint Bを初めて満たしたrunで全有効サンプルを使って一度だけ
実行します。Checkpoint Aは診断専用で、結果を見てSESOI、方向、Horizon、Bの必要数を変えません。

## Entry、時間軸、Look-ahead

- `T_PLUS_1_OPEN`: BREAKOUTをT終値で確認し、次営業日のOpenで入るPrimaryモデル。
- `T_CLOSE_REFERENCE`: T終値の参考比較。`REFERENCE_ONLY`であり実行戦略・推奨根拠にしません。
- `PIVOT_STOP`: T開始前に既知のPivotへ逆指値を置いた別のExperimentalモデル。

Open起点の銘柄とBenchmarkはともにOpenを基準にし、Close起点はともにCloseを基準にします。
Pivot Stopの同じ日足でEntry triggerとStopの双方へ到達し、順序が日足から分からない場合は
`PATH_AMBIGUOUS`としてPrimary outcomeから除外します。最新情報で過去Event、Pivot、Controlを
作り直しません。

## H3: DifferenceとEquivalence

H3-v1の固定Claimは`DIFFERENCE`です。Primary testはtwo-sided zero-difference test、Familyへ渡す
p値は`difference_primary_p`です。効果の絶対値が固定SESOI以上、95% CIが0を跨がず、Holm補正後
p<0.05で、さらにHIGH costでも方向が反転しない場合だけ`CONFIRMED`になれます。

将来のEquivalence VersionではClaimをHoldoutを見る前に`EQUIVALENCE`へ固定します。この場合は
TOSTの`equivalence_primary_p`だけをFamilyのPrimary pとしてHolm補正します。95% CI全体が
[-SESOI,+SESOI]内にあるかは、p値とは別の`equivalence_ci_containment_gate`です。両方とHIGH cost
robustnessが通った場合だけ`EQUIVALENCE_CONFIRMED`です。「通常の差の検定が非有意」を同等性と
みなしたり、結果を見てDifferenceへ切り替えたりしません。1 Hypothesis VersionがFamilyへ渡す
Primary pは常に1個です。

## Costと多重性

Cost Model `research-cost-v1`は片道相当のLOW 0.04pp、BASE 0.10pp、HIGH 0.25ppです。Primaryは
BASE、LOW/HIGHは感度分析です。incremental cost uncertaintyは0.105pp、最終SESOIは
`max(0.30pp, 2 × uncertainty)`で0.30pp（R単位0.21R）に固定しました。

Familyはplanned n=4のHolm法を使います。Family Close前は
`AWAITING_FAMILY_CORRECTION`です。B未到達のmemberは`INSUFFICIENT_SAMPLE`かつraw p=NULLのまま
planned family sizeに残ります。実行された各Primary testのみを補正対象にし、未実行testを都合よく
除外してnを縮めません。

最終状態は`CONFIRMED`、`EQUIVALENCE_CONFIRMED`、`INCONCLUSIVE`、`CONTRADICTED`、
`INSUFFICIENT_SAMPLE`です。反対方向も、効果量・95% CI・Holmの全Gateが通る場合だけ
`CONTRADICTED`です。

## 日足診断と統計

Overnight gap、Gap/ATR、Gap/R、Breakout-day range/ATR、PATH_AMBIGUOUS率、日中False Breakout率を
診断出力します。率はWilson 95% CI、全体推定は日付クラスタBootstrap 95% CIです。Phase 1では
これらの診断から利益最大化閾値を作りません。

## 保存量、性能、公開

Research処理時間、全workflowに占める比率、Control matching時間を毎run保存します。警告は
Research比率20%超またはmatching 60秒超です。履歴は20営業日まではHot、到達後は1/5/10/20日の
固定点へ圧縮しますが、Event snapshotとControl membershipは永久保持します。

保存量はValidation比30%未満かつ180日予測250MB未満をGREEN、30〜50%または250〜500MBを
YELLOW、それ以上（または日次5MB超継続）をREDとします。raw stateはGitへcommitせず、既存の
公開state seed方式で日次継続します。公開入口は`research/index.json`で、events、performance、
checkpoints、intraday diagnostics、storage/performance telemetryへ分離されています。
