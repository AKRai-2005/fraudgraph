/* Fraud Investigation Console -- vanilla JS, no CDN, works offline. */
'use strict';

const API = '';
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = (n) => '$' + Number(n || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct = (n) => (Number(n || 0) * 100).toFixed(0) + '%';

const state = { queue: [], current: null, graph: null, sort: { key: 'fraud_probability', dir: -1 } };

async function api(path, opts) {
  const r = await fetch(API + path, Object.assign({ headers: { 'content-type': 'application/json' } }, opts));
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) { /* keep statusText */ }
    throw new Error(msg);
  }
  return r.json();
}

function toast(msg, ms = 3200) {
  const t = $('#toast');
  t.textContent = msg;
  t.style.display = 'block';
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.style.display = 'none'; }, ms);
}

const probColor = (p) => p >= 0.7 ? 'var(--fraud)' : p >= 0.3 ? 'var(--uncertain)' : 'var(--legit)';

/* =========================================================== navigation */
$$('#tabs button').forEach((b) => b.addEventListener('click', () => show(b.dataset.view)));

function show(view) {
  $$('#tabs button').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
  $$('.view').forEach((v) => v.classList.toggle('active', v.id === 'view-' + view));
  if (view === 'memory') loadMemory();
  if (view === 'model') loadModel();
  if (view === 'overview') loadOverview();
}

/* ============================================================== health */
async function loadHealth() {
  const strip = $('#statusStrip');
  try {
    const h = await api('/api/health');
    const g = h.graph || {};
    const tgLive = g.backend === 'tigergraph' && g.ok;
    const parts = [
      `<span class="pill ${tgLive ? 'ok' : 'warn'}">Graph <b>${esc(g.backend)}</b>${g.ok ? '' : ' (down)'}</span>`,
      `<span class="pill">Transactions <b>${Number(g.transactions || 0).toLocaleString()}</b></span>`,
      `<span class="pill">Closed cases <b>${Number(g.closed_cases || 0).toLocaleString()}</b></span>`,
      `<span class="pill ${h.llm.enabled ? 'ok' : ''}">LLM <b>${h.llm.enabled ? esc(h.llm.model) : 'off (templates)'}</b></span>`,
      `<span class="pill warn">Actions <b>simulated</b></span>`,
    ];
    if (!tgLive) {
      parts.push(`<span class="pill warn" title="TigerGraph is the system of record; the local mirror serves the same query catalogue for development.">TigerGraph <b>${h.tigergraph_configured ? 'configured, unreachable' : 'not configured'}</b></span>`);
    }
    strip.innerHTML = parts.join('');
  } catch (e) {
    strip.innerHTML = `<span class="pill bad">Backend unreachable: ${esc(e.message)}</span>`;
  }
}

/* ============================================================ overview */
async function loadOverview() {
  let o;
  try { o = await api('/api/overview'); } catch (e) { return; }
  $('#kpis').innerHTML = [
    kpi(o.cases_investigated, 'Investigations', `${o.total_alerts_in_pack} alerts in the case pack`),
    kpi(o.closed_fraud, 'Closed &ndash; fraud', 'verdict fraud, evidence sufficient'),
    kpi(o.closed_legitimate, 'Closed &ndash; legitimate', 'alert not corroborated by the graph'),
    kpi(o.escalated + o.open, 'Open or escalated', 'awaiting a human decision'),
    kpi(o.awaiting_approval, 'Actions awaiting approval', 'L1 / L2 routes, not executed'),
    kpi(o.sar_filings_recommended, 'SARs recommended', 'regulatory filing, L2 approval'),
    kpi(money(o.total_exposure_usd), 'Total exposure', 'sum over identified episodes'),
    kpi(o.written_to_graph + '/' + o.cases_investigated, 'Written to graph', 'cases persisted as memory'),
  ].join('');

  const dist = o.risk_distribution || {};
  const max = Math.max(1, ...Object.values(dist));
  $('#riskDist').innerHTML = Object.entries(dist).map(([band, n]) => {
    const lo = parseFloat(band.split('-')[0]);
    return `<div class="dist-row"><span class="muted">${esc(band)}</span>
      <span class="bar"><i style="width:${(n / max) * 100}%;background:${probColor(lo)}"></i></span>
      <span class="n">${n}</span></div>`;
  }).join('');

  const chips = (obj, cls) => Object.entries(obj || {})
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `<span class="tag ${cls ? esc(k) : ''}">${esc(k)} &middot; ${v}</span>`).join('');
  $('#verdictSplit').innerHTML =
    `<div style="width:100%"><div class="muted" style="margin-bottom:6px">Verdict</div>${chips(o.by_verdict, true)}</div>
     <div style="width:100%;margin-top:12px"><div class="muted" style="margin-bottom:6px">Pattern identified</div>${chips(o.by_pattern)}</div>
     <div style="width:100%;margin-top:12px"><div class="muted" style="margin-bottom:6px">Case status</div>${chips(o.by_status, true)}</div>`;

  $('#recentActivity').innerHTML = (o.recent_activity || []).map((a) =>
    `<div class="act"><span class="cid" data-case="${esc(a.case_id)}">${esc(a.case_id)}</span>
     <span class="kind">${esc(a.kind)}</span><span class="d">${esc(a.detail)}</span></div>`).join('')
    || '<div class="muted">No investigations have been run yet.</div>';
  $$('#recentActivity .cid').forEach((el) =>
    el.addEventListener('click', () => openCase(el.dataset.case)));
}

const kpi = (v, l, s) => `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div><div class="s">${s}</div></div>`;

/* =============================================================== queue */
async function loadQueue() {
  state.queue = await api('/api/queue');
  const sel = $('#casePicker');
  sel.innerHTML = state.queue.map((r) =>
    `<option value="${esc(r.case_id)}">${esc(r.case_id)} &mdash; ${esc(r.verdict)} p=${Number(r.fraud_probability).toFixed(2)} &mdash; ${esc(r.card_id)}</option>`).join('');
  renderQueue();
}

function renderQueue() {
  const q = $('#queueSearch').value.trim().toLowerCase();
  const st = $('#queueStatus').value;
  const vd = $('#queueVerdict').value;
  const onlyAppr = $('#queueApproval').checked;
  let rows = state.queue.filter((r) => {
    if (st && r.status !== st) return false;
    if (vd && r.verdict !== vd) return false;
    if (onlyAppr && !(r.awaiting_approval || []).length) return false;
    if (!q) return true;
    return [r.case_id, r.card_id, r.customer_id, r.pattern, r.next_action, r.flagged_txn_id]
      .join(' ').toLowerCase().includes(q);
  });
  const { key, dir } = state.sort;
  rows.sort((a, b) => {
    const x = a[key], y = b[key];
    if (x == null) return 1;
    if (y == null) return -1;
    return (typeof x === 'number' ? x - y : String(x).localeCompare(String(y))) * dir;
  });
  $('#queueTable tbody').innerHTML = rows.map((r) => `
    <tr data-case="${esc(r.case_id)}">
      <td class="mono">${esc(r.case_id)}</td>
      <td><span class="tag">${esc((r.trigger_type || '').replace('_', ' '))}</span></td>
      <td class="mono">${esc(r.card_id)}</td>
      <td class="num">${r.bank_risk_score == null ? '&mdash;' : Number(r.bank_risk_score).toFixed(2)}</td>
      <td class="num"><span class="prob"><i class="swatch" style="background:${probColor(r.fraud_probability)}"></i>${Number(r.fraud_probability).toFixed(2)}</span></td>
      <td class="num">${r.confidence == null ? '&mdash;' : Number(r.confidence).toFixed(2)}</td>
      <td><span class="tag ${esc(r.verdict)}">${esc(r.verdict)}</span></td>
      <td>${esc(r.pattern)}</td>
      <td class="num">${r.exposure_usd ? money(r.exposure_usd) : '&mdash;'}</td>
      <td class="mono">${esc(r.next_action || '&mdash;')}</td>
      <td>${(r.awaiting_approval || []).length ? `<span class="tag L1">${r.awaiting_approval.length} pending</span>` : '<span class="muted">&mdash;</span>'}</td>
      <td class="muted">${esc(String(r.last_updated || '').slice(0, 16).replace('T', ' '))}</td>
    </tr>`).join('') || '<tr><td colspan="12" class="muted" style="padding:24px;text-align:center">No cases match this filter.</td></tr>';
  $$('#queueTable tbody tr[data-case]').forEach((tr) =>
    tr.addEventListener('click', () => openCase(tr.dataset.case)));
}

['#queueSearch', '#queueStatus', '#queueVerdict', '#queueApproval'].forEach((s) =>
  $(s).addEventListener('input', renderQueue));
$$('#queueTable th[data-sort]').forEach((th) => th.addEventListener('click', () => {
  const k = th.dataset.sort;
  state.sort = { key: k, dir: state.sort.key === k ? -state.sort.dir : -1 };
  renderQueue();
}));

$('#adhocBtn').addEventListener('click', async () => {
  const id = $('#adhocTxn').value.trim();
  if (!/^\d+$/.test(id)) { $('#adhocMsg').textContent = 'Enter a numeric TransactionID.'; return; }
  $('#adhocMsg').innerHTML = '<span class="spinner"></span> investigating&hellip;';
  try {
    const rec = await api('/api/investigate-adhoc', { method: 'POST', body: JSON.stringify({ txn_id: id }) });
    $('#adhocMsg').textContent = '';
    await loadQueue();
    renderCase(rec);
    show('case');
  } catch (e) {
    $('#adhocMsg').textContent = 'Failed: ' + e.message;
  }
});

/* ============================================================ workspace */
$('#casePicker').addEventListener('change', (e) => openCase(e.target.value));
$('#rerunBtn').addEventListener('click', async () => {
  const id = $('#casePicker').value;
  if (!id) return;
  $('#caseMsg').innerHTML = '<span class="spinner"></span> re-running&hellip;';
  try {
    const rec = await api(`/api/cases/${encodeURIComponent(id)}/investigate`, { method: 'POST' });
    $('#caseMsg').textContent = '';
    await loadQueue();
    renderCase(rec);
    toast(`${id} re-investigated`);
  } catch (e) { $('#caseMsg').textContent = 'Failed: ' + e.message; }
});

async function openCase(id) {
  show('case');
  $('#casePicker').value = id;
  $('#caseBody').innerHTML = '<div class="empty"><span class="spinner"></span> loading&hellip;</div>';
  try {
    const rec = await api('/api/cases/' + encodeURIComponent(id));
    renderCase(rec);
  } catch (e) {
    $('#caseBody').innerHTML = `<div class="empty">Could not load ${esc(id)}: ${esc(e.message)}</div>`;
  }
}

function renderCase(rec) {
  state.current = rec;
  const a = rec.answer || {}, c = a.case || {}, risk = rec.risk || {}, trig = rec.trigger || {};
  const nba = a.next_best_actions || {};
  const sar = a.sar || {};

  const left = `
    <div class="panel">
      <div class="panel-head">
        <h2>${esc(rec.case_id)} &middot; <span class="tag ${esc(c.verdict)}">${esc(c.verdict)}</span>
          <span class="tag ${esc(c.status)}">${esc((c.status || '').replace(/_/g, ' '))}</span></h2>
        <span class="muted">${a.tool_calls} graph calls &middot; ${a.latency_s}s &middot; ${a.tokens} LLM tokens</span>
      </div>
      <p class="panel-hint">${esc(trig.trigger_text || '')}</p>
      <div class="summary-box">${esc(c.summary)}</div>
      <div class="facts">
        ${fact('Agent p(fraud)', Number(c.fraud_probability).toFixed(3), probColor(c.fraud_probability))}
        ${fact('Bank model score', risk.bank_risk_score == null ? '&mdash;' : Number(risk.bank_risk_score).toFixed(2))}
        ${fact('Confidence', Number(risk.confidence || 0).toFixed(2))}
        ${fact('Independent signals', risk.independent_signal_count ?? 0)}
        ${fact('Exposure', money(c.exposure_usd))}
        ${fact('Affected txns', (c.affected_txn_ids || []).length)}
        ${fact('Pattern', c.pattern)}
        ${fact('Written to graph', c.written_to_graph ? 'yes' : 'no')}
      </div>
      ${c.pattern_description ? `<div class="warnbox"><b>Undocumented pattern.</b> ${esc(c.pattern_description)}</div>` : ''}
      ${(risk.conflicting_evidence || []).length ? `<div class="warnbox"><b>Conflicting evidence.</b> ${esc(risk.conflicting_evidence.join('; '))}</div>` : ''}
      ${(risk.uncertainty_notes || []).length ? `<div class="panel-hint"><b>Remaining uncertainty:</b> ${esc(risk.uncertainty_notes.join('; '))}</div>` : ''}
    </div>

    <div class="panel">
      <h2>Evidence</h2>
      <p class="panel-hint">Every claim carries the query that produced it and the entities it rests on.</p>
      ${(c.evidence || []).map((e) => `
        <div class="ev source-${esc(e.source)}">
          <div class="claim">${esc(e.claim)}</div>
          <div class="meta">${esc(e.source)} &middot; ${esc(e.ref)}${(e.entity_ids || []).length ? ' &middot; ' + esc(e.entity_ids.slice(0, 8).join(', ')) + (e.entity_ids.length > 8 ? ` +${e.entity_ids.length - 8}` : '') : ''}</div>
        </div>`).join('')}
    </div>

    <div class="panel">
      <h2>Graph relationships</h2>
      <p class="panel-hint">Drag to pan, scroll to zoom, drag a node to pin it. Red ring = flagged transaction; amber = part of the episode.</p>
      <div class="graph-wrap">
        <svg id="graphSvg"></svg>
        <div class="graph-controls">
          <button class="btn ghost small" id="graphReset">Reset</button>
        </div>
        <div class="graph-tip" id="graphTip"></div>
      </div>
      <div class="graph-legend" id="graphLegend"></div>
    </div>

    <div class="panel">
      <h2>Fraud pattern analysis</h2>
      <p class="panel-hint">All detectors run on every case; each states what it cannot rule out.</p>
      ${(rec.findings || []).map((f) => `
        <div class="ev" style="border-left-color:${f.matched ? probColor(f.strength) : 'var(--line)'}">
          <div class="claim"><b>${esc(f.name)}</b> ${f.matched
            ? `<span class="tag">matched &middot; strength ${Number(f.strength).toFixed(2)}</span>`
            : '<span class="tag">no match</span>'} </div>
          <div style="font-size:12.5px;color:var(--text-dim);margin-top:4px">${esc(f.why)}</div>
          ${f.limitations ? `<div class="meta" style="font-family:inherit">Limitation: ${esc(f.limitations)}</div>` : ''}
        </div>`).join('')}
    </div>`;

  const right = `
    <div class="panel">
      <h2>Next best action</h2>
      <p class="panel-hint">The agent may execute <span class="tag auto">auto</span> actions.
        <span class="tag L1">L1</span> and <span class="tag L2">L2</span> wait for a human.</p>
      <div class="nba-cols">
        <div class="nba-col"><h4>Initial &mdash; before requested evidence</h4>
          ${(nba.initial || []).map(actionRow).join('') || '<div class="muted">none</div>'}</div>
        <div class="nba-col"><h4>Final &mdash; after simulated response</h4>
          ${(nba.final || []).map(actionRow).join('') || '<div class="muted">none</div>'}</div>
      </div>
      <div class="changed"><b>What changed:</b> ${esc(nba.what_changed || 'nothing')}</div>
      <div class="panel-hint" style="margin-top:10px"><b>Stop reason:</b> ${esc(a.stop_reason)}</div>
    </div>

    <div class="panel">
      <h2>Approvals &amp; execution</h2>
      <p class="panel-hint">Actions execute against a <b>mock service</b>. Nothing reaches a real
        financial system.</p>
      <div id="approvalList">${approvalList(rec)}</div>
    </div>

    ${(a.evidence_requests || []).length ? `
    <div class="panel">
      <h2>Evidence requests</h2>
      <p class="panel-hint">Customer and analyst replies are not provided by the challenge.
        Responses below are <b>simulated</b> under a published rule.</p>
      ${(rec.evidence_requests_full || a.evidence_requests).map((r) => `
        <div class="ev source-customer">
          <div class="claim"><b>${esc(r.type)}</b> &middot; after step ${r.asked_after_step}
            <span class="tag sim">${esc(r.status || 'simulated')}</span></div>
          ${r.reason ? `<div style="font-size:12.5px;color:var(--text-dim);margin-top:4px">${esc(r.reason)}</div>` : ''}
          ${r.information_required ? `<div style="font-size:12.5px;margin-top:4px">Asked: ${esc(r.information_required)}</div>` : ''}
          <div style="font-size:12.5px;margin-top:4px"><b>Assumed:</b> ${esc(r.assumed_response)}</div>
          ${r.assumption_basis ? `<div class="meta" style="font-family:inherit">Basis: ${esc(r.assumption_basis)}</div>` : ''}
          ${r.effect_on_investigation ? `<div class="meta" style="font-family:inherit">Effect: ${esc(r.effect_on_investigation)}</div>` : ''}
        </div>`).join('')}
    </div>` : ''}

    <div class="panel">
      <h2>Suspicious activity report</h2>
      ${sar.file
        ? `<div class="warnbox"><b>Filing recommended</b> &mdash; requires L2 approval. ${esc(sar.reason)}</div>
           <div style="font-size:13px;line-height:1.65">${esc(sar.narrative)}</div>
           <div class="panel-hint" style="margin-top:10px">Subjects: ${esc((sar.subjects || []).join(', '))}
             &middot; Total ${money(sar.total_amount_usd)} &middot; ${esc((sar.activity_dates || []).join(' to '))}</div>`
        : `<div class="okbox"><b>No filing due.</b> ${esc(sar.reason)}</div>`}
    </div>

    <div class="panel">
      <h2>Case memory used</h2>
      <p class="panel-hint">Closed investigations retrieved as context. Historical evidence is kept
        separate from what this investigation found.</p>
      ${(c.similar_prior_cases || []).length
        ? (c.similar_prior_cases || []).map((id) => `<span class="tag" style="margin:2px">${esc(id)}</span>`).join('')
        : '<div class="muted">No comparable prior investigation was retrieved.</div>'}
      ${(c.connected_card_ids || []).length ? `
        <div style="margin-top:12px"><div class="muted" style="margin-bottom:5px">Connected cards (${c.connected_card_ids.length})</div>
        ${c.connected_card_ids.slice(0, 30).map((x) => `<span class="tag mono" style="margin:2px">${esc(x)}</span>`).join('')}</div>` : ''}
      ${(c.connected_device_profiles || []).length ? `
        <div style="margin-top:12px"><div class="muted" style="margin-bottom:5px">Shared device profiles</div>
        ${c.connected_device_profiles.map((x) => `<div class="mono" style="font-size:11.5px;color:var(--text-dim)">${esc(x)}</div>`).join('')}</div>` : ''}
    </div>

    <div class="panel">
      <h2>Agent activity</h2>
      <p class="panel-hint">Investigation steps and the graph calls behind them.</p>
      <div class="timeline">
        ${(rec.timeline || []).map((t) => `
          <div class="tl kind-${esc(t.kind)}">
            <span class="step">${t.step}</span>
            <span><span class="k">${esc(t.kind)}</span><br>${esc(t.detail)}</span>
          </div>`).join('')}
      </div>
      <details><summary>Tool call log (${(rec.tool_log || []).length})</summary>
        <pre class="json">${esc((rec.tool_log || []).map((t) =>
          `${String(t.step).padStart(2)}  ${t.ok ? 'ok  ' : 'FAIL'}  ${String(t.duration_ms).padStart(7)}ms  ${t.backend}  ${t.ref}\n      -> ${t.result_summary}${t.error ? '\n      !! ' + t.error : ''}`).join('\n'))}</pre>
      </details>
      <details><summary>Risk breakdown</summary>
        <pre class="json">${esc(JSON.stringify(rec.risk, null, 2))}</pre>
      </details>
    </div>`;

  $('#caseBody').innerHTML = `<div>${left}</div><div>${right}</div>`;
  wireApprovals();
  loadGraph(rec.case_id);
}

const fact = (l, v, color) =>
  `<div class="fact"><div class="l">${l}</div><div class="v" ${color ? `style="color:${color}"` : ''}>${v}</div></div>`;

const actionRow = (x) => `
  <div class="action-row"><div>
    <div class="a">${esc(x.action)} <span class="tag ${esc(x.route)}">${esc(x.route)}</span></div>
    <div class="r">${esc(x.reason)}</div>
  </div></div>`;

function approvalList(rec) {
  const finals = (rec.actions_full || {}).final || [];
  const appr = rec.approvals || {};
  const rows = finals.map((x) => {
    const done = appr[x.action];
    const ctl = x.route === 'auto'
      ? `<span class="tag auto">auto &middot; ${esc(x.status === 'recommended' ? 'agent may execute' : x.status)}</span>`
      : done
        ? `<span class="tag ${done.decision === 'approved' ? 'legitimate' : 'fraud'}">${esc(done.decision)} by ${esc(done.approver)}</span>`
        : `<span class="approve-controls">
             <input class="appr-name" data-action="${esc(x.action)}" placeholder="your name" style="width:110px">
             <button class="btn small appr-yes" data-action="${esc(x.action)}">Approve</button>
             <button class="btn small ghost appr-no" data-action="${esc(x.action)}">Reject</button>
           </span>`;
    return `<div class="action-row"><div>
        <div class="a">${esc(x.action)} <span class="tag ${esc(x.route)}">${esc(x.route)}</span></div>
        <div class="r">${esc(x.reason)}</div>
        ${done && done.execution ? `<div class="meta" style="font-size:11px;color:var(--text-faint);margin-top:4px">simulated execution ${esc(done.execution.execution_id)} &middot; would have ${esc(done.execution.would_have)}</div>` : ''}
      </div><div>${ctl}</div></div>`;
  }).join('');
  const execs = (rec.executions || []);
  return rows + (execs.length
    ? `<details><summary>Simulated execution log (${execs.length})</summary><pre class="json">${esc(JSON.stringify(execs, null, 2))}</pre></details>`
    : '');
}

function wireApprovals() {
  const send = async (action, decision) => {
    const name = ($(`.appr-name[data-action="${CSS.escape(action)}"]`) || {}).value || '';
    if (!name.trim()) { toast('Enter your name to record the approval.'); return; }
    try {
      await api(`/api/cases/${encodeURIComponent(state.current.case_id)}/approve`, {
        method: 'POST',
        body: JSON.stringify({ action, approver: name.trim(), decision, note: '' }),
      });
      const rec = await api('/api/cases/' + encodeURIComponent(state.current.case_id));
      state.current = rec;
      $('#approvalList').innerHTML = approvalList(rec);
      wireApprovals();
      toast(`${action} ${decision}${decision === 'approved' ? ' (simulated execution recorded)' : ''}`);
      loadQueue();
    } catch (e) { toast('Failed: ' + e.message); }
  };
  $$('.appr-yes').forEach((b) => b.addEventListener('click', () => send(b.dataset.action, 'approved')));
  $$('.appr-no').forEach((b) => b.addEventListener('click', () => send(b.dataset.action, 'rejected')));
}

/* =============================================== graph visualisation */
const NODE_STYLE = {
  Card: { r: 9, c: '#4d8dff' },
  Customer: { r: 10, c: '#7d5bff' },
  Transaction: { r: 5, c: '#7f8b9c' },
  DeviceProfile: { r: 9, c: '#e5a33c' },
  BillingRegion: { r: 7, c: '#30a46c' },
  ClosedCase: { r: 8, c: '#c05bd8' },
  AgentCase: { r: 11, c: '#e5484d' },
};

async function loadGraph(caseId) {
  const svg = $('#graphSvg');
  if (!svg) return;
  svg.innerHTML = '<text x="20" y="30" fill="#64707f" font-size="12">loading graph…</text>';
  let g;
  try { g = await api(`/api/cases/${encodeURIComponent(caseId)}/graph`); } catch (e) {
    svg.innerHTML = `<text x="20" y="30" fill="#f08a8d" font-size="12">graph unavailable: ${esc(e.message)}</text>`;
    return;
  }
  state.graph = g;
  $('#graphLegend').innerHTML = Object.entries(NODE_STYLE)
    .map(([k, v]) => `<span><i style="background:${v.c}"></i>${k}</span>`).join('')
    + `<span class="muted">${g.nodes.length} nodes &middot; ${g.edges.length} edges${g.truncated ? ' (truncated)' : ''} &middot; served by ${esc(g.backend)}</span>`;
  drawGraph(g);
}

function drawGraph(g) {
  const svg = $('#graphSvg');
  const W = svg.clientWidth || 800, H = svg.clientHeight || 430;
  const nodes = g.nodes.map((n) => Object.assign({}, n, {
    x: W / 2 + (Math.random() - 0.5) * W * 0.6,
    y: H / 2 + (Math.random() - 0.5) * H * 0.6,
    vx: 0, vy: 0,
  }));
  const index = new Map(nodes.map((n) => [n.id, n]));
  const links = g.edges
    .map((e) => ({ s: index.get(e.source), t: index.get(e.target), kind: e.kind }))
    .filter((l) => l.s && l.t);

  // simple force layout: repulsion + spring + centring
  for (let step = 0; step < 320; step++) {
    const k = 1 - step / 320;
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        let dx = b.x - a.x, dy = b.y - a.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
        const f = 2600 / d2;
        const d = Math.sqrt(d2);
        const fx = (dx / d) * f, fy = (dy / d) * f;
        a.vx -= fx; a.vy -= fy; b.vx += fx; b.vy += fy;
      }
    }
    links.forEach((l) => {
      const dx = l.t.x - l.s.x, dy = l.t.y - l.s.y;
      const d = Math.max(1, Math.sqrt(dx * dx + dy * dy));
      const f = (d - 62) * 0.012;
      const fx = (dx / d) * f, fy = (dy / d) * f;
      l.s.vx += fx; l.s.vy += fy; l.t.vx -= fx; l.t.vy -= fy;
    });
    nodes.forEach((n) => {
      n.vx += (W / 2 - n.x) * 0.0016;
      n.vy += (H / 2 - n.y) * 0.0016;
      n.x += n.vx * k; n.y += n.vy * k;
      n.vx *= 0.82; n.vy *= 0.82;
      n.x = Math.max(24, Math.min(W - 24, n.x));
      n.y = Math.max(20, Math.min(H - 20, n.y));
    });
  }

  const parts = ['<g id="gRoot">'];
  links.forEach((l) => {
    parts.push(`<line x1="${l.s.x.toFixed(1)}" y1="${l.s.y.toFixed(1)}" x2="${l.t.x.toFixed(1)}" y2="${l.t.y.toFixed(1)}" stroke="#2b3444" stroke-width="1"/>`);
  });
  nodes.forEach((n, i) => {
    const st = NODE_STYLE[n.kind] || { r: 6, c: '#7f8b9c' };
    let stroke = 'none', sw = 0;
    if (n.flagged) { stroke = '#e5484d'; sw = 2.5; }
    else if (n.affected) { stroke = '#f0a23c'; sw = 2; }
    else if (n.focus || n.shared || n.connected) { stroke = '#dde3ec'; sw = 1.5; }
    parts.push(`<circle data-i="${i}" cx="${n.x.toFixed(1)}" cy="${n.y.toFixed(1)}" r="${st.r}" fill="${st.c}" stroke="${stroke}" stroke-width="${sw}" style="cursor:pointer"/>`);
    if (n.kind !== 'Transaction' || n.flagged) {
      parts.push(`<text x="${(n.x + st.r + 4).toFixed(1)}" y="${(n.y + 3.5).toFixed(1)}" fill="#8d99ab" font-size="9.5" pointer-events="none">${esc(String(n.label).slice(0, 26))}</text>`);
    }
  });
  parts.push('</g>');
  svg.innerHTML = parts.join('');

  // pan / zoom
  let tx = 0, ty = 0, scale = 1, dragging = false, lx = 0, ly = 0;
  const root = $('#gRoot', svg);
  const apply = () => root.setAttribute('transform', `translate(${tx},${ty}) scale(${scale})`);
  svg.onmousedown = (e) => { dragging = true; lx = e.clientX; ly = e.clientY; };
  window.addEventListener('mouseup', () => { dragging = false; });
  svg.onmousemove = (e) => {
    if (dragging) { tx += e.clientX - lx; ty += e.clientY - ly; lx = e.clientX; ly = e.clientY; apply(); }
  };
  svg.onwheel = (e) => {
    e.preventDefault();
    const f = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    scale = Math.max(0.3, Math.min(4, scale * f));
    apply();
  };
  $('#graphReset').onclick = () => { tx = 0; ty = 0; scale = 1; apply(); };

  const tip = $('#graphTip');
  $$('#graphSvg circle').forEach((el) => {
    el.addEventListener('mouseenter', (e) => {
      const n = nodes[Number(el.dataset.i)];
      const bits = [`${n.kind}: ${n.label}`];
      if (n.amount != null) bits.push(`amount ${money(n.amount)}`, `ts ${n.ts}`, `${n.channel} / ${n.product}`, `bank score ${n.risk}`);
      if (n.proxy) bits.push(`proxy ${n.proxy}`);
      if (n.device_new) bits.push(`device ${n.device_new} for account`);
      if (n.flagged) bits.push('FLAGGED TRANSACTION');
      if (n.affected) bits.push('part of the identified episode');
      if (n.verdict) bits.push(`verdict ${n.verdict} p=${n.probability}`, `written to graph: ${n.written}`);
      tip.innerHTML = bits.map(esc).join('<br>');
      tip.style.display = 'block';
      const r = svg.getBoundingClientRect();
      tip.style.left = Math.min(r.width - 330, e.clientX - r.left + 12) + 'px';
      tip.style.top = (e.clientY - r.top + 12) + 'px';
    });
    el.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
  });
}

/* ============================================================== memory */
async function loadMemory() {
  const el = $('#memoryBody');
  el.innerHTML = '<div class="panel"><span class="spinner"></span> loading&hellip;</div>';
  try {
    const m = await api('/api/memory');
    const h = m.historical, ag = m.agent_written;
    el.innerHTML = `
      <div class="two-col">
        <div class="panel">
          <h2>Historical investigations (shipped with the dataset)</h2>
          <p class="panel-hint">Read-only ground truth, ${esc(h.date_range[0])} to ${esc(h.date_range[1])}.
            Used as retrieval context, never as a verdict on a new alert.</p>
          <div class="facts">
            ${fact('Total', h.total.toLocaleString())}
            ${fact('Confirmed fraud', h.confirmed_fraud.toLocaleString(), 'var(--fraud)')}
            ${fact('Cleared', h.cleared.toLocaleString(), 'var(--legit)')}
          </div>
          <div class="split">${Object.entries(h.by_pattern).sort((a, b) => b[1] - a[1])
            .map(([k, v]) => `<span class="tag">${esc(k)} &middot; ${v}</span>`).join('')}</div>
        </div>
        <div class="panel">
          <h2>Agent-written cases</h2>
          <p class="panel-hint">Investigations this system closed. These become memory for the next alert.</p>
          <div class="facts">
            ${fact('Written', ag.total)}
            ${fact('Persisted in TigerGraph', ag.written_to_graph)}
          </div>
          ${ag.total === 0 ? '<div class="muted">No agent cases yet.</div>' : ''}
        </div>
      </div>
      <div class="panel">
        <h2>Most recent agent cases</h2>
        <div class="table-wrap"><table><thead><tr>
          <th>Case</th><th>Verdict</th><th>p(fraud)</th><th>Pattern</th><th>Exposure</th><th>In graph</th>
        </tr></thead><tbody>
        ${(ag.cases || []).slice().reverse().map((row) => {
          const cse = row.case || row;
          return `<tr><td class="mono">${esc(cse.case_id || cse.graph_case_id)}</td>
            <td><span class="tag ${esc(cse.verdict)}">${esc(cse.verdict)}</span></td>
            <td class="num">${Number(cse.fraud_probability || 0).toFixed(2)}</td>
            <td>${esc(cse.pattern)}</td><td class="num">${money(cse.exposure_usd)}</td>
            <td>${row.written_to_graph ? 'yes' : 'no'}</td></tr>`;
        }).join('') || '<tr><td colspan="6" class="muted">none</td></tr>'}
        </tbody></table></div>
      </div>`;
  } catch (e) { el.innerHTML = `<div class="panel">Failed: ${esc(e.message)}</div>`; }
}

/* ======================================================== model/policy */
async function loadModel() {
  const el = $('#modelBody');
  el.innerHTML = '<div class="panel"><span class="spinner"></span> loading&hellip;</div>';
  try {
    const [m, p, q] = await Promise.all([api('/api/model-card'), api('/api/policy'), api('/api/ingest-quality')]);
    const met = m.metrics || {};
    const ws = Object.entries(m.weights || {}).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
    const maxW = Math.max(1e-6, ...ws.map(([, v]) => Math.abs(v)));
    el.innerHTML = `
      <div class="panel">
        <h2>Risk model card</h2>
        <p class="panel-hint">${esc(m.method || m.source)}. Fraud probability is the trigger's
          prior plus the measured log likelihood ratio of every detector that fired. A detector
          that does not fire contributes nothing.</p>
        <div class="facts">
          ${Object.entries(m.trigger_prior || {}).map(([k, v]) =>
            fact(k.replace(/_/g, ' '), `${v >= 0 ? '+' : ''}${Number(v).toFixed(2)}`,
                 v > 0 ? 'var(--fraud)' : v < 0 ? 'var(--legit)' : '')).join('')}
        </div>
        <p class="panel-hint">${esc(m.trigger_prior_note || '')}</p>
        ${(m.weight_basis || []).length ? `
        <div class="table-wrap" style="margin-top:12px"><table><thead><tr>
          <th>Detector</th><th>Weight</th><th>Fires on fraud</th><th>Fires on legitimate</th><th>Basis</th>
        </tr></thead><tbody>
        ${m.weight_basis.slice().sort((a,b) => Math.abs(b.weight) - Math.abs(a.weight)).map((b) => `
          <tr><td class="mono">${esc(b.detector)}</td>
            <td class="num" style="color:${b.weight > 0 ? 'var(--fraud)' : b.weight < 0 ? 'var(--legit)' : 'var(--text-dim)'}">${b.weight >= 0 ? '+' : ''}${Number(b.weight).toFixed(2)}</td>
            <td class="num">${b.rate_fraud_pct}%</td>
            <td class="num">${b.rate_legit_pct}%</td>
            <td style="white-space:normal;max-width:460px;font-size:11.5px;color:var(--text-dim)">
              <span class="tag">${esc(b.source)}</span> ${esc(b.basis)}</td></tr>`).join('')}
        </tbody></table></div>` : ''}
        ${m.notes && m.notes.design ? `<details style="margin-top:12px"><summary>Why the model is built this way</summary>
          <pre class="json" style="white-space:pre-wrap">${esc(m.notes.design)}</pre></details>` : ''}
        ${met.overall ? `<h3 style="font-size:13px;margin-top:18px">The logistic fit, and why it is not used</h3>
        <p class="panel-hint">Kept and reported because its failure is the evidence for the
          design above: fitted to separate confirmed fraud from cleared alerts, graph features
          score near chance, because the cleared alerts are the most anomalous legitimate
          activity the bank could find.</p>
        <div class="facts">
          ${fact('ROC AUC (out-of-fold)', Number(met.overall.roc_auc).toFixed(3))}
          ${fact('Accuracy', Number(met.overall.accuracy).toFixed(3))}
          ${fact('Recall (fraud)', Number(met.overall.recall_fraud).toFixed(3))}
          ${fact('Specificity', Number(met.overall.specificity).toFixed(3))}
          ${fact('Brier', Number(met.overall.brier).toFixed(3))}
          ${fact('Training rows', Number(met.overall.n).toLocaleString())}
        </div>` : '<div class="warnbox">No fitted model on disk; documented fallback weights are in use.</div>'}
        ${met.vs_cleared_hard_negatives ? `<p class="panel-hint">Against the hard negatives
          (alerts that looked suspicious and were cleared): AUC
          ${Number(met.vs_cleared_hard_negatives.roc_auc).toFixed(3)},
          specificity ${Number(met.vs_cleared_hard_negatives.specificity).toFixed(3)}.
          Against ordinary unalerted activity: AUC
          ${Number((met.vs_unalerted_base_rate || {}).roc_auc || 0).toFixed(3)}.</p>` : ''}
        <div style="margin-top:12px">
          ${ws.filter(([, v]) => v).map(([k, v]) => `<div class="dist-row" style="grid-template-columns:200px 1fr 54px">
            <span class="muted">${esc(k)}</span>
            <span class="wbar"><i style="${v >= 0 ? 'left:50%' : 'right:50%'};width:${(Math.abs(v) / maxW) * 50}%;background:${v >= 0 ? 'var(--fraud)' : 'var(--legit)'}"></i></span>
            <span class="n">${v >= 0 ? '+' : ''}${Number(v).toFixed(2)}</span></div>`).join('')}
        </div>
        <div class="panel-hint" style="margin-top:12px">No global intercept &mdash; the prior lives
          on the trigger. Each detector term is clipped at &plusmn;${m.max_abs_weight || 2.6} log-odds,
          so no single detector can carry a case alone.
          ${m.class_counts ? `Measured on ${Object.entries(m.class_counts).map(([k, v]) => `${v.toLocaleString()} ${k.replace(/_/g, ' ')}`).join(', ')}.` : ''}</div>
        ${Object.values(m.notes || {}).filter(Boolean).map((n) => `<div class="warnbox">${esc(n)}</div>`).join('')}
        ${(met.detector_firing_rates || []).length ? `
          <details open><summary>Detector firing rates (measured, not asserted)</summary>
            <div class="table-wrap"><table><thead><tr><th>Detector</th><th>Fires on fraud</th><th>Fires on legitimate</th><th>n</th></tr></thead><tbody>
            ${met.detector_firing_rates.map((r) => `<tr><td>${esc(r.feature)}</td>
              <td class="num">${r.fires_on_fraud_pct}%</td><td class="num">${r.fires_on_legit_pct}%</td>
              <td class="num">${r.n_fires}</td></tr>`).join('')}
            </tbody></table></div></details>` : ''}
        ${(met.reliability || []).length ? `
          <details><summary>Calibration reliability</summary><pre class="json">${esc(JSON.stringify(met.reliability, null, 2))}</pre></details>` : ''}
      </div>

      <div class="two-col">
        <div class="panel">
          <h2>Fraud policy v1.0</h2>
          <p class="panel-hint">Enforced deterministically. The LLM cannot override a route.</p>
          <dl class="kv">${Object.entries(p.rules).map(([k, v]) =>
            `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>
          <div style="margin-top:14px"><div class="muted" style="margin-bottom:6px">Approval routing</div>
            <div><span class="tag auto">auto</span> ${esc(p.routes.auto.join(', '))}</div>
            <div style="margin-top:6px"><span class="tag L1">L1</span> ${esc(p.routes.L1.join(', '))}</div>
            <div style="margin-top:6px"><span class="tag L2">L2</span> ${esc(p.routes.L2.join(', '))}</div>
          </div>
          <div class="warnbox" style="margin-top:14px"><b>Evidence-request simulation policy.</b>
            ${esc(p.evidence_request_simulation)}</div>
        </div>
        <div class="panel">
          <h2>Ingestion quality</h2>
          <p class="panel-hint">Validation run when the graph was built.</p>
          <pre class="json">${esc(JSON.stringify(q, null, 2))}</pre>
        </div>
      </div>`;
  } catch (e) { el.innerHTML = `<div class="panel">Failed: ${esc(e.message)}</div>`; }
}

/* ================================================================ boot */
(async function boot() {
  await loadHealth();
  await loadQueue();
  await loadOverview();
  setInterval(loadHealth, 30000);
})();
