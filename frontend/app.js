/* Fraud Investigation Console -- vanilla JS, no CDN, works offline. */
'use strict';

const API = '';
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = (n) => '$' + Number(n || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct = (n) => (n == null ? '—' : (Number(n) * 100).toFixed(n < 0.1 ? 2 : 1) + '%');

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

/* An error the user can act on: what failed, why, and a way to retry --
 * rather than a permanently shimmering skeleton. */
function showError(viewSel, title, detail, retry) {
  const view = $(viewSel);
  if (!view) return;
  clearError(viewSel);
  const box = document.createElement('div');
  box.className = 'errorbox';
  box.innerHTML = `<div class="msg"><b>${esc(title)}</b>${esc(detail || '')}</div>`;
  if (retry) {
    const b = document.createElement('button');
    b.className = 'btn small';
    b.textContent = 'Retry';
    b.addEventListener('click', () => { clearError(viewSel); retry(); });
    box.appendChild(b);
  }
  view.prepend(box);
}

function clearError(viewSel) {
  const view = $(viewSel);
  if (view) $$('.errorbox', view).forEach((n) => n.remove());
}

const probColor = (p) => p >= 0.7 ? 'var(--fraud)' : p >= 0.3 ? 'var(--uncertain)' : 'var(--legit)';

/* =========================================================== navigation */
const TABS = $$('#tabs button');
TABS.forEach((b, i) => {
  b.addEventListener('click', () => show(b.dataset.view));
  b.addEventListener('keydown', (ev) => {
    const step = ev.key === 'ArrowRight' ? 1 : ev.key === 'ArrowLeft' ? -1 : 0;
    if (!step) return;
    ev.preventDefault();
    const next = TABS[(i + step + TABS.length) % TABS.length];
    next.focus();
    show(next.dataset.view);
  });
});

function show(view) {
  $$('#tabs button').forEach((b) => {
    const on = b.dataset.view === view;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
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
      // name what was asked for and why it was not available, rather than
      // just showing a backend nobody chose
      const why = h.degraded_from
        ? `${esc(h.degraded_from)} requested, unavailable`
        : (h.tigergraph_configured ? 'configured, unreachable' : 'not configured');
      parts.push(`<span class="pill warn" title="${esc(h.degraded_reason || 'TigerGraph is the system of record; the local mirror serves the same query catalogue for development.')}">TigerGraph <b>${why}</b></span>`);
    }
    strip.innerHTML = parts.join('');
  } catch (e) {
    strip.innerHTML = `<span class="pill bad">Backend unreachable: ${esc(e.message)}</span>`;
  }
}

/* ============================================================ overview */
async function loadOverview() {
  let o;
  try {
    o = await api('/api/overview');
  } catch (e) {
    // this used to `return` silently, leaving the skeletons shimmering for
    // ever with no indication that anything had gone wrong
    showError('#view-overview', 'Could not load the overview', e.message, loadOverview);
    return;
  }
  clearError('#view-overview');
  $('#kpis').innerHTML = [
    kpi(o.cases_investigated, 'Investigations',
        `${o.total_alerts_in_pack} alerts in the case pack`
        + (o.adhoc_investigations ? ` &middot; ${o.adhoc_investigations} ad hoc, counted separately` : '')),
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
  $$('#recentActivity .cid').forEach((node) =>
    node.addEventListener('click', () => openCase(node.dataset.case)));

  loadDivergence();
}

const kpi = (v, l, s) => `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div><div class="s">${s}</div></div>`;

/* ================================================= score vs evidence chart
 * The argument for doing graph investigation at all, drawn from the records:
 * an alert arrives with a score, the agent investigates, and the two rarely
 * land in the same place. Points off the diagonal are cases where the graph
 * overruled the model -- in both directions.
 */
const SVGNS = 'http://www.w3.org/2000/svg';
const VERDICT_COLOR = { fraud: 'var(--fraud)', legitimate: 'var(--legit)', uncertain: 'var(--uncertain)' };
const el = (name, attrs = {}, text) => {
  const n = document.createElementNS(SVGNS, name);
  for (const k in attrs) n.setAttribute(k, attrs[k]);
  if (text != null) n.textContent = text;
  return n;
};
const signalText = (s) => String(s || '').replace(/_/g, ' ');

async function loadDivergence() {
  const svg = $('#divergenceChart');
  if (!svg) return;
  let d;
  try { d = await api('/api/divergence'); } catch (e) {
    $('#divergenceStats').innerHTML = `<span class="muted">chart unavailable: ${esc(e.message)}</span>`;
    return;
  }
  state.divergence = d;
  const total = d.n_scored;
  const moved = d.escalated + d.cleared;
  $('#divergenceStats').innerHTML = total ? `
    <div class="hero-stat"><b>${moved}</b><span>of ${total} alerts moved</span></div>
    <div class="hero-stat up"><b>${d.escalated}</b><span>escalated by evidence</span></div>
    <div class="hero-stat down"><b>${d.cleared}</b><span>cleared by evidence</span></div>` : '';
  drawDivergence(d);
  renderCallouts(d);
}

function drawDivergence(d) {
  const svg = $('#divergenceChart');
  svg.innerHTML = '';
  if (!d.points.length) {
    $('#divergenceChart').appendChild(el('text', { x: 20, y: 30, fill: 'var(--text-faint)', 'font-size': 12 },
      'No scored alerts have been investigated yet.'));
    return;
  }
  // The viewBox is scaled to the container, so a fixed 1000-unit width means
  // 11-unit text renders at under 4px on a phone. Narrow screens get their own
  // geometry: fewer units across, so every unit is worth more pixels.
  const narrow = (svg.clientWidth || 800) < 560;
  const W = narrow ? 460 : 1000;
  const H = narrow ? 430 : 430;
  const M = narrow ? { t: 16, r: 14, b: 44, l: 42 } : { t: 22, r: 24, b: 50, l: 58 };
  const FS = narrow ? { tick: 13, axis: 13, hint: 0, label: 14 }
                    : { tick: 11, axis: 12, hint: 10.5, label: 11.5 };
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
  const iw = W - M.l - M.r, ih = H - M.t - M.b;
  const X = (v) => M.l + v * iw;
  const Y = (v) => M.t + (1 - v) * ih;
  const band = d.agreement_band;

  const g = el('g');
  svg.appendChild(g);

  // agreement band: |agent - bank| <= band
  const poly = [[0, band], [1 - band, 1], [1, 1], [1, 1 - band], [band, 0], [0, 0]]
    .map(([x, y]) => `${X(x)},${Y(y)}`).join(' ');
  g.appendChild(el('polygon', { points: poly, fill: 'var(--agree-fill)', stroke: 'none' }));
  g.appendChild(el('line', {
    x1: X(0), y1: Y(0), x2: X(1), y2: Y(1),
    stroke: 'var(--line)', 'stroke-width': 1, 'stroke-dasharray': '4 4',
  }));

  // gridlines and ticks
  for (let i = 0; i <= 5; i++) {
    const v = i / 5;
    g.appendChild(el('line', { x1: X(0), y1: Y(v), x2: X(1), y2: Y(v), stroke: 'var(--line-soft)', 'stroke-width': 1 }));
    g.appendChild(el('line', { x1: X(v), y1: Y(0), x2: X(v), y2: Y(1), stroke: 'var(--line-soft)', 'stroke-width': 1 }));
    if (narrow && i % 2) continue;   // every other tick, or they collide
    g.appendChild(el('text', { x: M.l - 8, y: Y(v) + 4, fill: 'var(--text-faint)', 'font-size': FS.tick, 'text-anchor': 'end' }, v.toFixed(1)));
    g.appendChild(el('text', { x: X(v), y: H - M.b + 20, fill: 'var(--text-faint)', 'font-size': FS.tick, 'text-anchor': 'middle' }, v.toFixed(1)));
  }
  g.appendChild(el('text', { x: M.l + iw / 2, y: H - 8, fill: 'var(--text-dim)', 'font-size': FS.axis, 'text-anchor': 'middle' },
    narrow ? "Bank model's risk score" : "Bank model's risk score at the time of the alert"));
  const ylx = narrow ? 13 : 14;
  const yl = el('text', { x: ylx, y: M.t + ih / 2, fill: 'var(--text-dim)', 'font-size': FS.axis, 'text-anchor': 'middle' },
    narrow ? "Agent's probability" : "Agent's assessed probability");
  yl.setAttribute('transform', `rotate(-90 ${ylx} ${M.t + ih / 2})`);
  g.appendChild(yl);

  // quadrant hints
  if (!narrow) {
    g.appendChild(el('text', { x: X(0.02), y: Y(0.86), fill: 'var(--text-faint)', 'font-size': FS.hint },
      'low score, high evidence — the model missed it'));
    g.appendChild(el('text', { x: X(0.98), y: Y(0.14), fill: 'var(--text-faint)', 'font-size': FS.hint, 'text-anchor': 'end' },
      'high score, no evidence — a false alarm'));
  }

  // points
  const tip = $('#divergenceTip');
  const sorted = d.points.slice().sort((a, b) => Math.abs(a.delta) - Math.abs(b.delta));
  sorted.forEach((p) => {
    const cx = X(p.bank_risk_score), cy = Y(p.fraud_probability);
    const far = Math.abs(p.delta) > d.agreement_band;
    const node = el('g', {
      class: 'pt' + (far ? ' far' : ''), tabindex: '0', role: 'button',
      'aria-label': `${p.case_id}: bank score ${p.bank_risk_score.toFixed(2)}, agent ${p.fraud_probability.toFixed(2)}, ${p.verdict}. Open case.`,
    });
    if (far) {
      node.appendChild(el('line', {
        x1: cx, y1: cy, x2: X(p.bank_risk_score), y2: Y(p.bank_risk_score),
        stroke: VERDICT_COLOR[p.verdict] || 'var(--text-faint)', 'stroke-width': 1.5, opacity: 0.35,
      }));
    }
    node.appendChild(el('circle', {
      cx, cy, r: narrow ? (far ? 9 : 7) : (far ? 8 : 6),
      fill: VERDICT_COLOR[p.verdict] || 'var(--text-faint)',
      'fill-opacity': far ? 0.9 : 0.5,
      stroke: 'var(--bg)', 'stroke-width': 1.5,
    }));
    const showTip = () => {
      const dr = p.driver;
      tip.innerHTML = `<b>${esc(p.case_id)}</b> &middot; <span class="tag ${esc(p.verdict)}">${esc(p.verdict)}</span>
        <div>score <b>${p.bank_risk_score.toFixed(2)}</b> &rarr; agent <b>${p.fraud_probability.toFixed(2)}</b>
        (${p.delta >= 0 ? '+' : ''}${p.delta.toFixed(2)})</div>
        ${dr ? `<div class="muted">moved most by <b>${esc(signalText(dr.signal))}</b></div>` : ''}
        ${p.exposure_usd ? `<div class="muted">exposure ${money(p.exposure_usd)}</div>` : ''}
        <div class="muted">click to open</div>`;
      tip.style.display = 'block';
      const wrap = svg.getBoundingClientRect();
      const sx = wrap.width / W, sy = wrap.height / H;
      tip.style.left = Math.min(wrap.width - 210, Math.max(4, cx * sx + 12)) + 'px';
      tip.style.top = Math.max(4, cy * sy - 8) + 'px';
    };
    node.addEventListener('mouseenter', showTip);
    node.addEventListener('focus', showTip);
    node.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
    node.addEventListener('blur', () => { tip.style.display = 'none'; });
    node.addEventListener('click', () => openCase(p.case_id));
    node.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); openCase(p.case_id); }
    });
    g.appendChild(node);
  });

  // label the two furthest cases, one each way
  const up = d.points.filter((p) => p.direction === 'escalated')[0];
  const down = d.points.filter((p) => p.direction === 'cleared')[0];
  [up, down].filter(Boolean).forEach((p) => {
    const cx = X(p.bank_risk_score), cy = Y(p.fraud_probability);
    const below = p.fraud_probability > 0.5;   // room underneath a high point
    g.appendChild(el('text', {
      x: cx, y: cy + (below ? 26 : -18), 'font-size': FS.label, 'font-weight': 600,
      fill: VERDICT_COLOR[p.verdict] || 'var(--text)',
      'text-anchor': 'middle', 'pointer-events': 'none',
    }, p.case_id));
  });

  $('#divergenceDesc').textContent =
    `Scatter plot of ${d.n_scored} investigated alerts. ${d.escalated} were escalated by graph `
    + `evidence above the bank's score, ${d.cleared} were cleared below it, and ${d.agreed} agreed `
    + `within ${d.agreement_band}. Largest gap: ${d.largest_gap ? d.largest_gap.case_id : 'none'}.`;
}

function renderCallouts(d) {
  const box = $('#divergenceCallouts');
  if (!box) return;
  const up = d.points.filter((p) => p.direction === 'escalated')[0];
  const down = d.points.filter((p) => p.direction === 'cleared')[0];
  const card = (p, kind) => {
    if (!p) return '';
    const dr = p.driver;
    return `<button class="callout ${kind}" data-case="${esc(p.case_id)}">
      <div class="callout-head">${kind === 'up' ? 'Missed by the score' : 'False alarm'}
        <span class="mono">${esc(p.case_id)}</span></div>
      <div class="callout-move">
        <span class="from">${p.bank_risk_score.toFixed(2)}</span>
        <span class="arrow" aria-hidden="true">&rarr;</span>
        <span class="to" style="color:${VERDICT_COLOR[p.verdict]}">${p.fraud_probability.toFixed(2)}</span>
        <span class="tag ${esc(p.verdict)}">${esc(p.verdict)}</span>
      </div>
      <div class="callout-why">${dr ? `Moved most by <b>${esc(signalText(dr.signal))}</b>. ` : ''}${esc(p.note || '')}</div>
    </button>`;
  };
  box.innerHTML = card(up, 'up') + card(down, 'down');
  $$('.callout', box).forEach((b) => b.addEventListener('click', () => openCase(b.dataset.case)));
}

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
  // data-label drives the mobile card layout: under 900px each cell prints
  // its own heading, because a 12-column analyst table cannot shrink to 320px
  // and remain a table
  $('#queueTable tbody').innerHTML = rows.map((r) => `
    <tr data-case="${esc(r.case_id)}" tabindex="0" role="button"
        aria-label="Open case ${esc(r.case_id)}, ${esc(r.verdict)}">
      <td class="mono" data-label="Case">${esc(r.case_id)}${r.adhoc ? ' <span class="tag sim" title="Opened by an analyst on an arbitrary transaction, not one of the 20 challenge alerts">ad hoc</span>' : ''}</td>
      <td data-label="Trigger"><span class="tag">${esc((r.trigger_type || '').replace('_', ' '))}</span></td>
      <td class="mono" data-label="Card">${esc(r.card_id)}</td>
      <td class="num" data-label="Bank score">${r.bank_risk_score == null ? '&mdash;' : Number(r.bank_risk_score).toFixed(2)}</td>
      <td class="num" data-label="Agent p(fraud)"><span class="prob"><i class="swatch" style="background:${probColor(r.fraud_probability)}"></i>${Number(r.fraud_probability).toFixed(2)}</span></td>
      <td class="num" data-label="Confidence">${r.confidence == null ? '&mdash;' : Number(r.confidence).toFixed(2)}</td>
      <td data-label="Verdict"><span class="tag ${esc(r.verdict)}">${esc(r.verdict)}</span></td>
      <td data-label="Pattern">${esc(r.pattern)}</td>
      <td class="num" data-label="Exposure">${r.exposure_usd ? money(r.exposure_usd) : '&mdash;'}</td>
      <td class="mono" data-label="Next action">${esc(r.next_action || '&mdash;')}</td>
      <td data-label="Approval">${(r.awaiting_approval || []).length ? `<span class="tag L1">${r.awaiting_approval.length} pending</span>` : '<span class="muted">&mdash;</span>'}</td>
      <td class="muted" data-label="Updated">${esc(String(r.last_updated || '').slice(0, 16).replace('T', ' '))}</td>
    </tr>`).join('') || '<tr><td colspan="12" class="muted" style="padding:24px;text-align:center">No cases match this filter.</td></tr>';
  $$('#queueTable tbody tr[data-case]').forEach((tr) => {
    tr.addEventListener('click', () => openCase(tr.dataset.case));
    tr.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); openCase(tr.dataset.case); }
    });
  });
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
$('#rerunBtn').addEventListener('click', () => rerunQuietly($('#casePicker').value));

async function openCase(id) {
  show('case');
  $('#casePicker').value = id;
  if (live.es) { live.es.close(); live.es = null; }
  if ($('#liveCase') && $('#liveCase').textContent !== id) $('#livePanel').hidden = true;
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
    ${provenanceBanner(rec)}
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

/* Which record is on screen, and whether it still matches what was submitted.
 * A re-run is a working copy: it never edits cases/, so the two can differ --
 * most obviously when the graph is unreachable and the local mirror answered
 * instead. Saying so beats quietly showing a different answer. */
function provenanceBanner(rec) {
  const prov = rec.provenance || { kind: 'published' };
  const drift = rec.drift || {};
  const when = String(prov.at || rec.generated_at || '').slice(0, 19).replace('T', ' ');
  const backend = prov.backend ? `<b>${esc(prov.backend)}</b>` : '';
  if (prov.kind !== 'rerun') {
    return `<div class="provenance">Published answer file
      ${backend ? '&middot; served by ' + backend : ''}
      ${when ? '&middot; generated ' + esc(when) : ''}
      &middot; <span class="muted">cases/${esc(rec.case_id)}.json</span></div>`;
  }
  // The narrator samples its wording, so a perfectly correct re-run differs on
  // case.summary and sar.narrative while agreeing on every fact underneath.
  // Reported as "2 field(s) differ" that reads like a problem, so prose drift
  // and evidence drift are said separately.
  const factsMatch = drift.facts_match !== false;
  const prose = drift.n_prose_differences || 0;
  const rows = (drift.differences || []).slice(0, 6).map((d) =>
    `<li><span class="p">${esc(d.path)}</span><br>${esc(String(d.published).slice(0, 90))}
     &rarr; ${esc(String(d.current).slice(0, 90))}</li>`).join('');
  const verdictText = factsMatch
    ? (prose
        ? `evidence identical to the published answer; ${prose} narrated field(s) reworded by the LLM`
        : 'identical to the published answer')
    : `<b>${drift.n_differences || 0} evidence field(s) differ</b> from the published answer`;
  return `<div class="provenance ${factsMatch ? '' : 'drifted'}">
    <div>Live re-run &middot; served by ${backend || '<b>?</b>'} ${when ? '&middot; ' + esc(when) : ''}
      &middot; ${verdictText}
      ${factsMatch ? '' : `<ul class="drift-list">${rows}</ul>`}
      <div class="muted" style="margin-top:4px">The file in cases/ is unchanged; re-runs never write to it.</div>
    </div></div>`;
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

/* ================================================== end-to-end backtest
 * The only measurement of whether the agent's verdicts are RIGHT, as opposed
 * to whether its parts behave. Read from build/backtest.json; the dashboard
 * never recomputes it, because it takes minutes and a silently different
 * sample on every page load would not be a measurement.
 */
const BT_ORDER = ['neutral', 'neutral:undocumented+card_testing', 'score', 'actual'];

function backtestPanel(bt) {
  if (!bt || !bt.ran) {
    return `<div class="panel"><h2>End-to-end backtest</h2>
      <p class="panel-hint">Not run. The agent's parts are measured; its verdicts are not.</p>
      <pre class="json">${esc((bt && bt.how) || 'python -m fraudgraph.analysis.backtest --all-modes')}</pre></div>`;
  }
  const runs = bt.runs || {};
  const keys = BT_ORDER.filter((k) => runs[k]).concat(
    Object.keys(runs).filter((k) => !BT_ORDER.includes(k)));
  const headline = runs['neutral:undocumented+card_testing'] || runs.neutral;
  const leaks = keys.reduce((n, k) => n + (runs[k].leakage_violations || 0), 0);

  const card = (k) => {
    const r = runs[k];
    const d = r.uncertain_as_negative || {};
    const pats = (r.patterns || []).length ? ` &middot; ${esc(r.patterns.join(' + '))} only` : '';
    const bp = r.recall_by_true_pattern || {};
    const rows = Object.entries(bp).map(([pat, v]) =>
      `<tr><td>${esc(pat)}</td><td class="num">${v.called_fraud}/${v.n}</td>
       <td class="num" style="color:${v.recall >= 0.5 ? 'var(--legit)' : 'var(--text-dim)'}">${pct(v.recall)}</td>
       <td class="num">${Number(v.mean_probability).toFixed(2)}</td></tr>`).join('');
    return `<div class="bt-run">
      <div class="bt-head"><b>${esc(r.trigger_mode)}</b>${pats}
        <span class="muted">n=${r.n_scored}</span></div>
      <div class="facts">
        ${fact('Recall on fraud', pct(d.recall_on_fraud), d.recall_on_fraud >= 0.5 ? 'var(--legit)' : 'var(--uncertain)')}
        ${fact('Specificity', pct(d.specificity_on_cleared), 'var(--legit)')}
        ${fact('False fraud calls', d.fp == null ? '&mdash;' : d.fp, d.fp ? 'var(--uncertain)' : 'var(--legit)')}
        ${fact('AUC', (r.auc && r.auc.agent_probability) != null ? Number(r.auc.agent_probability).toFixed(3) : '&mdash;')}
      </div>
      ${rows ? `<table class="bt-table"><thead><tr><th>true typology</th><th class="num">caught</th>
        <th class="num">recall</th><th class="num">mean p</th></tr></thead><tbody>${rows}</tbody></table>` : ''}
    </div>`;
  };

  return `<div class="panel">
    <div class="panel-head">
      <div>
        <h2>End-to-end backtest</h2>
        <p class="panel-hint">Closed investigations replayed through the whole agent,
          with case memory time-boxed to each alert and the case itself excluded.
          The outcomes are real; the verdicts are the agent's.</p>
      </div>
      <span class="pill ${leaks ? 'bad' : 'ok'}">${leaks ? leaks + ' leakage violation(s)' : 'no leakage'}</span>
    </div>
    ${headline ? `<div class="okbox"><b>Headline.</b> Under a neutral prior &mdash; every case
      arriving as an analyst request, so nothing is on either scale and only graph
      evidence can move the answer &mdash; the agent called
      <b>${pct(((headline.recall_by_true_pattern || {}).undocumented || {}).recall)}</b>
      of the relational-fraud cases, against
      <b>${(headline.uncertain_as_negative || {}).fp}</b> false fraud calls on
      ${(headline.class_counts || {}).cleared} of the hardest negatives in the dataset.</div>` : ''}
    <div class="bt-grid">${keys.map(card).join('')}</div>
    <details><summary>What these numbers do not mean (${(headline && headline.caveats || []).length})</summary>
      <ul class="bt-caveats">${(headline && headline.caveats || []).map((c) => `<li>${esc(c)}</li>`).join('')}</ul>
    </details>
    <p class="panel-hint" style="margin-top:8px">Generated ${esc(String(bt.generated_at || '').slice(0, 19).replace('T', ' '))}
      &middot; <span class="mono">python -m fraudgraph.analysis.backtest --all-modes</span></p>
  </div>`;
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
            ${fact('Distinct cases', ag.total)}
            ${fact('Persisted in TigerGraph', ag.written_to_graph,
                   ag.written_to_graph ? 'var(--legit)' : 'var(--uncertain)')}
            ${fact('Write attempts logged', ag.write_attempts_logged)}
          </div>
          <p class="panel-hint">The journal is append-only, so a case re-run several
            times has several entries; the table below shows the latest per case.</p>
          ${ag.total === 0 ? '<div class="muted">No agent cases yet.</div>' : ''}
          ${ag.written_to_graph === 0 && ag.total
            ? `<div class="warnbox">None of these are in TigerGraph. The local mirror is
                read-only and reports the write as refused rather than claiming one it
                did not make.</div>` : ''}
        </div>
      </div>
      <div class="panel">
        <h2>Most recent agent cases</h2>
        <div class="table-wrap"><table><thead><tr>
          <th>Case</th><th>Verdict</th><th>p(fraud)</th><th>Pattern</th><th>Exposure</th><th>In graph</th>
        </tr></thead><tbody>
        ${(ag.cases || []).map((row) => {
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
      ${backtestPanel(m.backtest)}
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

let _chartResize;
window.addEventListener('resize', () => {
  clearTimeout(_chartResize);
  _chartResize = setTimeout(() => {
    if (state.divergence && $('#view-overview').classList.contains('active')) {
      drawDivergence(state.divergence);
    }
    if (state.graph && state.current) drawGraph(state.graph);
  }, 180);
});

/* ====================================================== live investigation
 * An answer file is the result. This is the work: each reasoning step and
 * each graph query arriving over SSE as the agent actually runs.
 *
 * Lines are revealed on a short stagger because a whole investigation takes
 * about a second against the local mirror and is otherwise unreadable. The
 * *timings shown are the real ones* -- the stagger paces the reveal, never
 * the numbers.
 */
const LIVE_STAGGER_MS = 150;

const live = {
  es: null, queue: [], timer: null, steps: 0, queries: 0, ms: 0,
  startedAt: 0, done: null, finished: false,
};

function liveReset(caseId) {
  live.queue = []; live.steps = 0; live.queries = 0; live.ms = 0;
  live.done = null; live.finished = false; live.startedAt = performance.now();
  clearInterval(live.timer); live.timer = null;
  $('#liveCase').textContent = caseId;
  $('#liveFeed').innerHTML = '';
  $('#liveSteps').textContent = '0';
  $('#liveQueries').textContent = '0';
  $('#liveMs').textContent = '0';
  $('#liveBackend').textContent = '';
  setLiveState('running', 'running');
  $('#livePanel').hidden = false;
}

function setLiveState(text, cls) {
  const n = $('#liveState');
  n.textContent = text;
  n.className = 'tag ' + (cls || '');
}

function liveEnqueue(kind, payload) {
  live.queue.push({ kind, payload });
  if (!live.timer) live.timer = setInterval(liveDrain, LIVE_STAGGER_MS);
}

function liveDrain() {
  const item = live.queue.shift();
  if (!item) {
    if (live.finished) { clearInterval(live.timer); live.timer = null; liveFinish(); }
    return;
  }
  const feed = $('#liveFeed');
  const row = document.createElement('div');
  if (item.kind === 'query') {
    const q = item.payload;
    live.queries += 1;
    live.ms += Number(q.duration_ms || 0);
    row.className = 'lf q' + (q.ok ? '' : ' bad');
    row.innerHTML = `<span class="lf-k">graph</span>
      <span class="lf-ref mono">${esc(q.ref)}</span>
      <span class="lf-res">${esc(q.ok ? (q.summary || '') : ('failed: ' + (q.error || '')))}</span>
      <span class="lf-ms">${Number(q.duration_ms || 0).toFixed(1)} ms</span>`;
    $('#liveQueries').textContent = live.queries;
    $('#liveMs').textContent = Math.round(live.ms);
  } else {
    const s = item.payload;
    live.steps += 1;
    row.className = 'lf s kind-' + esc(s.kind);
    row.innerHTML = `<span class="lf-k">${esc(s.kind)}</span>
      <span class="lf-detail">${esc(s.detail)}</span>`;
    $('#liveSteps').textContent = live.steps;
  }
  feed.appendChild(row);
  feed.scrollTop = feed.scrollHeight;
}

function liveFinish() {
  if (live.done) {
    const a = live.done.answer || {};
    const c = a.case || {};
    // The agent's own measured latency, NOT the wall clock in this browser:
    // that includes LIVE_STAGGER_MS per revealed line and would overstate the
    // investigation severalfold.
    const secs = Number(a.latency_s || 0).toFixed(2);
    setLiveState(`${c.verdict || 'done'} \u00b7 p=${Number(c.fraud_probability || 0).toFixed(2)} \u00b7 ${secs}s`,
      esc(c.verdict || ''));
    renderCase(live.done);
    loadQueue();
  } else {
    setLiveState('failed', 'fraud');
  }
  $('#caseMsg').textContent = '';
  $('#watchBtn').disabled = false;
}

function watchInvestigation(caseId) {
  if (!caseId) return;
  if (live.es) live.es.close();
  if (typeof EventSource === 'undefined') {
    toast('This browser has no EventSource; falling back to a quiet re-run.');
    return rerunQuietly(caseId);
  }
  liveReset(caseId);
  $('#watchBtn').disabled = true;
  const es = new EventSource(`/api/cases/${encodeURIComponent(caseId)}/investigate/stream`);
  live.es = es;

  es.addEventListener('open', (e) => {
    try { $('#liveBackend').textContent = 'backend: ' + JSON.parse(e.data).backend; }
    catch (err) { /* the browser's own open event carries no data */ }
  });
  es.addEventListener('step', (e) => liveEnqueue('step', JSON.parse(e.data)));
  es.addEventListener('query', (e) => liveEnqueue('query', JSON.parse(e.data)));
  es.addEventListener('done', (e) => {
    live.done = JSON.parse(e.data);
    live.finished = true;
    es.close(); live.es = null;
    if (!live.timer) liveFinish();
  });
  es.addEventListener('error', (e) => {
    let msg = 'the connection dropped';
    try { msg = JSON.parse(e.data).message; } catch (err) { /* transport-level error */ }
    live.finished = true;
    es.close(); live.es = null;
    clearInterval(live.timer); live.timer = null;
    setLiveState('failed', 'fraud');
    $('#liveFeed').insertAdjacentHTML('beforeend',
      `<div class="lf s bad"><span class="lf-k">error</span><span class="lf-detail">${esc(msg)}</span></div>`);
    $('#watchBtn').disabled = false;
    $('#caseMsg').textContent = '';
  });
}

async function rerunQuietly(caseId) {
  $('#caseMsg').innerHTML = '<span class="spinner"></span> re-running&hellip;';
  try {
    const rec = await api(`/api/cases/${encodeURIComponent(caseId)}/investigate`, { method: 'POST' });
    $('#caseMsg').textContent = '';
    await loadQueue();
    renderCase(rec);
    toast(`${caseId} re-investigated`);
  } catch (e) { $('#caseMsg').textContent = 'Failed: ' + e.message; }
}

$('#watchBtn').addEventListener('click', () => watchInvestigation($('#casePicker').value));
