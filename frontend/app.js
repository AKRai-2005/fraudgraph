/* Fraud Investigation Console -- vanilla JS, no CDN, works offline. */
'use strict';

const API = '';
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = (n) => '$' + Number(n || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct = (n) => (n == null ? '—' : (Number(n) * 100).toFixed(1) + '%');

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
// The status line is for the system, not the data: which graph is answering,
// whether the LLM is on, and that actions are simulated. That last one is an
// honesty signal, so it is shown on every screen size.
async function loadHealth() {
  const strip = $('#statusStrip');
  try {
    const h = await api('/api/health');
    const g = h.graph || {};
    const tgLive = g.backend === 'tigergraph' && g.ok;
    const why = h.degraded_from
      ? (h.degraded_from === 'mcp' ? 'over MCP requested, unavailable' : 'requested, unavailable')
      : (h.tigergraph_configured ? 'configured, unreachable' : 'not configured');
    const graphTitle = tgLive
      ? 'TigerGraph is answering queries'
      : `TigerGraph ${why}. ${h.degraded_reason || 'The local mirror serves the same query catalogue.'}`;
    // Short labels; the explanation lives in the tooltip. Nav must never be
    // squeezed by status, and "actions: simulated" must never be the item
    // that falls off the end -- it is first in priority, so it goes first.
    strip.innerHTML = [
      `<span class="st" title="Every action executes against a mock service. Nothing reaches a real financial system.">`
        + `<span class="dot warn"></span>Actions <b>simulated</b></span>`,
      `<span class="st" title="${esc(graphTitle)}"><span class="dot ${g.ok ? (tgLive ? 'ok' : 'warn') : 'bad'}"></span>`
        + `Graph <b>${esc(g.backend || '?')}</b>${g.ok ? (tgLive ? '' : ' (fallback)') : ' (down)'}</span>`,
      `<span class="st" title="${esc(h.llm.enabled ? `Narration by ${h.llm.model}; verdicts never come from it` : 'Narration uses deterministic templates')}">`
        + `LLM <b>${h.llm.enabled ? 'on' : 'off'}</b></span>`,
    ].join('');
  } catch (e) {
    strip.innerHTML = `<span class="st"><span class="dot bad"></span>Backend unreachable: ${esc(e.message)}</span>`;
  }
}

/* =============================================================== theme */
// Light is the designed default; dark follows the system unless someone has
// chosen. The chart and graph take their colours from CSS variables, so they
// follow a switch without being redrawn.
const themeBtn = $('#themeToggle');
function currentTheme() {
  const set = document.documentElement.dataset.theme;
  if (set === 'light' || set === 'dark') return set;
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}
function syncThemeLabel() {
  const next = currentTheme() === 'dark' ? 'light' : 'dark';
  themeBtn.setAttribute('aria-label', `Switch to ${next} theme`);
  themeBtn.title = `Switch to ${next} theme`;
}
themeBtn.addEventListener('click', () => {
  const next = currentTheme() === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem('fg-theme', next); } catch (e) { /* private mode: still switches */ }
  syncThemeLabel();
});
syncThemeLabel();

/* ============================================================ overview */
// Shared formatting. One way to write a label/value pair (the ledger), one
// probability format (two decimals), one time format.
const ledgerItem = (label, value, note = '', tone = '') =>
  `<div><dt>${label}</dt><dd class="${tone}">${value}${note ? `<small>${note}</small>` : ''}</dd></div>`;
const humanise = (s) => String(s == null ? '' : s).replace(/_/g, ' ');
const sentence = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : '');
const toneFor = (p) => (p >= 0.7 ? 'fraud' : p >= 0.3 ? 'uncertain' : 'legit');
const fmtTime = (s) => String(s || '').slice(0, 16).replace('T', ' ');
const fmtP = (p) => (p == null ? '&mdash;' : Number(p).toFixed(2));

async function loadOverview() {
  let o;
  try {
    o = await api('/api/overview');
  } catch (e) {
    // this used to `return` silently, leaving the placeholders up for ever
    // with no indication that anything had gone wrong
    showError('#view-overview', 'Could not load the overview', e.message, loadOverview);
    return;
  }
  clearError('#view-overview');

  // Each figure appears once. The verdict counts used to be shown three
  // times over: as tiles, as chips, and again inside the distribution.
  const open = (o.escalated || 0) + (o.open || 0);
  $('#packMeta').textContent = `${o.total_alerts_in_pack} alerts`
    + (o.adhoc_investigations
      ? ` · ${o.adhoc_investigations} ad hoc investigation${o.adhoc_investigations === 1 ? '' : 's'} counted separately`
      : '');
  $('#kpis').innerHTML = [
    ledgerItem('Investigated', o.cases_investigated, `of ${o.total_alerts_in_pack} alerts`),
    ledgerItem('Fraud', o.closed_fraud, 'closed, evidence sufficient', 'fraud'),
    ledgerItem('Legitimate', o.closed_legitimate, 'closed, not corroborated', 'legit'),
    ledgerItem('Open', open, 'awaiting a person', open ? 'uncertain' : ''),
    ledgerItem('Awaiting approval', o.awaiting_approval, 'L1 and L2 actions'),
    ledgerItem('SARs recommended', o.sar_filings_recommended, 'need L2 approval'),
    ledgerItem('Exposure', money(o.total_exposure_usd), 'across identified episodes'),
    ledgerItem('In the graph', `${o.written_to_graph}/${o.cases_investigated}`, 'kept as case memory'),
  ].join('');

  const dist = o.risk_distribution || {};
  const max = Math.max(1, ...Object.values(dist));
  $('#riskDist').innerHTML = Object.entries(dist).map(([band, n]) => {
    const lo = parseFloat(band.split('-')[0]);
    return `<div class="dist-row"><span class="band-label">${esc(band.replace('-', '–'))}</span>
      <span class="bar"><i style="--w:${(n / max) * 100}%;--c:${probColor(lo)}"></i></span>
      <span class="n">${n}</span></div>`;
  }).join('');

  const pats = Object.entries(o.by_pattern || {}).sort((a, b) => b[1] - a[1]);
  $('#verdictSplit').innerHTML = pats.length
    ? `<table><thead><tr><th scope="col">Pattern</th><th scope="col" class="num">Cases</th></tr></thead><tbody>
        ${pats.map(([k, v]) => `<tr><td>${k === 'none' ? '<span class="muted">none identified</span>' : esc(humanise(k))}</td>
          <td class="num">${v}</td></tr>`).join('')}</tbody></table>`
    : '<p class="muted">No investigations yet.</p>';

  // Case ids in the log are real buttons: they used to be <span>s with a
  // click handler, which a keyboard could not reach.
  $('#recentActivity').innerHTML = (o.recent_activity || []).map((a) => `
    <div class="log-row">
      <button class="linkish mono" type="button" data-case="${esc(a.case_id)}">${esc(a.case_id)}</button>
      <span class="log-kind">${esc(humanise(a.kind))}</span>
      <span class="log-detail">${esc(a.detail)}</span>
    </div>`).join('')
    || '<p class="muted">No investigations have been run yet.</p>';
  $$('#recentActivity button[data-case]').forEach((b) =>
    b.addEventListener('click', () => openCase(b.dataset.case)));

  loadDivergence();
}

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
    $('#divergenceStats').innerHTML = `<span class="muted">The chart could not be loaded: ${esc(e.message)}</span>`;
    return;
  }
  state.divergence = d;
  const total = d.n_scored;
  const moved = d.escalated + d.cleared;
  // The finding, stated as a sentence built from the data. It replaces three
  // stat tiles that showed the same numbers without saying what they meant.
  $('#divergenceStats').innerHTML = total
    ? `Of <span class="n">${total}</span> alerts, investigating against the graph moved
       <span class="n">${moved}</span> away from the bank's score:
       <span class="n fraud">${d.escalated}</span> escalated on evidence the score missed, and
       <span class="n legit">${d.cleared}</span> cleared because nothing in the graph stood behind a high score.
       ${d.agreed} agreed.`
    : 'No scored alerts have been investigated yet.';
  drawDivergence(d);
  renderCallouts(d);
}

function drawDivergence(d) {
  const svg = $('#divergenceChart');
  svg.innerHTML = '';
  if (!d.points.length) {
    $('#divergenceChart').appendChild(el('text', { x: 20, y: 30, fill: 'var(--ink-3)', 'font-size': 12 },
      'No scored alerts have been investigated yet.'));
    return;
  }
  // The viewBox is scaled to the container, so a fixed 1000-unit width means
  // 11-unit text renders at under 4px on a phone. Narrow screens get their own
  // geometry: fewer units across, so every unit is worth more pixels.
  const narrow = (svg.clientWidth || 800) < 560;
  const W = narrow ? 420 : 1000;
  const H = narrow ? 400 : 430;
  const M = narrow ? { t: 16, r: 14, b: 48, l: 46 } : { t: 22, r: 24, b: 52, l: 58 };
  // sized so the rendered text stays at or above 12px at the widths each
  // geometry is used at
  const FS = narrow ? { tick: 15, axis: 15, hint: 0, label: 16 }
                    : { tick: 12, axis: 13, hint: 12, label: 13 };
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
  g.appendChild(el('polygon', { points: poly, fill: 'var(--band)', stroke: 'none' }));
  g.appendChild(el('line', {
    x1: X(0), y1: Y(0), x2: X(1), y2: Y(1),
    stroke: 'var(--rule-strong)', 'stroke-width': 1, 'stroke-dasharray': '4 4',
  }));

  // gridlines and ticks
  for (let i = 0; i <= 5; i++) {
    const v = i / 5;
    g.appendChild(el('line', { x1: X(0), y1: Y(v), x2: X(1), y2: Y(v), stroke: 'var(--grid)', 'stroke-width': 1 }));
    g.appendChild(el('line', { x1: X(v), y1: Y(0), x2: X(v), y2: Y(1), stroke: 'var(--grid)', 'stroke-width': 1 }));
    if (narrow && i % 2) continue;   // every other tick, or they collide
    g.appendChild(el('text', { x: M.l - 8, y: Y(v) + 4, fill: 'var(--ink-3)', 'font-size': FS.tick, 'text-anchor': 'end' }, v.toFixed(1)));
    g.appendChild(el('text', { x: X(v), y: H - M.b + 20, fill: 'var(--ink-3)', 'font-size': FS.tick, 'text-anchor': 'middle' }, v.toFixed(1)));
  }
  g.appendChild(el('text', { x: M.l + iw / 2, y: H - 8, fill: 'var(--ink-2)', 'font-size': FS.axis, 'text-anchor': 'middle' },
    narrow ? "Bank model's risk score" : "Bank model's risk score at the time of the alert"));
  const ylx = narrow ? 13 : 14;
  const yl = el('text', { x: ylx, y: M.t + ih / 2, fill: 'var(--ink-2)', 'font-size': FS.axis, 'text-anchor': 'middle' },
    narrow ? "Agent's probability" : "Agent's assessed probability");
  yl.setAttribute('transform', `rotate(-90 ${ylx} ${M.t + ih / 2})`);
  g.appendChild(yl);

  // quadrant hints
  if (!narrow) {
    g.appendChild(el('text', { x: X(0.02), y: Y(0.86), fill: 'var(--ink-3)', 'font-size': FS.hint },
      'low score, high evidence — the model missed it'));
    g.appendChild(el('text', { x: X(0.98), y: Y(0.14), fill: 'var(--ink-3)', 'font-size': FS.hint, 'text-anchor': 'end' },
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
        stroke: VERDICT_COLOR[p.verdict] || 'var(--ink-3)', 'stroke-width': 1.5, opacity: 0.35,
      }));
    }
    node.appendChild(el('circle', {
      cx, cy, r: narrow ? (far ? 9 : 7) : (far ? 8 : 6),
      fill: VERDICT_COLOR[p.verdict] || 'var(--ink-3)',
      'fill-opacity': far ? 0.9 : 0.5,
      stroke: 'var(--sheet)', 'stroke-width': 1.5,
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
      fill: VERDICT_COLOR[p.verdict] || 'var(--ink)',
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
  // Margin notes on the chart, not cards: a rule in the verdict's colour, the
  // move from score to assessment, the reason, and a visible way in.
  const note = (p, kind) => {
    if (!p) return '';
    const dr = p.driver;
    return `<button class="callout ${kind}" type="button" data-case="${esc(p.case_id)}">
      <span class="callout-label">${kind === 'up' ? 'Missed by the score' : 'A false alarm'} &middot;
        <span class="mono">${esc(p.case_id)}</span></span>
      <span class="callout-move">
        <span class="from" title="bank score">${p.bank_risk_score.toFixed(2)}</span>
        <span aria-hidden="true">&rarr;</span>
        <span class="to ${esc(p.verdict)}" title="agent's probability">${p.fraud_probability.toFixed(2)}</span>
        <span class="stamp ${esc(p.verdict)}">${esc(p.verdict)}</span>
      </span>
      <span class="callout-why">${dr ? `Moved most by ${esc(signalText(dr.signal))}. ` : ''}${esc(sentence(p.note || ''))}</span>
      <span class="callout-open">Open ${esc(p.case_id)}</span>
    </button>`;
  };
  box.innerHTML = '<h3>The two largest disagreements</h3>' + note(up, 'up') + note(down, 'down');
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
  const total = state.queue.length;
  $('#queueCount').textContent = rows.length === total
    ? `${total} case${total === 1 ? '' : 's'}`
    : `${rows.length} of ${total} cases`;
  $$('#queueTable th[data-sort]').forEach((th) => {
    if (th.dataset.sort === key) th.setAttribute('aria-sort', dir > 0 ? 'ascending' : 'descending');
    else th.removeAttribute('aria-sort');
  });

  // data-label drives the narrow layout: under 960px each cell prints its own
  // heading, because a 12-column analyst table cannot shrink to 320px and
  // remain a table. The case id is a real button -- the row click is a
  // convenience for the mouse, not the only way in.
  $('#queueTable tbody').innerHTML = rows.map((r) => {
    const p = Number(r.fraud_probability || 0);
    const pending = (r.awaiting_approval || []).length;
    return `
    <tr data-case="${esc(r.case_id)}">
      <td data-label="Case"><button class="linkish mono" type="button">${esc(r.case_id)}</button>${r.adhoc
        ? '<span class="tag tag-under" title="Opened by an analyst on an arbitrary transaction, not one of the challenge alerts">ad hoc</span>' : ''}</td>
      <td class="wrap" data-label="Trigger">${esc(humanise(r.trigger_type))}</td>
      <td class="mono" data-label="Card">${esc(r.card_id)}</td>
      <td class="num" data-label="Bank score">${r.bank_risk_score == null ? '<span class="muted">none</span>' : fmtP(r.bank_risk_score)}</td>
      <td class="num" data-label="Agent p(fraud)"><span class="prob"><i style="--w:${Math.round(p * 100)}%;--c:${probColor(p)}"></i>${fmtP(p)}</span></td>
      <td class="num opt" data-label="Confidence">${r.confidence == null ? '<span class="muted">&mdash;</span>' : fmtP(r.confidence)}</td>
      <td data-label="Verdict"><span class="stamp ${esc(r.verdict)}">${esc(r.verdict)}</span></td>
      <td class="wrap" data-label="Pattern">${r.pattern === 'none' ? '<span class="muted">none</span>' : esc(humanise(r.pattern))}</td>
      <td class="num" data-label="Exposure">${r.exposure_usd ? money(r.exposure_usd) : '<span class="muted">&mdash;</span>'}</td>
      <td class="mono" data-label="Next action">${esc(r.next_action || '—')}</td>
      <td data-label="Approval">${pending ? `<span class="tag pending">${pending} pending</span>` : '<span class="muted">&mdash;</span>'}</td>
      <td class="muted opt" data-label="Updated">${esc(fmtTime(r.last_updated))}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="12" class="empty">No cases match this filter.</td></tr>';
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
  const p = Number(c.fraud_probability || 0);

  // --- the answer first. The verdict used to share one heading size with the
  // tool-call log; now the case reads top-down: what was concluded, in words,
  // with the figures under it, then the evidence, then what to do.
  const alerts = [];
  if (c.pattern_description) {
    alerts.push(`<div class="alert"><b>Undocumented pattern.</b> ${esc(c.pattern_description)}</div>`);
  }
  if ((risk.conflicting_evidence || []).length) {
    alerts.push(`<div class="alert"><b>Conflicting evidence.</b> ${esc(risk.conflicting_evidence.join('; '))}</div>`);
  }
  const head = `
    <header class="case-head">
      <div class="case-id">
        <h1>${esc(rec.case_id)}</h1>
        <span class="stamp lg ${esc(c.verdict)}">${esc(c.verdict)}</span>
        <span class="tag">${esc(humanise(c.status))}</span>
        <span class="meta">${a.tool_calls} graph calls &middot; ${Number(a.latency_s || 0).toFixed(2)}s
          &middot; ${a.tokens ? `${Number(a.tokens).toLocaleString()} LLM tokens` : 'no LLM narration'}</span>
      </div>
      <p class="case-trigger">${esc(trig.trigger_text || '')}</p>
      <p class="prose lead">${esc(c.summary)}</p>
      <dl class="ledger compact">
        ${ledgerItem('Agent p(fraud)', fmtP(p), '', toneFor(p))}
        ${ledgerItem('Bank score', risk.bank_risk_score == null ? '<span class="muted">none</span>' : fmtP(risk.bank_risk_score))}
        ${ledgerItem('Confidence', fmtP(risk.confidence || 0))}
        ${ledgerItem('Independent signals', risk.independent_signal_count ?? 0)}
        ${ledgerItem('Exposure', money(c.exposure_usd))}
        ${ledgerItem('Transactions', (c.affected_txn_ids || []).length)}
        ${ledgerItem('Pattern', c.pattern === 'none' ? 'none' : esc(humanise(c.pattern)))}
        ${ledgerItem('In the graph', c.written_to_graph ? 'yes' : 'no')}
      </dl>
      ${(risk.uncertainty_notes || []).length
        ? `<p class="sec-note"><b>What remains uncertain:</b> ${esc(risk.uncertainty_notes.join('; '))}</p>` : ''}
      ${alerts.join('')}
      ${provenanceBanner(rec)}
    </header>`;

  // --- the evidence
  const evidence = (c.evidence || []).map((e) => {
    const ids = e.entity_ids || [];
    const idText = ids.length
      ? ' &middot; ' + esc(ids.slice(0, 8).join(', ')) + (ids.length > 8 ? ` +${ids.length - 8}` : '')
      : '';
    return `<div class="ev source-${esc(e.source)}">
        <p class="ev-claim">${esc(e.claim)}</p>
        <p class="ev-meta"><span class="src">${esc(e.source)}</span> &middot; ${esc(e.ref)}${idText}</p>
      </div>`;
  }).join('');

  const findings = rec.findings || [];
  const matched = findings.filter((f) => f.matched);
  const quiet = findings.filter((f) => !f.matched);
  const detRow = (f) => `
    <div class="det ${f.matched ? 'matched' : ''}">
      <span class="det-name">${esc(f.name)}<span class="s">${f.matched ? `matched &middot; ${fmtP(f.strength)}` : 'no match'}</span></span>
      <span class="det-why">${esc(f.why)}${f.limitations ? `<span class="det-lim">Cannot rule out: ${esc(f.limitations)}</span>` : ''}</span>
    </div>`;

  const main = `
    <section class="sec">
      <div class="sec-head"><h2>Evidence</h2>
        <span class="sec-meta">${(c.evidence || []).length} claims, each with the query that produced it</span></div>
      <div class="ev-list">${evidence || '<p class="muted">No evidence recorded.</p>'}</div>
    </section>

    <section class="sec">
      <div class="sec-head"><h2>The graph around the alert</h2><span class="sec-meta" id="graphMeta"></span></div>
      <div class="graph-frame">
        <svg id="graphSvg" role="img" aria-label="Entities connected to ${esc(rec.case_id)}"></svg>
        <div class="graph-controls"><button class="btn sm" id="graphReset" type="button">Reset view</button></div>
        <div class="tip" id="graphTip"></div>
      </div>
      <div class="graph-legend" id="graphLegend"></div>
    </section>

    <section class="sec">
      <div class="sec-head"><h2>Pattern detectors</h2>
        <span class="sec-meta">${matched.length} of ${findings.length} matched</span></div>
      <p class="sec-note">Every detector runs on every case, and each states what it cannot rule out.</p>
      <div class="detectors">${matched.map(detRow).join('') || '<p class="muted">None matched.</p>'}</div>
      ${quiet.length ? `<details><summary>${quiet.length} detector${quiet.length === 1 ? '' : 's'} did not match</summary>
        <div class="detectors det-group">${quiet.map(detRow).join('')}</div></details>` : ''}
    </section>`;

  // --- the decision. The final actions and their approvals were listed twice,
  // in "Next best action" and again in "Approvals"; they are one list now.
  const initial = nba.initial || [];
  const reqs = rec.evidence_requests_full || a.evidence_requests || [];
  const prior = c.similar_prior_cases || [];
  const connected = c.connected_card_ids || [];
  const devices = c.connected_device_profiles || [];
  const timeline = rec.timeline || [];
  const toolLog = rec.tool_log || [];

  const aside = `
    <aside class="aside" aria-label="Decision">
      <section class="sec">
        <div class="sec-head"><h2>Decision</h2></div>
        <p class="sec-note">The agent may carry out <span class="tag route auto">auto</span> actions itself;
          <span class="tag route L1">L1</span> and <span class="tag route L2">L2</span> wait for a person.
          Execution is simulated and reaches no real financial system.</p>
        <div id="approvalList" class="actions">${approvalList(rec)}</div>
        ${initial.length ? `<div class="sub"><h3>Before the evidence request</h3>
          <ul class="initial-list">${initial.map((x) =>
            `<li><span class="mono">${esc(x.action)}</span> <span class="tag route ${esc(x.route)}">${esc(x.route)}</span></li>`).join('')}</ul>
          <p class="changed sub-tight"><b>What changed:</b> ${esc(nba.what_changed || 'nothing')}</p></div>` : ''}
        <p class="sec-note sub"><b>Why it stopped:</b> ${esc(a.stop_reason)}</p>
      </section>

      <section class="sec">
        <div class="sec-head"><h2>Suspicious activity report</h2>
          ${sar.file ? '<span class="tag fraud">filing recommended</span>' : '<span class="tag">not due</span>'}</div>
        ${sar.file
          ? `<p class="sec-note">Filing needs L2 approval. ${esc(sar.reason)}</p>
             <p class="prose">${esc(sar.narrative)}</p>
             <dl class="kv sub">
               <dt>Subjects</dt><dd class="mono">${esc((sar.subjects || []).join(', '))}</dd>
               <dt>Total</dt><dd>${money(sar.total_amount_usd)}</dd>
               <dt>Activity</dt><dd>${esc((sar.activity_dates || []).join(' to '))}</dd>
             </dl>`
          : `<p class="sec-note">${esc(sar.reason)}</p>`}
      </section>

      ${reqs.length ? `
      <section class="sec">
        <div class="sec-head"><h2>Evidence requests</h2><span class="tag">simulated</span></div>
        <p class="sec-note">The challenge supplies no customer or analyst replies. The responses below are
          simulated under a published rule, and say so.</p>
        ${reqs.map((r) => `
          <div class="req">
            <p class="req-head">${esc(humanise(r.type))} <span class="muted">after step ${r.asked_after_step}</span></p>
            ${r.reason ? `<p>${esc(r.reason)}</p>` : ''}
            ${r.information_required ? `<p><b>Asked:</b> ${esc(r.information_required)}</p>` : ''}
            <p><b>Assumed reply:</b> ${esc(r.assumed_response)}</p>
            ${r.assumption_basis ? `<p class="basis">Basis: ${esc(r.assumption_basis)}</p>` : ''}
            ${r.effect_on_investigation ? `<p class="basis">Effect: ${esc(r.effect_on_investigation)}</p>` : ''}
          </div>`).join('')}
      </section>` : ''}

      <section class="sec">
        <div class="sec-head"><h2>Case memory used</h2></div>
        <p class="sec-note">Closed investigations retrieved as context, kept apart from what this investigation found.</p>
        ${prior.length
          ? `<div class="id-list">${prior.map((id) => `<span>${esc(id)}</span>`).join('')}</div>`
          : '<p class="muted">No comparable prior investigation was retrieved.</p>'}
        ${connected.length ? `<div class="sub"><h3>Connected cards (${connected.length})</h3>
          <div class="id-list">${connected.slice(0, 30).map((x) => `<span>${esc(x)}</span>`).join('')}</div></div>` : ''}
        ${devices.length ? `<div class="sub"><h3>Shared device profile</h3>
          ${devices.map((x) => `<p class="mono muted">${esc(x)}</p>`).join('')}</div>` : ''}
      </section>

      <section class="sec">
        <div class="sec-head"><h2>Agent log</h2><span class="sec-meta">${timeline.length} steps</span></div>
        <div class="timeline">${timeline.map((t) => `
          <div class="tl kind-${esc(t.kind)}"><span class="step">${t.step}</span>
            <span class="k">${esc(humanise(t.kind))}</span><span class="d">${esc(t.detail)}</span></div>`).join('')}</div>
        <details><summary>Graph calls (${toolLog.length})</summary>
          <pre class="code">${esc(toolLog.map((t) =>
            `${String(t.step).padStart(2)}  ${t.ok ? 'ok  ' : 'FAIL'}  ${String(t.duration_ms).padStart(7)}ms  ${t.backend}  ${t.ref}\n      -> ${t.result_summary}${t.error ? '\n      !! ' + t.error : ''}`).join('\n'))}</pre>
        </details>
        <details><summary>Risk breakdown</summary><pre class="code">${esc(JSON.stringify(rec.risk, null, 2))}</pre></details>
      </section>
    </aside>`;

  $('#caseBody').innerHTML = head + `<div class="grid-main"><div>${main}</div>${aside}</div>`;
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
  const when = fmtTime(prov.at || rec.generated_at);
  const backend = prov.backend ? `<b>${esc(prov.backend)}</b>` : '';
  if (prov.kind !== 'rerun') {
    return `<p class="provenance">Published answer file
      ${backend ? `&middot; served by ${backend}` : ''}${when ? ` &middot; generated ${esc(when)}` : ''}
      &middot; <span class="mono">cases/${esc(rec.case_id)}.json</span></p>`;
  }
  // The narrator samples its wording, so a correct re-run differs on
  // case.summary and sar.narrative while agreeing on every fact underneath.
  // Prose drift and evidence drift are reported separately.
  const factsMatch = drift.facts_match !== false;
  const prose = drift.n_prose_differences || 0;
  const rows = (drift.differences || []).slice(0, 6).map((d) =>
    `<li><span class="p">${esc(d.path)}</span><br>${esc(String(d.published).slice(0, 90))}
     &rarr; ${esc(String(d.current).slice(0, 90))}</li>`).join('');
  const verdictText = factsMatch
    ? (prose
        ? `evidence identical to the published answer; ${prose} narrated field${prose === 1 ? '' : 's'} reworded by the LLM`
        : 'identical to the published answer')
    : `<b>${drift.n_differences || 0} evidence field${drift.n_differences === 1 ? '' : 's'} differ</b> from the published answer`;
  return `<div class="provenance ${factsMatch ? '' : 'drifted'}">
    Live re-run &middot; served by ${backend || '<b>?</b>'}${when ? ` &middot; ${esc(when)}` : ''} &middot; ${verdictText}.
    The file in cases/ is unchanged; re-runs never write to it.
    ${factsMatch ? '' : `<ul class="drift-list">${rows}</ul>`}
  </div>`;
}

function approvalList(rec) {
  const finals = (rec.actions_full || {}).final || [];
  const appr = rec.approvals || {};
  const rows = finals.map((x) => {
    const done = appr[x.action];
    let ctl;
    if (x.route === 'auto') {
      ctl = `<span class="muted">${esc(x.status === 'recommended' ? 'agent may execute' : x.status)}</span>`;
    } else if (done) {
      ctl = `<span class="tag ${done.decision === 'approved' ? 'done-yes' : 'done-no'}">${esc(done.decision)} by ${esc(done.approver)}</span>`;
    } else {
      ctl = `<span class="approve">
          <label class="sr-only" for="appr-${esc(x.action)}">Your name, to approve or reject ${esc(x.action)}</label>
          <input class="appr-name" id="appr-${esc(x.action)}" data-action="${esc(x.action)}" placeholder="Your name" autocomplete="name">
          <button class="btn sm primary appr-yes" type="button" data-action="${esc(x.action)}">Approve</button>
          <button class="btn sm appr-no" type="button" data-action="${esc(x.action)}">Reject</button>
        </span>`;
    }
    return `<div class="act-row">
        <div>
          <div class="act-name">${esc(x.action)} <span class="tag route ${esc(x.route)}">${esc(x.route)}</span></div>
          <p class="act-why">${esc(x.reason)}</p>
          ${done && done.execution ? `<p class="act-exec">Simulated execution ${esc(done.execution.execution_id)}: would have ${esc(done.execution.would_have)}</p>` : ''}
        </div>
        <div>${ctl}</div>
      </div>`;
  }).join('') || '<p class="muted">No action recommended.</p>';
  const execs = rec.executions || [];
  return rows + (execs.length
    ? `<details><summary>Simulated execution log (${execs.length})</summary><pre class="code">${esc(JSON.stringify(execs, null, 2))}</pre></details>`
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
// Colours are CSS variables, so the graph follows the theme without a redraw.
const NODE_STYLE = {
  Card: { r: 9, c: 'var(--n-card)' },
  Customer: { r: 10, c: 'var(--n-customer)' },
  Transaction: { r: 5, c: 'var(--n-txn)' },
  DeviceProfile: { r: 9, c: 'var(--n-device)' },
  BillingRegion: { r: 7, c: 'var(--n-region)' },
  ClosedCase: { r: 8, c: 'var(--n-case)' },
  AgentCase: { r: 11, c: 'var(--n-agent)' },
};
const NODE_LABEL = {
  Card: 'card', Customer: 'customer', Transaction: 'transaction', DeviceProfile: 'device',
  BillingRegion: 'billing region', ClosedCase: 'closed case', AgentCase: 'this case',
};

async function loadGraph(caseId) {
  const svg = $('#graphSvg');
  if (!svg) return;
  svg.innerHTML = '<text x="16" y="28" fill="var(--ink-3)" font-size="13">Loading the graph&#8230;</text>';
  let g;
  try { g = await api(`/api/cases/${encodeURIComponent(caseId)}/graph`); } catch (e) {
    svg.innerHTML = `<text x="16" y="28" fill="var(--fraud)" font-size="13">The graph could not be loaded: ${esc(e.message)}</text>`;
    return;
  }
  state.graph = g;
  $('#graphMeta').textContent = `${g.nodes.length} nodes · ${g.edges.length} edges${g.truncated ? ' (truncated)' : ''} · served by ${g.backend}`;
  $('#graphLegend').innerHTML = Object.entries(NODE_STYLE)
    .map(([k, v]) => `<span><i style="background:${v.c}"></i>${NODE_LABEL[k] || k}</span>`).join('')
    + '<span><i class="ring-flag"></i>flagged transaction</span>'
    + '<span><i class="ring-ep"></i>part of the episode</span>'
    + '<span>Drag to pan, scroll to zoom.</span>';
  drawGraph(g);
}

function drawGraph(g) {
  const svg = $('#graphSvg');
  if (!svg) return;
  const W = svg.clientWidth || 800, H = svg.clientHeight || 420;
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
    parts.push(`<line x1="${l.s.x.toFixed(1)}" y1="${l.s.y.toFixed(1)}" x2="${l.t.x.toFixed(1)}" y2="${l.t.y.toFixed(1)}" stroke="var(--rule-strong)" stroke-width="1"/>`);
  });
  nodes.forEach((n, i) => {
    const st = NODE_STYLE[n.kind] || { r: 6, c: 'var(--n-txn)' };
    let stroke = 'var(--sheet)', sw = 1.5;
    if (n.flagged) { stroke = 'var(--fraud)'; sw = 3; }
    else if (n.affected) { stroke = 'var(--uncertain)'; sw = 2.5; }
    else if (n.focus || n.shared || n.connected) { stroke = 'var(--ink)'; sw = 1.5; }
    parts.push(`<circle data-i="${i}" cx="${n.x.toFixed(1)}" cy="${n.y.toFixed(1)}" r="${st.r}" fill="${st.c}" stroke="${stroke}" stroke-width="${sw}"/>`);
    if (n.kind !== 'Transaction' || n.flagged) {
      parts.push(`<text x="${(n.x + st.r + 4).toFixed(1)}" y="${(n.y + 4).toFixed(1)}" fill="var(--ink-2)" font-size="12" pointer-events="none">${esc(String(n.label).slice(0, 22))}</text>`);
    }
  });
  parts.push('</g>');
  svg.innerHTML = parts.join('');

  // Pan and zoom on pointer events, so touch works too. The mouse version
  // pinned a new window-level mouseup listener on every redraw.
  let tx = 0, ty = 0, scale = 1, dragging = false, lx = 0, ly = 0;
  const root = $('#gRoot', svg);
  const apply = () => root.setAttribute('transform', `translate(${tx},${ty}) scale(${scale})`);
  svg.onpointerdown = (e) => {
    dragging = true; lx = e.clientX; ly = e.clientY;
    try { svg.setPointerCapture(e.pointerId); } catch (err) { /* synthetic events */ }
  };
  svg.onpointerup = svg.onpointercancel = () => { dragging = false; };
  svg.onpointermove = (e) => {
    if (!dragging) return;
    tx += e.clientX - lx; ty += e.clientY - ly; lx = e.clientX; ly = e.clientY; apply();
  };
  svg.onwheel = (e) => {
    e.preventDefault();
    const f = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    scale = Math.max(0.3, Math.min(4, scale * f));
    apply();
  };
  $('#graphReset').onclick = () => { tx = 0; ty = 0; scale = 1; apply(); };

  const tip = $('#graphTip');
  $$('#graphSvg circle').forEach((c) => {
    c.addEventListener('pointerenter', (e) => {
      const n = nodes[Number(c.dataset.i)];
      const bits = [`${NODE_LABEL[n.kind] || n.kind}: ${n.label}`];
      if (n.amount != null) bits.push(`${money(n.amount)} · ${n.ts}`, `${n.channel} · product ${n.product} · bank score ${n.risk}`);
      if (n.proxy) bits.push(`proxy ${n.proxy}`);
      if (n.device_new) bits.push(`device ${n.device_new} for this account`);
      if (n.flagged) bits.push('the flagged transaction');
      if (n.affected) bits.push('part of the identified episode');
      if (n.verdict) bits.push(`verdict ${n.verdict}, p=${n.probability}`, `in the graph: ${n.written}`);
      tip.innerHTML = bits.map(esc).join('<br>');
      tip.style.display = 'block';
      const r = svg.getBoundingClientRect();
      tip.style.left = Math.max(4, Math.min(r.width - 250, e.clientX - r.left + 12)) + 'px';
      tip.style.top = (e.clientY - r.top + 12) + 'px';
    });
    c.addEventListener('pointerleave', () => { tip.style.display = 'none'; });
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
    return `<section class="sec">
      <div class="sec-head"><h2>Are the verdicts right?</h2></div>
      <p class="sec-note">Not measured yet. The agent's parts are measured; its verdicts are not.</p>
      <pre class="code">${esc((bt && bt.how) || 'python -m fraudgraph.analysis.backtest --all-modes')}</pre>
    </section>`;
  }
  const runs = bt.runs || {};
  const keys = BT_ORDER.filter((k) => runs[k]).concat(
    Object.keys(runs).filter((k) => !BT_ORDER.includes(k)));
  const headline = runs['neutral:undocumented+card_testing'] || runs.neutral;
  const leaks = keys.reduce((n, k) => n + (runs[k].leakage_violations || 0), 0);
  const RUN_LABEL = {
    'neutral:undocumented+card_testing': 'Neutral prior · every relational-fraud case',
    neutral: 'Neutral prior · random sample',
    score: 'Model-score prior · the exam operating point',
    actual: 'Real trigger · partly circular, see caveats',
  };

  // Four runs compared on the same measures is a table. It had been four
  // cards, each holding four tiles and a table of its own.
  const rows = keys.map((k) => {
    const r = runs[k];
    const d = r.uncertain_as_negative || {};
    const auc = (r.auc && r.auc.agent_probability) != null ? Number(r.auc.agent_probability).toFixed(3) : '&mdash;';
    return `<tr><td>${esc(RUN_LABEL[k] || k)}</td>
      <td class="num">${r.n_scored}</td>
      <td class="num">${pct(d.recall_on_fraud)}</td>
      <td class="num">${pct(d.specificity_on_cleared)}</td>
      <td class="num">${d.fp == null ? '&mdash;' : d.fp}</td>
      <td class="num">${auc}</td></tr>`;
  }).join('');

  const bp = (headline && headline.recall_by_true_pattern) || {};
  const typRows = Object.entries(bp).map(([pat, v]) => `<tr>
      <td>${esc(humanise(pat))}</td>
      <td class="num">${v.called_fraud} of ${v.n}</td>
      <td class="num">${pct(v.recall)}</td>
      <td class="num">${fmtP(v.mean_probability)}</td></tr>`).join('');
  const und = bp.undocumented;
  const hd = (headline && headline.uncertain_as_negative) || {};
  const caveats = (headline && headline.caveats) || [];

  return `<section class="sec">
    <div class="sec-head"><h2>Are the verdicts right?</h2>
      <span class="tag ${leaks ? 'fraud' : 'legitimate'}">${leaks ? `${leaks} leakage violation${leaks === 1 ? '' : 's'}` : 'no leakage'}</span></div>
    <p class="sec-note">Closed investigations with known outcomes, replayed through the whole agent, with case
      memory and transaction windows cut off at the moment each case was opened.</p>
    ${und ? `<p class="prose">With no prior on either side, so that only graph evidence could move the answer,
      the agent called <b>${und.called_fraud} of ${und.n}</b> cases of the typologies the challenge does not
      document fraud, against <b>${hd.fp}</b> false fraud calls on
      ${(headline.class_counts || {}).cleared} legitimate alerts that the bank's model had scored high.</p>` : ''}
    <div class="table-wrap sheet sub fit"><table>
      <thead><tr><th scope="col">Run</th><th scope="col" class="num">Cases</th><th scope="col" class="num">Recall on fraud</th>
        <th scope="col" class="num">Specificity</th><th scope="col" class="num">False fraud calls</th><th scope="col" class="num">AUC</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    ${typRows ? `<div class="sub"><h3>Relational fraud, by the typology the analyst recorded</h3>
      <div class="table-wrap sheet fit"><table>
        <thead><tr><th scope="col">Typology</th><th scope="col" class="num">Called fraud</th>
          <th scope="col" class="num">Recall</th><th scope="col" class="num">Mean p</th></tr></thead>
        <tbody>${typRows}</tbody></table></div></div>` : ''}
    ${caveats.length ? `<details><summary>What these numbers do not mean (${caveats.length})</summary>
      <ul class="caveats">${caveats.map((c) => `<li>${esc(c)}</li>`).join('')}</ul></details>` : ''}
    <p class="sec-meta sub">Generated ${esc(fmtTime(bt.generated_at))} &middot;
      <span class="mono">python -m fraudgraph.analysis.backtest --all-modes</span></p>
  </section>`;
}

/* ============================================================== memory */
const loadingBlock = '<p class="empty"><span class="spinner"></span> Loading&hellip;</p>';

async function loadMemory() {
  const body = $('#memoryBody');
  body.innerHTML = loadingBlock;
  try {
    const m = await api('/api/memory');
    const h = m.historical, ag = m.agent_written;
    const pats = Object.entries(h.by_pattern || {}).sort((a, b) => b[1] - a[1]);
    body.innerHTML = `
      <div class="grid-2">
        <section class="sec">
          <div class="sec-head"><h2>From the dataset</h2>
            <span class="sec-meta">${esc(h.date_range[0])} to ${esc(h.date_range[1])}</span></div>
          <p class="sec-note">Read-only ground truth. Retrieved as context for a new alert, never used as its verdict.</p>
          <dl class="ledger compact fit">
            ${ledgerItem('Closed investigations', h.total.toLocaleString())}
            ${ledgerItem('Confirmed fraud', h.confirmed_fraud.toLocaleString(), '', 'fraud')}
            ${ledgerItem('Cleared', h.cleared.toLocaleString(), '', 'legit')}
          </dl>
          <div class="sub"><h3>By pattern</h3>
            <table><thead><tr><th scope="col">Pattern</th><th scope="col" class="num">Cases</th></tr></thead><tbody>
            ${pats.map(([k, v]) => `<tr><td>${k === 'none' ? '<span class="muted">none (cleared)</span>' : esc(humanise(k))}</td>
              <td class="num">${v.toLocaleString()}</td></tr>`).join('')}</tbody></table></div>
        </section>
        <section class="sec">
          <div class="sec-head"><h2>Written by the agent</h2></div>
          <p class="sec-note">Investigations this system closed. They become memory for the next alert.</p>
          <dl class="ledger compact fit">
            ${ledgerItem('Cases', ag.total)}
            ${ledgerItem('In TigerGraph', ag.written_to_graph, '', ag.written_to_graph ? 'legit' : 'uncertain')}
            ${ledgerItem('Write attempts logged', ag.write_attempts_logged)}
          </dl>
          <p class="sec-note sub">The journal is append-only, so a case re-run several times has several
            entries; the table shows the latest for each.</p>
          ${ag.total === 0 ? '<p class="muted">No agent cases yet.</p>' : ''}
          ${ag.written_to_graph === 0 && ag.total
            ? `<div class="alert"><b>None of these are in TigerGraph.</b> The local mirror is read-only and
                reports the write as refused rather than claiming one it did not make.</div>` : ''}
        </section>
      </div>
      <section class="sec">
        <div class="sec-head"><h2>Latest agent cases</h2></div>
        <div class="table-wrap sheet"><table>
          <thead><tr><th scope="col">Case</th><th scope="col">Verdict</th><th scope="col" class="num">p(fraud)</th>
            <th scope="col">Pattern</th><th scope="col" class="num">Exposure</th><th scope="col">In graph</th></tr></thead>
          <tbody>
          ${(ag.cases || []).map((row) => {
            const cse = row.case || row;
            return `<tr><td class="mono">${esc(cse.case_id || cse.graph_case_id)}</td>
              <td><span class="stamp ${esc(cse.verdict)}">${esc(cse.verdict)}</span></td>
              <td class="num">${fmtP(cse.fraud_probability || 0)}</td>
              <td>${cse.pattern === 'none' ? '<span class="muted">none</span>' : esc(humanise(cse.pattern))}</td>
              <td class="num">${money(cse.exposure_usd)}</td>
              <td>${row.written_to_graph ? 'yes' : '<span class="muted">no</span>'}</td></tr>`;
          }).join('') || '<tr><td colspan="6" class="empty">None yet.</td></tr>'}
          </tbody></table></div>
      </section>`;
  } catch (e) {
    body.innerHTML = '';
    showError('#view-memory', 'Could not load case memory', e.message, loadMemory);
  }
}

/* ======================================================== model/policy */
async function loadModel() {
  const body = $('#modelBody');
  body.innerHTML = loadingBlock;
  try {
    const [m, p, q] = await Promise.all([api('/api/model-card'), api('/api/policy'), api('/api/ingest-quality')]);
    const met = m.metrics || {};
    const basis = (m.weight_basis || []).slice().sort((a, b) => Math.abs(b.weight) - Math.abs(a.weight));
    const maxW = Math.max(1e-6, ...basis.map((b) => Math.abs(b.weight)));
    const clip = m.max_abs_weight || 2.6;
    const notes = Object.entries(m.notes || {}).filter(([k, v]) => v && k !== 'design');
    const counts = (q && q.counts) || {};
    const checks = Object.entries(q || {}).filter(([k, v]) => v && typeof v === 'object' && !Array.isArray(v) && k !== 'counts');
    const errs = (q && q.errors) || [], warns = (q && q.warnings) || [];

    // Weights are shown once, as a table with the bar in the cell. They used
    // to be a table and then, again, a separate bar chart underneath it.
    const weightRows = basis.map((b) => {
      const w = Number(b.weight);
      return `<tr>
        <td class="mono">${esc(b.detector)}</td>
        <td class="num"><span class="wt"><i class="${w < 0 ? 'neg' : ''}" style="--w:${(Math.abs(w) / maxW) * 50}%"></i></span>${w >= 0 ? '+' : ''}${w.toFixed(2)}</td>
        <td class="num">${b.rate_fraud_pct}%</td>
        <td class="num">${b.rate_legit_pct}%</td>
        <td class="wrap"><span class="tag">${esc(b.source)}</span> <span class="basis">${esc(b.basis)}</span></td></tr>`;
    }).join('');

    body.innerHTML = `
      ${backtestPanel(m.backtest)}

      <section class="sec">
        <div class="sec-head"><h2>How the probability is built</h2><span class="sec-meta">${esc(m.method || m.source || '')}</span></div>
        <p class="sec-note">The trigger sets where a case starts. Every detector that fires adds its measured log
          likelihood ratio; one that does not fire adds nothing. Each term is clipped at &plusmn;${clip} log-odds,
          so no single detector can carry a case alone.</p>

        <div class="sub"><h3>Where a case starts</h3>
          <dl class="ledger compact fit">${Object.entries(m.trigger_prior || {}).map(([k, v]) =>
            ledgerItem(esc(humanise(k)), `${v >= 0 ? '+' : ''}${Number(v).toFixed(2)}`, 'log-odds',
                       v > 0 ? 'fraud' : v < 0 ? 'legit' : '')).join('')}</dl>
          ${m.trigger_prior_note ? `<p class="sec-note sub-tight">${esc(m.trigger_prior_note)}</p>` : ''}
        </div>

        ${basis.length ? `<div class="sub"><h3>What each detector is worth</h3>
          <div class="table-wrap sheet"><table>
            <thead><tr><th scope="col">Detector</th><th scope="col" class="num">Weight</th>
              <th scope="col" class="num">Fires on fraud</th><th scope="col" class="num">Fires on legitimate</th>
              <th scope="col">How the weight was set</th></tr></thead>
            <tbody>${weightRows}</tbody></table></div></div>` : ''}

        ${met.overall ? `<div class="sub"><h3>The logistic fit, and why it is not used</h3>
          <p class="sec-note">Kept because its failure is the evidence for the design above: fitted to separate
            confirmed fraud from cleared alerts, graph features score near chance, because the cleared alerts
            are the most anomalous legitimate activity the bank could find.</p>
          <dl class="ledger compact">
            ${ledgerItem('ROC AUC, out of fold', Number(met.overall.roc_auc).toFixed(3))}
            ${ledgerItem('Accuracy', Number(met.overall.accuracy).toFixed(3))}
            ${ledgerItem('Recall on fraud', Number(met.overall.recall_fraud).toFixed(3))}
            ${ledgerItem('Specificity', Number(met.overall.specificity).toFixed(3))}
            ${ledgerItem('Brier score', Number(met.overall.brier).toFixed(3))}
            ${ledgerItem('Training rows', Number(met.overall.n).toLocaleString())}
          </dl>
          ${met.vs_cleared_hard_negatives ? `<p class="sec-note sub-tight">Against the hard negatives alone: AUC
            ${Number(met.vs_cleared_hard_negatives.roc_auc).toFixed(3)}, specificity
            ${Number(met.vs_cleared_hard_negatives.specificity).toFixed(3)}. Against ordinary unalerted activity: AUC
            ${Number((met.vs_unalerted_base_rate || {}).roc_auc || 0).toFixed(3)}.</p>` : ''}
        </div>` : '<div class="alert"><b>No fitted model on disk.</b> Documented fallback weights are in use.</div>'}

        ${notes.map(([k, v]) => `<p class="sec-note sub"><b>${esc(sentence(humanise(k)))}.</b> ${esc(v)}</p>`).join('')}
        ${m.notes && m.notes.design ? `<details><summary>Why the model is built this way</summary>
          <pre class="code wrap">${esc(m.notes.design)}</pre></details>` : ''}
        ${(met.detector_firing_rates || []).length ? `<details><summary>Detector firing rates in the fit</summary>
          <div class="table-wrap sub-tight"><table><thead><tr><th scope="col">Feature</th><th scope="col" class="num">Fires on fraud</th>
            <th scope="col" class="num">Fires on legitimate</th><th scope="col" class="num">n</th></tr></thead><tbody>
          ${met.detector_firing_rates.map((r) => `<tr><td class="mono">${esc(r.feature)}</td>
            <td class="num">${r.fires_on_fraud_pct}%</td><td class="num">${r.fires_on_legit_pct}%</td>
            <td class="num">${r.n_fires}</td></tr>`).join('')}</tbody></table></div></details>` : ''}
        ${(met.reliability || []).length ? `<details><summary>Calibration reliability</summary>
          <pre class="code">${esc(JSON.stringify(met.reliability, null, 2))}</pre></details>` : ''}
      </section>

      <div class="grid-2">
        <section class="sec">
          <div class="sec-head"><h2>Fraud policy v1.0</h2></div>
          <p class="sec-note">Enforced by code. The LLM cannot change a route.</p>
          <dl class="kv">${Object.entries(p.rules || {}).map(([k, v]) =>
            `<dt class="mono">${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>
          <div class="sub"><h3>Who may carry out each action</h3>
            <table><tbody>
              ${['auto', 'L1', 'L2'].map((r) => `<tr><td><span class="tag route ${r}">${r}</span></td>
                <td class="wrap mono">${esc(((p.routes || {})[r] || []).join(', '))}</td></tr>`).join('')}
            </tbody></table></div>
          ${p.evidence_request_simulation ? `<p class="sec-note sub"><b>Simulated evidence requests.</b>
            ${esc(p.evidence_request_simulation)}</p>` : ''}
        </section>

        <section class="sec">
          <div class="sec-head"><h2>Ingestion quality</h2>
            <span class="tag ${errs.length ? 'fraud' : warns.length ? 'uncertain' : 'legitimate'}">${errs.length} errors &middot; ${warns.length} warnings</span></div>
          <p class="sec-note">Checked when the graph was built.</p>
          ${Object.keys(counts).length ? `<dl class="ledger compact">${Object.entries(counts).slice(0, 8).map(([k, v]) =>
            ledgerItem(esc(sentence(humanise(k))), Number(v).toLocaleString())).join('')}</dl>` : ''}
          ${checks.map(([k, v]) => `<div class="sub"><h3>${esc(sentence(humanise(k)))}</h3>
            <dl class="kv">${Object.entries(v).map(([kk, vv]) => `<dt>${esc(humanise(kk))}</dt>
              <dd>${typeof vv === 'number' ? vv.toLocaleString() : esc(JSON.stringify(vv))}</dd>`).join('')}</dl></div>`).join('')}
          ${errs.concat(warns).map((x) => `<div class="alert">${esc(x)}</div>`).join('')}
          <details><summary>Full report</summary><pre class="code">${esc(JSON.stringify(q, null, 2))}</pre></details>
        </section>
      </div>`;
  } catch (e) {
    body.innerHTML = '';
    showError('#view-model', 'Could not load the model card', e.message, loadModel);
  }
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
  n.className = 'tag ' + (cls || '');   // running | fraud | legitimate | uncertain
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
