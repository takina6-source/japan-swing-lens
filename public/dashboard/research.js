(() => {
  'use strict';

  const FILES = [
    'index', 'hypotheses', 'families', 'checkpoints',
    'intraday_diagnostics', 'storage_metrics', 'performance_metrics',
  ];
  const PRIMARY_FILES = new Set(['index', 'hypotheses', 'families']);
  const HELP = {
    holdout: ['正式検証用データ', 'Holdout', '仮説を作るときに見ていないデータです。正式な判断には、LIVE_FORWARDかつHOLDOUTの観測だけを使います。'],
    checkpoint: ['次の確認地点', 'Checkpoint', '必要なデータ量をあらかじめ固定した確認地点です。Aは途中経過、Bは一度だけ行う正式検証です。'],
    family: ['仮説群の確定', 'Family Close', 'H1〜H4をまとめて最終評価する日です。それまでは、個別結果を最終確定として表示しません。'],
    sesoi: ['最小限意味のある差', 'SESOI', '統計上の差だけでなく、実務で意味があると事前に決めた最小の差です。'],
    holm: ['多重比較補正', 'Holm correction', '複数の仮説を同時に調べることで偶然の当たりが増えないようにする補正です。UIでは再計算しません。'],
    legacy: ['探索用の過去データ', 'Reconstructed Legacy', '過去データから再構築した探索・参考用データです。正式検証の件数には加えません。'],
    live: ['今後集める観測', 'Live Forward', 'Freeze後に日々の処理で新しく記録されたデータです。HOLDOUTと組み合わさった場合だけ正式検証に使えます。'],
    path: ['日足内の順序不明', 'PATH_AMBIGUOUS', '日足だけでは、1日の中で「先に買値へ到達したか」「先に損切り価格へ到達したか」を判定できないケースです。'],
    h3: ['差と同等性', 'Difference / Equivalence', '「差が有意でない」だけでは同等とはいえません。同等性には専用検定が必要です。結果を見て検証方式を切り替えず、HIGH Costで方向が逆転する場合は確認成立にしません。'],
    diagnostics: ['日足診断', 'Intraday Diagnostics', '日足から確認できるギャップや値幅、日中の順序が分からない割合を診断します。投資判断を自動化する値ではありません。'],
    formal: ['正式判定対象', 'Formal Validation Eligibility', '正式判定に使用できるのは、LIVE_FORWARDかつHOLDOUTのデータだけです。Legacyや対象外データは加算しません。'],
  };
  const STATUS = {
    COLLECTING_HOLDOUT: ['◷', 'データ収集中'],
    AWAITING_FAMILY_CORRECTION: ['◷', '多重比較の確定待ち'],
    CONFIRMED: ['✓', '仮説を確認できた'],
    EQUIVALENCE_CONFIRMED: ['✓', '実務上同等と確認できた'],
    INCONCLUSIVE: ['!', '結論を出せない'],
    CONTRADICTED: ['!', '反対方向の結果を確認'],
    INSUFFICIENT_SAMPLE: ['!', 'サンプル不足'],
    FROZEN_COLLECTING_HOLDOUT: ['◷', 'データ収集中'],
    CLOSED: ['✓', '最終評価完了'],
  };
  const QUESTIONS = {
    H1: ['手法の合致が4/5以上なら、3/5の銘柄よりその後も強いか', 'Core Alignment Strength Hypothesis'],
    H2: ['5/5で待機してから上抜けた銘柄は、その後も比較対象より強いか', '5/5 WATCH → BREAKOUT Hypothesis'],
    H3: ['Pivot逆指値と翌営業日始値では、結果に差があるか', 'Pivot Stop vs T+1 Open Entry Hypothesis'],
    H4: ['5/5で上抜けた銘柄は、4/5より失敗が少ないか', '5/5 vs 4/5 Failed Breakout Hypothesis'],
  };
  const CONDITION_LABELS = {
    events: 'Event数', paired_events: '組み合わせEvent数', unique_stocks: '銘柄数',
    unique_dates: '取引日数', group_3: '3/5グループ', group_4: '4/5グループ',
    group_4plus: '4/5以上グループ', group_5: '5/5グループ', effective_n: '有効Sample数',
  };
  const state = { loaded: false, loading: false, mode: 'simple', data: {}, failures: [] };

  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]);
  const present = value => value !== null && value !== undefined && value !== '';
  const number = (value, digits = 0) => present(value)
    ? Number(value).toLocaleString('ja-JP', { maximumFractionDigits: digits }) : 'N/A';
  const date = value => present(value) ? esc(String(value).slice(0, 10)) : 'N/A';
  const percent = (value, ratio = false) => present(value)
    ? `${number(Number(value) * (ratio ? 100 : 1), 1)}%` : '未計算';
  const bytes = value => present(value) ? `${number(Number(value) / 1024 / 1024, 2)} MB` : 'N/A';
  const valueText = value => {
    if (!present(value)) return 'N/A';
    if (typeof value === 'object') return esc(JSON.stringify(value));
    return esc(value);
  };
  const help = key => `<button class="research-help" type="button" data-research-help="${esc(key)}" aria-label="${esc(HELP[key]?.[0] || key)}の説明を開く" aria-expanded="false">?</button>`;
  const progressBar = (current, total) => {
    const known = present(current) && present(total) && Number(total) > 0;
    const width = known ? Math.max(0, Math.min(100, Number(current) / Number(total) * 100)) : 0;
    return `<div class="research-bar" role="progressbar" aria-valuemin="0" aria-valuemax="${known ? esc(total) : 100}" aria-valuenow="${known ? esc(current) : 0}" aria-label="進捗"><span style="width:${width}%"></span></div>`;
  };
  const statusInfo = code => STATUS[code] || ['?', '未対応状態'];
  const statusMarkup = (code, familyClosed = false) => {
    const [icon, label] = statusInfo(code);
    const finalCodes = new Set(['CONFIRMED', 'EQUIVALENCE_CONFIRMED', 'CONTRADICTED']);
    const visibleLabel = finalCodes.has(code) && !familyClosed ? '正式確定前' : label;
    return `<strong><span aria-hidden="true">${icon}</span> ${esc(visibleLabel)}</strong><small>${esc(code || 'UNKNOWN_STATE')}</small>`;
  };

  function currentView() {
    return location.hash.toLowerCase() === '#research' ? 'research' : 'ranking';
  }

  function showView(view, push = false) {
    const research = view === 'research';
    const rankingPanel = document.querySelector('#rankingView');
    const researchPanel = document.querySelector('#researchView');
    const rankingTab = document.querySelector('#rankingTab');
    const researchTab = document.querySelector('#researchTab');
    if (!rankingPanel || !researchPanel || !rankingTab || !researchTab) return;
    rankingPanel.hidden = research;
    researchPanel.hidden = !research;
    rankingTab.classList.toggle('active', !research);
    researchTab.classList.toggle('active', research);
    rankingTab.setAttribute('aria-selected', String(!research));
    researchTab.setAttribute('aria-selected', String(research));
    rankingTab.tabIndex = research ? -1 : 0;
    researchTab.tabIndex = research ? 0 : -1;
    if (push) {
      const target = research ? '#research' : `${location.pathname}${location.search}`;
      history.pushState({ view }, '', target);
    }
    if (research && !state.loaded && !state.loading) loadResearch();
    document.title = research ? 'Research進捗 | 日本株 Swing Lens' : '日本株 Swing Lens';
  }

  async function loadResearch(force = false) {
    if (state.loading) return;
    state.loading = true;
    if (force) state.loaded = false;
    const root = document.querySelector('#researchRoot');
    root.innerHTML = '<section class="research-loading"><span class="research-spinner" aria-hidden="true"></span><p>Researchデータを読み込んでいます…</p></section>';
    const results = await Promise.allSettled(FILES.map(async name => {
      const response = await fetch(`./research/${name}.json`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`${name}: HTTP ${response.status}`);
      return [name, await response.json()];
    }));
    state.data = {};
    state.failures = [];
    results.forEach((result, index) => {
      const name = FILES[index];
      if (result.status === 'fulfilled') state.data[result.value[0]] = result.value[1];
      else state.failures.push(name);
    });
    state.loading = false;
    const missingPrimary = state.failures.filter(name => PRIMARY_FILES.has(name));
    if (missingPrimary.length === PRIMARY_FILES.size) {
      root.innerHTML = `<section class="research-error"><div><h1>Researchデータを読み込めませんでした。</h1><p>銘柄ランキングはそのまま利用できます。</p></div><button class="research-retry" type="button">再読み込み</button></section>`;
      return;
    }
    state.loaded = true;
    render();
  }

  function render() {
    try {
      const root = document.querySelector('#researchRoot');
      root.className = state.mode === 'detail' ? 'research-view-detail' : 'research-view-simple';
      root.innerHTML = `${headerMarkup()}${partialMarkup()}${summaryMarkup()}${hypothesesMarkup()}${dataQualityMarkup()}${entryModelsMarkup()}${diagnosticsMarkup()}${systemMarkup()}${footerMarkup()}`;
    } catch (error) {
      document.querySelector('#researchRoot').innerHTML = `<section class="research-error"><div><h1>Research画面を表示できませんでした。</h1><p>銘柄ランキングには影響していません。</p></div><button class="research-retry" type="button">再読み込み</button></section>`;
    }
  }

  function headerMarkup() {
    return `<header class="research-header"><div><p class="eyebrow">RESEARCH VIEWER｜検証の進捗</p><h1>Research進捗</h1><p>何を調べ、どこまでデータが集まり、まだ何を結論にしてはいけないかを確認する画面です。</p></div><div class="research-mode" role="group" aria-label="Researchの表示量"><button type="button" data-research-mode="simple" class="${state.mode === 'simple' ? 'active' : ''}" aria-pressed="${state.mode === 'simple'}">かんたん</button><button type="button" data-research-mode="detail" class="${state.mode === 'detail' ? 'active' : ''}" aria-pressed="${state.mode === 'detail'}">詳細</button></div></header>`;
  }

  function partialMarkup() {
    if (!state.failures.length) return '';
    return `<div class="research-notice research-partial" role="status"><span aria-hidden="true">!</span><div><strong>一部のResearchデータを表示できません</strong><br>${esc(state.failures.join(', '))} はN/Aとして表示しています。ランキングには影響しません。</div></div>`;
  }

  function summaryMarkup() {
    const index = state.data.index || {};
    const family = Array.isArray(state.data.families) ? state.data.families[0] || {} : {};
    const familyResult = family.result || {};
    const code = familyResult.family_status || family.family_status || 'UNKNOWN_STATE';
    const session = index.trading_session_progress || {};
    const origin = index.event_data_origin_counts || {};
    const phase = index.event_evaluation_phase_counts || {};
    const elapsed = session.elapsed;
    const total = session.total || 90;
    return `<section aria-labelledby="researchSummaryTitle"><div class="research-summary"><div class="research-summary-main"><div class="research-status-line"><div><small id="researchSummaryTitle">現在のResearch状態</small><div class="research-status">${statusMarkup(code, familyResult.family_status === 'CLOSED')}</div><div class="research-status-code">Family Registryの状態をそのまま表示</div></div>${help('family')}</div><div class="research-progress"><div class="research-progress-head"><div><small>正式検証期間（Holdout）</small><strong>90取引日の進捗</strong></div><span>${present(elapsed) ? number(elapsed) : 'N/A'} / ${number(total)}取引日</span></div>${progressBar(elapsed, total)}</div><div class="research-dates"><div><small>仮説を固定した日（Freeze）</small><strong>${date(family.family_freeze_date)}</strong></div><div><small>正式検証の開始（Holdout）</small><strong>${date(family.family_holdout_start)}</strong></div><div><small>仮説群の確定予定（Family Close）</small><strong>${date(family.family_close_at)}</strong></div></div></div><div class="research-counts"><div><small>Research Event</small><strong>${number(index.event_count)}</strong></div><div><small>ValidationSubject</small><strong>${number(index.validation_subject_count)}</strong></div><div><small>今後集める観測（LIVE_FORWARD）</small><strong>${number(origin.LIVE_FORWARD)}</strong></div><div class="legacy"><small>探索用の過去データ（RECONSTRUCTED_LEGACY）</small><strong>${number(origin.RECONSTRUCTED_LEGACY)}</strong></div><div><small>判定対象外（NOT_ELIGIBLE）</small><strong>${number(origin.NOT_ELIGIBLE)}</strong></div><div><small>探索（DISCOVERY）</small><strong>${number(phase.DISCOVERY)}</strong></div><div class="formal wide"><small>正式判定対象（LIVE_FORWARD × HOLDOUT）</small><strong>${number(index.formal_validation_event_count)} Event</strong></div></div></div><div class="research-notice"><span aria-hidden="true">✓</span><div><strong>正式判定に使用できるのは、LIVE_FORWARD かつ HOLDOUT のデータだけです。</strong><br>過去データ（Legacy）は探索・参考用で、正式件数には加えていません。</div>${help('formal')}</div></section>`;
  }

  function hypothesesMarkup() {
    const hypotheses = Array.isArray(state.data.hypotheses) ? state.data.hypotheses : [];
    if (!hypotheses.length) return `<section class="research-section"><div class="research-section-head"><div><p class="eyebrow">HYPOTHESES｜調べていること</p><h2>仮説データなし</h2></div></div></section>`;
    return `<section class="research-section" aria-labelledby="hypothesesTitle"><div class="research-section-head"><div><p class="eyebrow">HYPOTHESES｜調べていること</p><h2 id="hypothesesTitle">仮説ごとの進捗</h2></div><p>Checkpoint Aは途中経過、Bは一度だけ行う正式検証です。件数がそろっても、画面側では結論を作りません。</p></div><div class="hypothesis-list">${hypotheses.map(hypothesisMarkup).join('')}</div></section>`;
  }

  function hypothesisMarkup(hypothesis) {
    const id = hypothesis.hypothesis_id || 'H?';
    const [question, original] = QUESTIONS[id] || [hypothesis.definition || '問いの説明がありません', hypothesis.hypothesis_version || 'Hypothesis'];
    const progress = hypothesis.progress || {};
    const holdout = progress.holdout || {};
    const family = Array.isArray(state.data.families) ? state.data.families[0] || {} : {};
    const familyClosed = family.result?.family_status === 'CLOSED';
    const status = progress.current_status || 'UNKNOWN_STATE';
    const sample = present(holdout.effective_n) ? holdout.effective_n : holdout.total_n;
    const a = checkpointSummary(hypothesis, 'A');
    const b = checkpointSummary(hypothesis, 'B');
    const effect = present(holdout.effect_estimate) ? `${number(holdout.effect_estimate, 2)} pp` : '未計算';
    const judgement = judgementText(status, familyClosed);
    return `<article class="hypothesis-card" data-hypothesis="${esc(id)}"><div class="hypothesis-top"><div class="hypothesis-id">${esc(id)}</div><div class="hypothesis-title"><h3>${esc(question)}</h3><p>${esc(original)}</p></div><div class="hypothesis-status">${statusMarkup(status, familyClosed)}</div></div><div class="hypothesis-overview"><div><small>正式検証Sample</small><strong>${number(sample)}件</strong></div><div><small>Checkpoint Aまで</small><strong>${a.short}</strong></div><div><small>Checkpoint Bまで</small><strong>${b.short}</strong></div><div><small>現在の効果推定</small><strong>${effect}</strong></div><div class="judgement"><small>現段階で判断可能か</small><strong>${esc(judgement)}</strong></div></div><p class="hypothesis-note">${familyClosed ? 'Research Layerの確定結果を表示しています。' : 'Family Close前のため、最終結論として扱えません。'}</p>${id === 'H3' ? h3Markup(hypothesis) : ''}<div class="checkpoint-grid">${checkpointMarkup(hypothesis, 'A')}${checkpointMarkup(hypothesis, 'B')}</div>${hypothesisDetails(hypothesis)}</article>`;
  }

  function checkpointSummary(hypothesis, name) {
    const minimums = hypothesis.checkpoint_schedule?.[name]?.minimums || {};
    const actual = hypothesis.progress?.holdout || {};
    const rows = Object.entries(minimums).map(([key, minimum]) => {
      const known = present(actual[key]);
      const current = known ? Number(actual[key]) : null;
      return { key, current, minimum: Number(minimum), known,
        ratio: known && minimum ? current / minimum : -1,
        remaining: known ? Math.max(0, Number(minimum) - current) : null };
    });
    if (!rows.length) return { rows: [], weakest: null, short: 'N/A' };
    const weakest = [...rows].sort((left, right) => left.ratio - right.ratio)[0];
    const short = !weakest.known ? `${CONDITION_LABELS[weakest.key] || weakest.key} N/A`
      : weakest.remaining > 0 ? `${CONDITION_LABELS[weakest.key] || weakest.key} あと${number(weakest.remaining)}`
        : '必要数を収集済み';
    return { rows, weakest, short };
  }

  function checkpointMarkup(hypothesis, name) {
    const info = checkpointSummary(hypothesis, name);
    const checkpoint = (hypothesis.progress?.checkpoints || []).find(row => row.checkpoint_name === name);
    const meaning = name === 'A' ? '途中経過の確認' : '一度だけ行う正式検証';
    const weakestText = info.weakest
      ? info.weakest.known
        ? `収集が最も遅い条件：${esc(CONDITION_LABELS[info.weakest.key] || info.weakest.key)} あと${number(info.weakest.remaining)}（達成率 ${percent(info.weakest.ratio, true)}）`
        : `収集が最も遅い条件：${esc(CONDITION_LABELS[info.weakest.key] || info.weakest.key)} データなし`
      : '';
    return `<section class="checkpoint"><h4>Checkpoint ${name}</h4><small>${meaning}</small>${info.rows.map(row => `<div class="checkpoint-condition"><div class="checkpoint-condition-head"><span>${esc(CONDITION_LABELS[row.key] || row.key)}</span><strong>${number(row.current)} / ${number(row.minimum)}</strong></div>${progressBar(row.current, row.minimum)}</div>`).join('')}${weakestText ? `<p class="checkpoint-weak">${weakestText}</p>` : ''}<p class="checkpoint-registry">Registry記録：${checkpoint ? esc(checkpoint.result || '記録あり') : '未到達'}</p></section>`;
  }

  function judgementText(status, familyClosed) {
    if (!familyClosed) {
      if (status === 'AWAITING_FAMILY_CORRECTION') return '正式検証済み・Family Close待ち';
      return 'まだ正式判断できません';
    }
    return statusInfo(status)[1];
  }

  function h3Markup(hypothesis) {
    return `<section class="h3-callout"><div class="diagnostic-value"><div><h4>現在の検証方式：差があるか（Difference）</h4><p><b>${esc(hypothesis.hypothesis_version || 'H3-v1')}はDIFFERENCEとして固定済みです。</b></p></div>${help('h3')}</div><p>Difference：2つの方法に差があるかを調べる検証</p><p>Equivalence：2つの方法が実務上ほぼ同じと言えるかを調べる専用検証</p></section>`;
  }

  function hypothesisDetails(hypothesis) {
    const progress = hypothesis.progress || {};
    const holdout = progress.holdout || {};
    const formal = progress.formal_result || {};
    const rows = [
      ['hypothesis_id', hypothesis.hypothesis_id], ['hypothesis_version', hypothesis.hypothesis_version],
      ['freeze status', hypothesis.freeze_status], ['freeze date', hypothesis.frozen_at],
      ['evaluation mode', hypothesis.analysis_method], ['primary metric', hypothesis.primary_metric],
      ['effect estimate', holdout.effect_estimate], ['95% CI lower', holdout.ci_95_lower],
      ['95% CI upper', holdout.ci_95_upper], ['p-value', formal.raw_primary_p],
      ['SESOI', hypothesis.sesoi], ['eligibility', holdout.eligible_rule],
      ['family', hypothesis.correction_family], ['cost model', hypothesis.cost_model],
      ['decision state', progress.final_decision], ['definition hash', hypothesis.definition_hash],
    ];
    return `<details class="research-details research-detail-only"><summary>Registry・統計の詳細を開く</summary><p class="hypothesis-note">Checkpoint B前の数値は正式判定前の参考値です。</p><dl class="research-detail-grid">${rows.map(([key, value]) => `<div><dt>${esc(key)}</dt><dd>${valueText(value)}</dd></div>`).join('')}</dl></details>`;
  }

  function dataQualityMarkup() {
    const index = state.data.index || {};
    const origin = index.event_data_origin_counts || {};
    const phase = index.event_evaluation_phase_counts || {};
    const items = [
      ['今後集める観測', 'LIVE_FORWARD', origin.LIVE_FORWARD, 'Freeze後に新しく記録したデータ', 'formal'],
      ['探索用の過去データ', 'RECONSTRUCTED_LEGACY', origin.RECONSTRUCTED_LEGACY, '過去データを使った探索・参考用。正式件数には加算しません', 'legacy'],
      ['判定対象外', 'NOT_ELIGIBLE', origin.NOT_ELIGIBLE, '必要条件を満たさず正式判定に使わないデータ', ''],
      ['仮説を考える探索', 'DISCOVERY', phase.DISCOVERY, '仮説を考えるための探索データ', ''],
      ['正式検証用', 'HOLDOUT', phase.HOLDOUT, '仮説を正式に検証するための未使用データ', 'formal'],
      ['正式判定後の観測', 'POST_CONFIRMATION', phase.POST_CONFIRMATION, '正式判定後に追加された観測データ', ''],
    ];
    return `<section class="research-section" aria-labelledby="dataQualityTitle"><div class="research-section-head"><div><p class="eyebrow">DATA QUALITY｜データの役割</p><h2 id="dataQualityTitle">正式データと参考データを分ける</h2></div><p>Legacyが多くても、正式検証が進んだようには表示しません。</p></div><div class="research-panel"><div class="quality-grid-research">${items.map(([jp, en, value, description, cls]) => `<div class="quality-item ${cls}"><small>${esc(jp)}<br>${esc(en)}</small><strong>${number(value)}</strong><p>${esc(description)}</p></div>`).join('')}</div></div></section>`;
  }

  function entryModelsMarkup() {
    return `<section class="research-section" aria-labelledby="entryModelsTitle"><div class="research-section-head"><div><p class="eyebrow">ENTRY MODELS｜入り方の比較</p><h2 id="entryModelsTitle">3つのEntry Model</h2></div><p>同じ銘柄でも「いつ・どの価格で入ったと考えるか」を分けて検証します。</p></div><div class="research-panel"><div class="research-card-grid"><article class="research-mini-card primary"><h3>翌営業日始値</h3><span class="original">T+1 Open｜Primary Entry Model</span><strong>標準モデル</strong><p>シグナルの翌営業日、始値で入る前提です。</p></article><article class="research-mini-card"><h3>当日終値・参考値</h3><span class="original">T Close Reference</span><strong>参考比較のみ</strong><p>当日の終値で入れたと仮定した比較値で、実行可能な売買戦略ではありません。</p></article><article class="research-mini-card experimental"><h3>Pivot逆指値</h3><span class="original">Pivot Stop｜Experimental Entry Model</span><strong>実験モデル</strong><p>Pivot到達を入口とする別モデルです。日足内の順序が分からない場合があります。</p></article></div><div class="research-notice"><span aria-hidden="true">!</span><div><strong>PATH_AMBIGUOUS</strong><br>日足だけでは買値と損切り価格のどちらへ先に到達したか判定できないケースです。</div>${help('path')}</div><p class="hypothesis-note">このResearchは投資手法の検証を目的とし、特定銘柄の売買を推奨するものではありません。</p></div></section>`;
  }

  function diagnosticsMarkup() {
    const payload = state.data.intraday_diagnostics || {};
    const diagnostics = payload.diagnostics || {};
    const specs = [
      ['gap_pct', '翌朝の価格差', 'Overnight Gap', true, false],
      ['gap_atr', '値動き幅で見たGap', 'Gap / ATR', false, false],
      ['gap_r', '想定損失幅で見たGap', 'Gap / R', false, false],
      ['breakout_range_atr', '上抜け日の値幅', 'Breakout Day Range / ATR', false, false],
      ['path_ambiguous', '日足内の順序不明率', 'PATH_AMBIGUOUS rate', true, true],
      ['false_breakout', '日中の上抜け失敗率', 'Intraday False Breakout rate', true, true],
    ];
    return `<section class="research-section" aria-labelledby="diagnosticsTitle"><div class="research-section-head"><div><p class="eyebrow">INTRADAY DIAGNOSTICS｜日足診断</p><h2 id="diagnosticsTitle">Entry周辺の値動き</h2></div><p>日足から分かる範囲の診断です。閾値の最適化には使用していません。</p></div><div class="research-panel"><div class="diagnostics-grid">${specs.map(spec => diagnosticMarkup(diagnostics[spec[0]], ...spec.slice(1))).join('')}</div><p class="research-detail-only hypothesis-note">Research version：${valueText(payload.research_version)}｜Unique events：${number(payload.unique_events)}｜銘柄：${number(payload.unique_stocks)}｜取引日：${number(payload.unique_dates)}｜Threshold optimized：${valueText(payload.threshold_optimized)}</p></div></section>`;
  }

  function diagnosticMarkup(metric = {}, japanese, original, isPercent, isRatio) {
    const value = isRatio ? percent(metric.rate, true) : isPercent ? percent(metric.mean, false) : (present(metric.mean) ? number(metric.mean, 2) : '未計算');
    const detail = Object.entries(metric || {}).map(([key, item]) => `<div><dt>${esc(key)}</dt><dd>${valueText(item)}</dd></div>`).join('');
    return `<article class="research-mini-card"><div class="diagnostic-value"><div><h3>${esc(japanese)}</h3><span class="original">${esc(original)}</span></div>${original.includes('PATH') ? help('path') : help('diagnostics')}</div><strong>${value}</strong><p class="diagnostic-meta">対象 ${number(metric.count)}件</p>${detail ? `<details class="research-details research-detail-only"><summary>診断値の詳細</summary><dl class="research-detail-grid">${detail}</dl></details>` : ''}</article>`;
  }

  function systemMarkup() {
    const performance = state.data.performance_metrics || {};
    const storage = state.data.storage_metrics || {};
    const status = storage.budget_status;
    const healthy = performance.warning === false && status === 'GREEN';
    const unknown = !present(performance.warning) && !present(status);
    const label = unknown ? '? 状態を確認できません' : healthy ? '✓ 正常' : `! 要確認（${status || 'Performance warning'}）`;
    return `<section class="research-section" aria-labelledby="systemTitle"><div class="research-section-head"><div><p class="eyebrow">SYSTEM｜処理と保存量</p><h2 id="systemTitle">システム状態</h2></div><p>警告がないときは、かんたん表示では詳細値を省略します。</p></div><div class="research-panel"><div class="system-state ${healthy ? 'ok' : 'warn'}">${esc(label)}</div><div class="system-detail research-detail-only"><div><small>Research処理時間</small><strong>${present(performance.research_total_seconds) ? `${number(performance.research_total_seconds, 2)}秒` : 'N/A'}</strong></div><div><small>Workflowに占める割合</small><strong>${present(performance.research_share_pct) ? `${number(performance.research_share_pct, 2)}%` : 'N/A'}</strong></div><div><small>Control matching時間</small><strong>${present(performance.research_control_seconds) ? `${number(performance.research_control_seconds, 2)}秒` : 'N/A'}</strong></div><div><small>Research state容量</small><strong>${bytes(storage.research_state_bytes)}</strong></div><div><small>Validation state容量</small><strong>${bytes(storage.validation_state_bytes)}</strong></div><div><small>180日予測</small><strong>${present(storage.projected_180d_size_mb) ? `${number(storage.projected_180d_size_mb, 2)} MB` : 'N/A'}</strong></div><div><small>容量判定</small><strong>${valueText(status)}</strong></div></div></div></section>`;
  }

  function footerMarkup() {
    const generated = state.data.index?.generated_at;
    return `<footer><div class="research-downloads research-detail-only">${FILES.map(name => `<a href="./research/${name}.json">${esc(name)}.json</a>`).join('')}</div><p class="research-updated">最終更新：${present(generated) ? esc(generated) : 'N/A'}<br>Research ViewerはResearch Layerの出力を表示するだけで、判定を再計算しません。</p></footer>`;
  }

  function openHelp(key, button) {
    const info = HELP[key];
    if (!info) return;
    document.querySelectorAll('[data-research-help][aria-expanded="true"]').forEach(item => item.setAttribute('aria-expanded', 'false'));
    button?.setAttribute('aria-expanded', 'true');
    const sheet = document.querySelector('#termSheet');
    document.querySelector('#termTitle').textContent = info[0];
    document.querySelector('#termOriginal').textContent = info[1];
    document.querySelector('#termDescription').textContent = info[2];
    sheet.hidden = false;
    document.querySelector('#termClose').focus();
  }

  function init() {
    document.querySelectorAll('.screen-tab').forEach(tab => {
      tab.addEventListener('click', () => showView(tab.dataset.screen, true));
      tab.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
        event.preventDefault();
        const next = tab.dataset.screen === 'ranking' ? 'research' : 'ranking';
        showView(next, true);
        document.querySelector(`#${next}Tab`)?.focus();
      });
    });
    addEventListener('popstate', () => showView(currentView()));
    addEventListener('hashchange', () => showView(currentView()));
    document.addEventListener('click', event => {
      const retry = event.target.closest('.research-retry');
      if (retry) return void loadResearch(true);
      const mode = event.target.closest('[data-research-mode]');
      if (mode) {
        state.mode = mode.dataset.researchMode === 'detail' ? 'detail' : 'simple';
        return void render();
      }
      const helpButton = event.target.closest('[data-research-help]');
      if (helpButton) {
        event.preventDefault();
        event.stopPropagation();
        return void openHelp(helpButton.dataset.researchHelp, helpButton);
      }
      if (event.target.id === 'termClose' || event.target.id === 'termSheet') {
        document.querySelectorAll('[data-research-help]').forEach(item => item.setAttribute('aria-expanded', 'false'));
      }
    });
    showView(currentView());
  }

  window.ResearchViewer = { init, loadResearch, showView, render, state, checkpointSummary };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
