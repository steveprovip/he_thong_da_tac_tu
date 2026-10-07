'use strict';
/* GROKTOPUS Swarm Observatory — read-only telemetry renderer.
   No inline styles (CSP style-src 'self'); all colour lives in style.css and is
   toggled by class. Every colour-coded state is also written as text, so no
   information is carried by colour alone. */

const NAMES = ['NORO', 'LUMEN', 'TIDAL', 'ZEPHR', 'RUNE', 'OKAPI', 'MARIN', 'VESKA'];
const ROLES = ['Fair value & VWAP', 'Order flow & sentiment', 'Momentum & liquidity sweeps',
  'Liquidity & cost gate', 'Absolute risk veto', 'Exposure & Kelly sizing',
  'Exit & position protection', 'Authenticated execution'];
let EXECUTE_GATE = 0.768, VETO_GATE = 0.70, VPIN_TOXIC = 0.65;
const SVG_NS = 'http://www.w3.org/2000/svg';

const byId = id => document.getElementById(id);
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
const finite = v => v !== null && v !== undefined && Number.isFinite(Number(v));
const number = (v, d = 2) => finite(v) ? Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }) : '—';
const clamp01 = v => Math.max(0, Math.min(1, Number(v) || 0));
const set = (id, value) => { byId(id).textContent = value; };
const stanceOf = vote => vote ? (vote.veto ? 'VETO' : vote.side) : 'WAIT';
const stanceClass = stance => stance === 'VETO' ? 'veto' : stance === 'BUY' ? 'buy' : stance === 'SELL' ? 'sell' : 'hold';

/* ---------- 01 · the organism: eight living tentacles ---------- */
const arms = {};
NAMES.forEach((name, i) => {
  const angle = (i * 45 - 112.5) * Math.PI / 180;
  const x = 300 + 220 * Math.cos(angle), y = 210 + 155 * Math.sin(angle);
  const d = `M ${300 + 45 * Math.cos(angle)} ${205 + 40 * Math.sin(angle)}`
    + ` C ${300 + 115 * Math.cos(angle - .35)} ${210 + 100 * Math.sin(angle - .35)},`
    + `${x - 30 * Math.sin(angle)} ${y + 30 * Math.cos(angle)},${x} ${y}`;
  const g = document.createElementNS(SVG_NS, 'g');
  g.setAttribute('class', 'arm');
  const limb = document.createElementNS(SVG_NS, 'path');
  limb.setAttribute('d', d); limb.setAttribute('class', 'limb');
  // Normalised path length lets one CSS keyframe drive every tentacle's impulse.
  const pulse = document.createElementNS(SVG_NS, 'path');
  pulse.setAttribute('d', d); pulse.setAttribute('class', 'pulse'); pulse.setAttribute('pathLength', '100');
  const node = document.createElementNS(SVG_NS, 'circle');
  node.setAttribute('cx', x); node.setAttribute('cy', y); node.setAttribute('r', 5); node.setAttribute('class', 'node');
  const label = document.createElementNS(SVG_NS, 'text');
  label.setAttribute('x', x); label.setAttribute('y', y + (y < 210 ? -17 : 25));
  label.setAttribute('text-anchor', 'middle'); label.textContent = name;
  g.append(limb, pulse, node, label);
  byId('arms').append(g);
  arms[name] = g;

  const row = el('div', 'agent');
  const description = el('div');
  description.append(el('strong', null, name), el('small', null, ROLES[i]));
  const stance = el('span', 'stance', 'WAIT'), confidence = el('span', 'confidence', '—');
  row.append(el('span', 'index', String(i + 1).padStart(2, '0')), description, stance, confidence);
  byId('agent-list').append(row);
  arms[name].row = row;
});

/* ---------- 00 · multi-market ticker strip ---------- */
const tiles = new Map();
function tile(symbol) {
  let t = tiles.get(symbol);
  if (t) return t;
  const root = el('article', 'tile');
  const head = el('header');
  const base = symbol.endsWith('USDT') ? symbol.slice(0, -4) : symbol;
  head.append(el('b', null, base), el('span', 'pair', symbol.slice(base.length)), el('span', 'chip hold', 'WAIT'));
  const mid = el('div', 'mid', '—');
  const stats = el('dl');
  const cells = {};
  for (const [key, title] of [['spread', 'SPREAD bps'], ['obi', 'OBI'], ['vpin', 'VPIN']]) {
    const cell = el('div'); const dd = el('dd', null, '—');
    cell.append(el('dt', null, title), dd); stats.append(cell); cells[key] = dd;
  }
  const obiMeter = el('div', 'meter diverging'), obiFill = el('b');
  obiMeter.append(el('i', 'centre'), obiFill);
  const vpinMeter = el('div', 'meter'), vpinFill = el('b');
  vpinMeter.append(vpinFill, el('i', 'gate toxic'));
  const consensus = el('div', 'consensus-row');
  const bar = el('div', 'cbar'), barFill = el('b');
  bar.append(barFill, el('i', 'gate'));
  const cval = el('span', 'cval', '—');
  consensus.append(bar, cval);
  const state = el('footer', 'state', 'WAITING');
  root.append(head, mid, stats, obiMeter, vpinMeter, consensus, state);
  t = { root, chip: head.lastChild, mid, cells, obiFill, vpinFill, barFill, cval, state };
  tiles.set(symbol, t);
  return t;
}

function renderTicker(markets) {
  const strip = byId('ticker');
  if (!markets.length) return;
  const order = [];
  for (const m of markets) {
    const t = tile(m.symbol);
    const stance = m.status === 'AUTHORIZED' ? m.side : (m.score ?? 0) >= VETO_GATE ? m.side : 'HOLD';
    t.chip.textContent = ['WAITING', 'WARMUP', 'STALE'].includes(m.status) ? 'WAIT' : stance;
    t.chip.className = 'chip ' + stanceClass(t.chip.textContent);
    t.mid.textContent = number(m.mid, m.mid >= 100 ? 2 : 5);
    t.cells.spread.textContent = number(m.spread, 2);
    t.cells.obi.textContent = number(m.obi, 3);
    // A saturated bucket means VPIN measured nothing; say so rather than showing a number.
    const saturated = finite(m.saturation) && m.saturation > 0.5;
    t.cells.vpin.textContent = finite(m.vpin) ? number(m.vpin, 3) + (saturated ? ' SAT' : '') : 'WARMUP';
    t.cells.vpin.className = saturated ? 'saturated' : '';
    const obi = Math.max(-1, Math.min(1, Number(m.obi) || 0));
    t.obiFill.style.left = (obi >= 0 ? 50 : 50 + obi * 50) + '%';
    t.obiFill.style.width = Math.abs(obi) * 50 + '%';
    t.obiFill.className = obi >= 0 ? 'bid' : 'ask';
    const vpin = clamp01(m.vpin);
    t.vpinFill.style.width = (finite(m.vpin) ? vpin * 100 : 0) + '%';
    t.vpinFill.className = finite(m.vpin) && vpin > VPIN_TOXIC ? 'toxic' : '';
    const score = clamp01(m.score);
    t.barFill.style.width = score * 100 + '%';
    t.barFill.className = score >= EXECUTE_GATE ? 'pass' : score >= VETO_GATE ? 'near' : 'blocked';
    t.cval.textContent = number(score * 100, 1) + '%';
    const blockers = (m.blockers ?? []).join(', ');
    t.state.textContent = blockers ? `${m.status} · ${blockers}` : (m.status ?? '—');
    t.state.title = m.why ?? '';
    t.root.classList.toggle('authorized', ['AUTHORIZED', 'IN_POSITION', 'ENTRY_PENDING', 'SIGNAL'].includes(m.status));
    order.push(t.root);
  }
  strip.replaceChildren(...order);
}

/* ---------- 03 · equity trace ---------- */
let trace = [];
const START_EQUITY = 3.85;   // ~100k VND
const KILL_EQUITY = 3.80;    // start less the 5-cent hard stop

function renderEquity(history) {
  const points = history.filter(p => finite(p.value));
  const line = byId('equity-line'), area = byId('equity-area');
  if (!points.length) { line.setAttribute('d', ''); area.setAttribute('d', ''); return; }
  trace = points;
  const values = points.map(p => p.value);
  // The micro-capital run only has two levels that matter: where it started and
  // where the kill switch fires. Keep both inside the domain so the line is
  // always read against them, not against an autoscaled window.
  values.push(START_EQUITY, KILL_EQUITY);
  const min = Math.min(...values), max = Math.max(...values), range = max - min || 1;
  const X = i => points.length < 2 ? 0 : i / (points.length - 1) * 900;
  const Y = v => max === min ? 70 : 125 - (v - min) / range * 110;
  // Straight segments at 600 points read as a smooth curve and stay jank-free;
  // a spline is only worth its cost while the trace is sparse.
  // Start / kill guides and the header label hold for EVERY path shape. They
  // used to be set only on the straight-line branch, so between 3 and 120
  // points the guides sat at y=0 and the label was overwritten by min—max.
  const startLine = byId('equity-start'), killLine = byId('equity-kill');
  if (startLine) { const y = Y(START_EQUITY).toFixed(2); startLine.setAttribute('y1', y); startLine.setAttribute('y2', y); }
  if (killLine) { const y = Y(KILL_EQUITY).toFixed(2); killLine.setAttribute('y1', y); killLine.setAttribute('y2', y); }
  const last = values[values.length - 3];
  const range3 = byId('equity-range');
  if (range3 && finite(last)) {
    const delta = last - START_EQUITY;
    range3.textContent = `${number(last, 4)} U (${delta >= 0 ? '+' : ''}${number(delta, 4)}) · VÀO ${START_EQUITY.toFixed(2)} · DỪNG ${KILL_EQUITY.toFixed(2)}`;
    range3.className = last >= START_EQUITY ? 'pos' : last <= KILL_EQUITY ? 'neg' : 'zero';
  }
  let d;
  if (points.length > 120 || points.length < 3) {
    d = points.map((p, i) => `${i ? 'L' : 'M'} ${X(i).toFixed(2)} ${Y(p.value).toFixed(2)}`).join(' ');
  } else {
    d = `M ${X(0).toFixed(2)} ${Y(values[0]).toFixed(2)}`;
    for (let i = 0; i < points.length - 1; i++) {
      const x0 = X(Math.max(0, i - 1)), y0 = Y(values[Math.max(0, i - 1)]);
      const x1 = X(i), y1 = Y(values[i]), x2 = X(i + 1), y2 = Y(values[i + 1]);
      const x3 = X(Math.min(points.length - 1, i + 2)), y3 = Y(values[Math.min(points.length - 1, i + 2)]);
      d += ` C ${(x1 + (x2 - x0) / 6).toFixed(2)} ${(y1 + (y2 - y0) / 6).toFixed(2)},`
        + `${(x2 - (x3 - x1) / 6).toFixed(2)} ${(y2 - (y3 - y1) / 6).toFixed(2)},${x2.toFixed(2)} ${y2.toFixed(2)}`;
    }
  }
  line.setAttribute('d', d);
  area.setAttribute('d', d + ` L ${X(points.length - 1).toFixed(2)} 140 L 0 140 Z`);
}

const wrap = document.querySelector('.chart-wrap');
const cursor = byId('equity-cursor'), tip = byId('equity-tip');
wrap.addEventListener('pointermove', event => {
  if (!trace.length) return;
  const box = wrap.getBoundingClientRect();
  const ratio = Math.max(0, Math.min(1, (event.clientX - box.left) / box.width));
  const point = trace[Math.round(ratio * (trace.length - 1))];
  cursor.setAttribute('x1', ratio * 900); cursor.setAttribute('x2', ratio * 900);
  cursor.classList.add('visible');
  tip.hidden = false;
  tip.textContent = `${number(point.value)} USDT · ${new Date(point.t * 1000).toLocaleTimeString()}`;
  tip.style.left = Math.min(Math.max(ratio * box.width, 60), box.width - 60) + 'px';
});
wrap.addEventListener('pointerleave', () => { cursor.classList.remove('visible'); tip.hidden = true; });

/* ---------- render ---------- */
function render(s) {
  if (finite(s.threshold)) EXECUTE_GATE = Number(s.threshold);
  if (finite(s.veto_threshold)) VETO_GATE = Number(s.veto_threshold);
  if (finite(s.toxic_vpin)) VPIN_TOXIC = Number(s.toxic_vpin);

  const gateNote = document.querySelector('.gate-note');
  if (gateNote) {
    gateNote.innerHTML = `EXECUTE &ge; ${(EXECUTE_GATE * 100).toFixed(1)}%<br>RUNE VETO &lt; ${(VETO_GATE * 100).toFixed(1)}%`;
  }
  const thExecute = document.querySelector('.threshold:not(.veto-line)');
  if (thExecute) thExecute.style.left = `${EXECUTE_GATE * 100}%`;
  const thVeto = document.querySelector('.threshold.veto-line');
  if (thVeto) thVeto.style.left = `${VETO_GATE * 100}%`;

  set('mode', s.mode ?? 'OFFLINE');
  set('armed', s.armed ? 'EXECUTION ENABLED' : 'EXECUTION LOCKED');
  set('status', s.status ?? 'Waiting');
  for (const field of ['equity', 'wallet', 'pnl', 'volume', 'fees']) set(field, number(s[field]));
  byId('pnl').className = Number(s.pnl) >= 0 ? 'positive' : 'negative';
  set('drawdown', s.drawdown == null ? '—' : number(s.drawdown * 100) + '%');
  set('kelly', (s.kelly == null ? 'WARMUP' : number(s.kelly * 100) + '%') + ` · n=${s.outcome_samples ?? 0}`);

  const score = clamp01(s.score);
  byId('score').replaceChildren(document.createTextNode(number(score * 100, 1)), el('span', null, '%'));
  byId('scorebar').style.width = score * 100 + '%';
  byId('scorebar').className = score >= EXECUTE_GATE ? 'pass' : score >= VETO_GATE ? 'near' : 'blocked';

  const votes = s.agents ?? [];
  for (const name of NAMES) {
    const vote = votes.find(v => v.agent === name), arm = arms[name], row = arm.row;
    const stance = stanceOf(vote);
    const klass = stanceClass(stance);
    arm.setAttribute('class', 'arm ' + klass + (vote ? '' : ' idle'));
    row.querySelector('.stance').textContent = stance;
    row.querySelector('.stance').className = 'stance ' + klass;
    row.querySelector('.confidence').textContent = vote ? number(vote.confidence * 100, 0) + '%' : '—';
    row.title = vote?.reason ?? 'Awaiting data';
  }

  // VESKA's core lights only while a freshly issued AuthToken is inside its 5s TTL.
  const authorized = finite(s.auth_at) && (Date.now() / 1000 - s.auth_at) < 6;
  byId('octopus').classList.toggle('authorized', authorized);
  set('core-state', authorized ? 'AUTHTOKEN LIVE · VESKA EXECUTING' : s.armed ? 'ARMED · AWAITING QUORUM' : 'STANDBY');
  byId('core-state').className = authorized ? 'auth-live' : '';
  set('auth-tag', 'AUTHTOKEN ×' + (s.authorized ?? 0));

  // The lab counts signals the gates refused, so a flat session is still measurable.
  const lab = s.lab;
  if (s.scanner) {
    const sc = s.scanner;
    set('lab-tag', `SCAN intents ${sc.intents} · GTX fills ${sc.fills}`
      + (finite(sc.fill_rate) ? ` (${number(sc.fill_rate * 100, 0)}%)` : '')
      + ` · cancels ${sc.cancels}` + (finite(sc.trades_per_hour) ? ` · ${number(sc.trades_per_hour, 1)} lệnh/giờ` : ''));
    byId('lab-tag').className = 'tag' + (sc.fills ? ' buy' : '');
  } else if (!lab || !lab.probes) {
    set('lab-tag', lab ? `LAB ${lab.open ?? 0} OPEN · 0 RESOLVED` : 'LAB OFF');
    byId('lab-tag').className = 'tag';
  } else {
    set('lab-tag', `LAB n=${lab.probes} · TP ${number(lab.tp * 100, 0)}% · `
      + `${lab.net_bps >= 0 ? '+' : ''}${number(lab.net_bps, 2)} bps`
      + ' · ASSUMED FILLS');
    byId('lab-tag').className = 'tag ' + (lab.probes < 200 ? 'provisional' : lab.net_bps > 0 ? 'buy' : 'sell');
  }

  set('data-source', `FEED ${String(s.market_data ?? 'unknown').toUpperCase()} · ${s.policy ?? '—'} · EDGE PROXY UNCALIBRATED`);
  const markets = s.markets ?? [];
  renderTicker(markets);
  const tbody = byId('markets');
  tbody.replaceChildren();
  /* Hard blockers (cost/risk — hành động được) to đỏ; informational blockers
     (warmup/low-*) chỉ xám nhạt: bảng không còn là "bức tường đỏ". */
  const HARD_BLOCKERS = new Set(['STALE_DATA', 'TIGHT_SPREAD', 'WIDE_SPREAD',
    'RISK_OR_FEED_GATE', 'VPIN_TOXIC', 'HALTED']);
  for (const m of markets) {
    const tr = document.createElement('tr');
    const saturated = finite(m.saturation) && m.saturation > 0.5;
    const blockers = m.reasons ?? m.blockers ?? [];
    const netEdge = finite(m.edge_bps) && finite(m.cost_bps) ? m.edge_bps - m.cost_bps : null;
    const cells = [m.symbol, number(m.mid), number(m.spread), number(m.obi, 3),
      finite(m.vpin) ? number(m.vpin, 3) + (saturated ? ' SAT' : '') : 'WARMUP',
      number(clamp01(m.score) * 100, 1) + '%', number(clamp01(m.directional) * 100, 1) + '%',
      netEdge === null ? '—' : (netEdge >= 0 ? '+' : '') + number(netEdge, 1), m.status];
    cells.forEach((value, i) => {
      let td;
      if (i === 7) {
        td = el('td', 'edge ' + (netEdge === null ? 'unknown' : netEdge >= 0 ? 'pos' : 'neg'), value);
        td.title = `${m.why ?? ''} | Hòa vốn sau phí: edge ${number(m.edge_bps)} − cost ${number(m.cost_bps)} bps | ATR ${number(m.atr_bps)}bps | VPIN buckets ${m.vpin_buckets ?? 0}`;
      } else if (i === 8) {
        td = el('td', 'blockers');
        if (blockers.length) {
          for (const b of blockers.slice(0, 2))
            td.append(el('span', 'blk' + (HARD_BLOCKERS.has(b) ? ' hard' : ''), b));
          if (blockers.length > 2) td.append(el('span', 'blk more', '+' + (blockers.length - 2)));
          td.title = blockers.join(' · ');
        } else td.textContent = '—';
      } else {
        td = el('td', i === 4 && saturated ? 'saturated' : null, value);
      }
      tr.append(td);
    });
    tbody.append(tr);
  }
  if (!markets.length) { const tr = document.createElement('tr'); const td = el('td', null, 'Waiting for live order books…'); td.colSpan = 10; tr.append(td); tbody.append(tr); }

  renderEquity(s.history ?? []);

  if (s.branch_labels) Object.assign(BRANCH_LABEL, s.branch_labels);
  renderBranches(s.branches);

  const positions = byId('positions');
  positions.replaceChildren();
  if (!(s.positions ?? []).length) positions.textContent = 'No active positions';
  for (const p of s.positions ?? []) {
    const dp = finite(p.entry) && p.entry < 1 ? 7 : 5;
    const text = p.pending
      ? `${p.symbol} / ${p.side} · GTX CHỜ KHỚP ${number(p.size, 2)} @ ${number(p.entry, dp)} · ${number(p.age_s, 1)}/${number(p.ttl_s, 0)}s · queue ${number(p.queue_ahead, 0)}`
      : `${p.branch ? p.branch.toUpperCase() + ' · ' : ''}${p.symbol} / ${p.side || 'BUY'} · ${number(p.filled || p.size, 2)} · entry ${number(p.entry, dp)}`
        + ` · pnl ${number(p.pnl_bps, 1)} bps` + (finite(p.mfe_bps) ? ` · MFE ${number(p.mfe_bps, 1)}` : '')
        + (finite(p.tp) ? ` · TP ${number(p.tp, dp)} / SL ${number(p.sl, dp)}${p.protected ? ' (BE)' : ''}` : '')
        + (finite(p.age_s) ? ` · ${number(p.age_s, 0)}/${number(p.max_hold_s, 0)}s` : '');
    const row = el('div', 'position' + (p.pending ? ' pending' : Number(p.pnl_bps) >= 0 ? ' up' : ' down'), text);
    positions.append(row);
  }
  for (const o of s.open_orders ?? []) {
    const dp = finite(o.price) && o.price < 1 ? 7 : 5;
    positions.append(el('div', 'position ' + (o.status === 'FILLED' ? 'up' : 'pending'),
      `ORDER · ${o.symbol} · ${o.kind} @ ${number(o.price, dp)}` + (finite(o.qty) ? ` × ${number(o.qty, 2)}` : ' · closePosition')
      + (o.status ? ` · ${o.status}` : ' · WORKING')));
  }
  renderTrades(s.trades);

  const events = byId('events');
  events.replaceChildren();
  for (const e of s.events ?? []) {
    const li = document.createElement('li'), time = document.createElement('time');
    time.textContent = new Date(e.t * 1000).toLocaleTimeString();
    li.append(time, document.createTextNode(e.message));
    events.append(li);
  }
}

/* ---------- 00 · shadow branch benchmark ----------
   Three branches trade the SAME entries; the only difference is the stop rule.
   So the comparison is only honest if the winner is read off the numbers each
   tick — pinning "profitable" to one branch name makes the board lie the moment
   another branch takes the lead. */
const EV_SCALE = 40;      // bps half-domain: TP net +35.67, SL net -29.44
const PNL_SCALE = 0.05;   // USDT half-domain: the kill switch fires at 5 cents
const CHOP_RATE = 0.40;   // premature stopout share that marks a branch as trapped

const BRANCH_LABEL = {
  baseline: 'BASELINE · FIXED −20.44',
  be_15_10: 'BE_15_10 · TRIGGER +15 → +10',
  be_20_10: 'BE_20_10 · TRIGGER +20 → +10',
};

function diverging(cls, value, scale) {
  /* Zero sits at the centre; the bar grows left for losses, right for gains. */
  const row = el('div', 'bar diverging ' + cls);
  const fill = el('b');
  const pct = Math.min(1, Math.abs(Number(value) || 0) / scale) * 50;
  fill.style.width = pct.toFixed(1) + '%';
  if ((Number(value) || 0) < 0) { fill.style.right = '50%'; fill.classList.add('neg'); }
  else { fill.style.left = '50%'; fill.classList.add('pos'); }
  row.append(el('i', 'axis'), fill);
  return row;
}

function stacked(tp, protect, sl) {
  const total = tp + protect + sl;
  const row = el('div', 'bar stacked');
  if (!total) { row.append(el('b', 'empty')); return row; }
  [['tp', tp], ['protect', protect], ['sl', sl]].forEach(([cls, count]) => {
    if (!count) return;
    const seg = el('b', cls);
    seg.style.width = (count / total * 100).toFixed(1) + '%';
    seg.title = `${cls.toUpperCase()} ${count}`;
    row.append(seg);
  });
  return row;
}

function rateBar(rate) {
  const row = el('div', 'bar rate');
  const fill = el('b', rate >= CHOP_RATE ? 'danger' : rate > 0 ? 'warn' : 'ok');
  fill.style.width = (Math.min(1, rate) * 100).toFixed(1) + '%';
  const gate = el('i', 'gate');
  gate.style.left = (CHOP_RATE * 100) + '%';
  row.append(fill, gate);
  return row;
}

function metric(label, valueText, valueCls, bar, note) {
  const item = el('div', 'bm-item');
  const head = el('div', 'bm-head');
  head.append(el('dt', null, label), el('dd', valueCls, valueText));
  item.append(head, bar);
  if (note) item.append(el('small', 'bm-note', note));
  return item;
}

function renderBranches(branches) {
  const container = byId('branch-cards');
  if (!container || !branches) return;
  const keys = ['baseline', 'be_20_10', 'be_15_10'].filter(k => branches[k]);
  if (!keys.length) return;
  container.replaceChildren();

  // Lead is whichever branch actually has the best positive EV right now.
  let leadKey = null, best = 0;
  for (const k of keys) {
    const ev = Number(branches[k].analytical_ev_bps) || 0;
    if (ev > best) { best = ev; leadKey = k; }
  }

  for (const key of keys) {
    const b = branches[key];
    const ev = Number(b.analytical_ev_bps) || 0;
    const pnl = Number(b.cum_pnl_usdt) || 0;
    const prem = Number(b.premature_rate) || 0;
    const tp = b.tp_count || 0, pro = b.protect_count || 0, sl = b.sl_count || 0;
    const trades = b.total_trades || 0;
    const isLead = key === leadKey;
    const isChop = prem >= CHOP_RATE && pro > 0;

    const card = el('div', 'branch-card' + (isLead ? ' lead' : isChop ? ' chop' : ''));
    const head = el('div', 'branch-head');
    head.append(el('span', 'branch-name', BRANCH_LABEL[key] || key.toUpperCase()));
    head.append(el('span',
      'branch-badge ' + (isLead ? 'lead' : isChop ? 'chop' : 'warn'),
      isLead ? '★ DẪN ĐẦU' : isChop ? '⚠ QUÉT NON' : trades ? 'ĐANG ĐO' : 'CHỜ MẪU'));
    card.append(head);

    const dl = el('dl', 'branch-metrics');
    dl.append(metric('NET EV',
      `${ev > 0 ? '+' : ''}${number(ev, 2)} bps`,
      ev > 0 ? 'pos' : ev < 0 ? 'neg' : 'zero',
      diverging('ev', ev, EV_SCALE),
      `L95 ${number(b.lower95_ev_bps, 1)} · n=${trades}`
      + (finite(b.p_ev_positive) ? ` · P(EV>0) ${number(b.p_ev_positive * 100, 0)}%` : '')));

    dl.append(metric('PNL LŨY KẾ',
      `${pnl > 0 ? '+' : ''}${number(pnl, 4)} U`,
      pnl > 0 ? 'pos' : pnl < 0 ? 'neg' : 'zero',
      diverging('pnl', pnl, PNL_SCALE),
      `thang đo ±${PNL_SCALE.toFixed(2)} U = ngưỡng kill`));

    dl.append(metric('TP / PROTECT / SL',
      `${tp} / ${pro} / ${sl}` + (finite(b.time_count) ? ` / T${b.time_count}` : ''), 'zero',
      stacked(tp, pro, sl),
      trades ? `${number(tp / trades * 100, 0)}% chạm TP` : 'chưa có lệnh'));

    dl.append(metric('QUÉT NON',
      `${b.premature_count || 0} (${number(prem * 100, 0)}%)`,
      prem >= CHOP_RATE ? 'neg' : prem > 0 ? 'zero' : 'pos',
      rateBar(prem),
      `vạch cảnh báo ${CHOP_RATE * 100}%`));

    card.append(dl);
    container.append(card);
  }

  const verdict = byId('branch-verdict');
  if (verdict) {
    verdict.textContent = leadKey
      ? `${(BRANCH_LABEL[leadKey] || leadKey).split(' ·')[0]} DẪN ĐẦU · EV ${number(best, 2)} bps`
      : 'CHƯA NHÁNH NÀO CÓ EV DƯƠNG';
    verdict.className = leadKey ? 'verdict lead' : 'verdict';
  }
}

/* A new bid from Binance is the one signal that the board is alive rather than
   merely connected, so it gets its own beat instead of hiding inside a number. */
let lastMid = null;
function beat(markets) {
  const dot = byId('tick-dot');
  if (!dot) return;
  const mid = Array.isArray(markets) && markets.length ? Number(markets[0].mid) : null;
  if (!finite(mid)) return;
  if (lastMid !== null && mid !== lastMid) {
    dot.classList.remove('beat');
    void dot.offsetWidth;                    // restart the animation
    dot.classList.add('beat', mid > lastMid ? 'up' : 'down');
    dot.classList.remove(mid > lastMid ? 'down' : 'up');
  }
  lastMid = mid;
  const px = byId('tick-price');
  if (px) {
    const m0 = markets[0];
    const cvd = Number(m0.cvd_60s);
    px.textContent = mid.toFixed(5)
      + (finite(m0.obi) ? ` · OBI ${m0.obi >= 0 ? '+' : ''}${Number(m0.obi).toFixed(2)}` : '')
      + (finite(m0.cvd_60s) ? ` · CVD60 ${cvd >= 0 ? '+' : ''}${Math.abs(cvd) >= 1000 ? (cvd / 1000).toFixed(1) + 'k' : cvd.toFixed(0)}` : '');
  }
}

/* ---------- 08 · closed trades (all branches, newest first) ---------- */
function renderTrades(trades) {
  const box = byId('trades');
  if (!box) return;
  box.replaceChildren();
  if (!(trades ?? []).length) { box.textContent = 'Chưa có lệnh đóng'; return; }
  for (const t of trades.slice(0, 30)) {
    const dp = t.entry_price < 1 ? 7 : 4;
    const row = el('div', 'position ' + (t.net_bps >= 0 ? 'up' : 'down'),
      `${new Date(t.exit_time * 1000).toLocaleTimeString()} · ${String(t.branch).toUpperCase()} · ${t.symbol} ${t.side}`
      + ` · ${number(t.entry_price, dp)} → ${number(t.exit_price, dp)} · ${t.reason}`
      + ` · ${t.net_bps >= 0 ? '+' : ''}${number(t.net_bps, 1)} bps · ${t.pnl_usdt >= 0 ? '+' : ''}${number(t.pnl_usdt, 4)} U · ${number(t.hold_s, 0)}s`);
    box.append(row);
  }
}

/* ---------- R1/R2 · alpha + event radar (research.radar_runner -> /api/radar) ---------- */
const tpLabel = tp => tp === 'ladder' ? 'LADDER TP1→BE→TP2' : `TP+${tp}%`;
const pct = (v, d = 2) => finite(v) ? `${v >= 0 ? '+' : ''}${number(v, d)}%` : '—';
function listInto(id, items, fmt, empty) {
  const box = byId(id);
  if (!box) return;
  box.replaceChildren();
  if (!items || !items.length) { box.textContent = empty; return; }
  for (const it of items) { const [text, cls] = fmt(it); box.append(el('div', 'position ' + (cls || ''), text)); }
}
function renderRadar(o) {
  if (!o || o.status === 'no radar yet') return;
  const age = finite(o.updated_at) ? Math.round(Date.now() / 1000 - o.updated_at) : null;
  const r = o.radar;
  set('radar-meta', r ? `QUÉT ${r.scanned}/${r.universe} CẶP · ${r.fired} TÍN HIỆU · ${age ?? '—'}s TRƯỚC`
                      : `BINANCE LỖI · ${(o.errors || []).join(' | ') || '—'}`);
  const tbody = byId('radar-picks');
  tbody.replaceChildren();
  for (const p of (r && r.picks) || []) {
    const pl = p.plan || {}, t1 = (pl.tps || [])[0] || {}, t2 = (pl.tps || [])[1] || {};
    const warn = [...(pl.warnings || []), ...(p.chase ? ['CHASE'] : [])];
    const cells = [p.symbol, p.side, `${number(p.surge, 1)}x / ${p.window_min}m`, pct(p.ret_pct), number(p.rsi, 0),
      number(p.taker, 2), finite(p.obi) ? number(p.obi, 2) : '—',
      p.retest ? `RETEST ${number(p.retest.price, 6)} (−${number(p.retest.pullback_pct, 2)}% ${p.retest.anchor}) · SL ${number((p.retest_plan || pl).sl, 6)}`
               + (p.trend ? ` · ${Object.entries(p.trend).map(([k, v]) => k + ' ' + v).join(' ')}` : '')
               : `${number(pl.entry, 6)} · ${number(pl.sl, 6)} (−${number(pl.sl_pct, 2)}%)`,
      `${number(t1.pnl_usdt, 3)} / ${number(t2.pnl_usdt, 3)} U · ${number(t1.pnl_vnd, 0)} / ${number(t2.pnl_vnd, 0)} đ`,
      warn.length ? warn.join(' · ') : '—'];
    const tr = document.createElement('tr');
    cells.forEach((v, i) => tr.append(el('td', i === 1 ? (p.side === 'LONG' ? 'edge pos' : 'edge neg') : null, v)));
    tbody.append(tr);
  }
  if (!tbody.children.length) {
    const tr = document.createElement('tr');
    const td = el('td', null, r ? 'Không có cặp nào đạt surge ≥3x + breakout + RSI. Đứng ngoài cũng là một vị thế.' : 'Không lấy được dữ liệu Binance');
    td.colSpan = 10; tr.append(td); tbody.append(tr);
  }
  const bt = o.backtest && o.backtest.by_tp;
  set('radar-base', bt ? 'BASE RATE (cùng tín hiệu, vào nến sau): ' + Object.entries(bt).map(([tp, s]) => s.n
    ? `${tpLabel(tp)} n=${s.n} chạm TP1 ${number(s.tp_rate * 100, 0)}% · SL ${number(s.sl_rate * 100, 0)}% · TB ${pct(s.mean_net_pct)}`
      + (finite(s.lower95_pct) ? ` · L95 ${pct(s.lower95_pct)}` : '')
      + (o.backtest.split && o.backtest.split.oos && o.backtest.split.oos.n ? ` · OOS ${pct(o.backtest.split.oos.mean_net_pct)} (n=${o.backtest.split.oos.n})` : '')
    : `${tpLabel(tp)} chưa có mẫu`).join('  |  ')
    + (o.backtest.by_tp_retest ? '  ‖  RETEST: ' + Object.entries(o.backtest.by_tp_retest).map(([tp, s]) => s.n
      ? `${tpLabel(tp)} khớp ${number(s.fill_rate * 100, 0)}% n=${s.n} chạm ${number(s.tp_rate * 100, 0)}% · SL ${number(s.sl_rate * 100, 0)}% · TB ${pct(s.mean_net_pct)}`
      : `${tpLabel(tp)} chưa khớp mẫu nào`).join('  |  ') : '') : 'BASE RATE: chưa chạy');
  set('radar-watch', 'WATCH: ' + (((r && r.watchlist) || []).slice(0, 5).map(w => `${w.symbol} ${number(w.surge, 1)}x`).join(' · ') || '—'));
  const e = o.events;
  set('event-meta', e ? `${e.events} SỰ KIỆN · ${e.markets} THỊ TRƯỜNG · ARB ${e.arbs.length} · EDGE ${e.edges.length}` : 'POLYMARKET LỖI / TẮT');
  listInto('event-alerts', e && e.alerts, a => a.kind === 'ARB'
    ? [`🔒 ARB ${a.type} · ${a.event} · +${number(a.edge * 100, 1)}¢/bộ · cần $${number(a.min_capital, 2)}${a.fits_account ? ' · VỪA VỐN' : ' · VƯỢT VỐN'}`, a.fits_account ? 'up' : 'pending']
    : [`⚡ EDGE ${number(a.edge * 100, 0)}pp · ${a.question} → ${a.side} @ ${number(a.price, 2)} · p_model ${number(a.p_model, 2)} · vol ${number(a.sigma * 100, 0)}% vs ngụ ý ${finite(a.implied_vol) ? number(a.implied_vol * 100, 0) + '%' : '—'} (model chưa hiệu chuẩn)`, 'pending'],
    'Không có kèo arb hay edge ≥ 15pp lúc này');
  listInto('event-arbs', e && e.arbs, a => [`${a.fits_account ? 'LOCK-IN' : 'VƯỢT VỐN'} · ${a.type} · ${a.event} · ${a.legs} chân · +${number(a.edge * 100, 1)}¢/bộ`
    + (finite(a.min_capital) ? ` · cần ≥ $${number(a.min_capital, 2)} (${a.min_shares} cổ/chân)` : '')
    + (finite(a.days_to_resolution) ? ` · khoá ${number(a.days_to_resolution, 0)} ngày` : '')
    + (a.warnings.length ? ' · ⚠ ' + a.warnings[0] : ''), a.fits_account ? 'up' : 'pending'], 'Không có arb sau phí');
  listInto('event-edges', e && e.edges, x => [`${x.question} → ${x.side} @ ${number(x.price, 2)} · p ${number(x.p_model, 2)} · edge ${number(x.edge * 100, 1)}pp · vol ${number(x.sigma * 100, 0)}% vs ngụ ý ${finite(x.implied_vol) ? number(x.implied_vol * 100, 0) + '%' : '—'} · ${x.shares} cổ ≈ $${number(x.stake, 2)}`, 'pending'], 'Không có kèo edge ≥ 5pp sau phí');
  listInto('event-macro', e && e.macro, m => [`${m.question} · YES ${finite(m.yes) ? number(m.yes * 100, 0) + '%' : '—'}` + (finite(m.change_1d) ? ` (${m.change_1d >= 0 ? '+' : ''}${number(m.change_1d * 100, 1)} 24h)` : '') + ` · $${number(m.volume24h, 0)}/24h`, ''], '—');
}
async function pollRadar() {
  try {
    const res = await fetch('/api/radar', { cache: 'no-store', signal: AbortSignal.timeout(3500) });
    if (res.ok) renderRadar(await res.json());
  } catch { /* radar is optional; the trading board keeps working */ }
  setTimeout(pollRadar, 10000);
}
pollRadar();

/* ---------- F1/F2 · fast lane (research.fast_runner -> /api/fast) ---------- */
const usd = v => finite(v) ? '$' + Number(v).toLocaleString('en-US', { maximumFractionDigits: 0 }) : '—';
const hhmmss = t => finite(t) ? new Date(t * 1000).toLocaleTimeString() : '—';
function renderFast(o) {
  if (!o || o.status === 'no fast lane yet') return;
  const sn = o.sniper || {}, st = sn.stats || {}, sm = sn.summary || {};
  const age = finite(o.updated_at) ? Math.round(Date.now() / 1000 - o.updated_at) : null;
  set('fast-meta', `${o.ws && o.ws.connected ? 'WS LIVE' : 'WS DOWN ' + ((o.ws && o.ws.error) || '')} · ${st.liquidations ?? 0} lệnh thanh lý (${usd(st.liq_usd)}) · ${st.cascades ?? 0} cascade · ${sm.n ?? 0} lệnh shadow · PnL ${number(sm.pnl_usdt, 4)} U · ${age ?? '—'}s trước`);
  listInto('fast-feed', sn.feed, e => [`${hhmmss(e.recv)} · ${e.symbol} · ${e.liquidated} cháy ${usd(e.notional)} @ ${number(e.price, e.price < 1 ? 6 : 2)}`, e.liquidated === 'LONG' ? 'down' : 'up'], 'Chưa có lệnh thanh lý ≥ $10k');
  listInto('fast-cascades', sn.cascades, c => [`${hhmmss(c.t)} · ${c.symbol} · ${c.liquidated} ${usd(c.usd_window)} ×${c.count} · wick ${finite(c.move_pct) ? number(c.move_pct, 2) + '%' : '—'} → ${c.action}`, (c.action === 'BUY' || c.action === 'SELL') ? 'up' : 'pending'], 'Chưa có cascade vượt ngưỡng');
  const rows = [];
  if (sn.position) { const p = sn.position; rows.push({ position: p }); }
  for (const t of sn.trades || []) rows.push({ trade: t });
  listInto('fast-trades', rows, r => r.position
    ? [`ĐANG GIỮ ${r.position.symbol} ${r.position.side} @ ${number(r.position.entry, 6)} · TP ${number(r.position.tp, 6)} · SL ${number(r.position.sl, 6)} (−${number(r.position.sl_pct, 2)}%) · ${number(r.position.age_s, 0)}s · uPnL ${number(r.position.upnl, 4)} U`, 'pending']
    : [`${hhmmss(r.trade.closed)} · ${r.trade.symbol} ${r.trade.side} ${r.trade.reason} ${pct(r.trade.net_pct)} · ${number(r.trade.pnl, 4)} U · ${number(r.trade.hold_s, 0)}s`, r.trade.pnl >= 0 ? 'up' : 'down'],
    'Chưa có lệnh shadow');
  const pmo = o.pm15 || {}, sc = pmo.score || {};
  set('pm15-meta', (sc.n ? `ĐÃ CHẤM ${sc.n} kèo: trúng ${number(sc.hit_rate * 100, 0)}% · ROI TB ${pct(sc.mean_roi * 100)} · edge tự nhận TB ${number(sc.mean_edge_claimed * 100, 1)}pp` : `CHƯA CÓ KÈO ĐÃ CHỐT (${sc.open ?? 0} đang chờ)`) + ((pmo.errors || []).length ? ' · ⚠ ' + pmo.errors[0] : ''));
  const tb = byId('pm15-rows');
  if (tb) {
    tb.replaceChildren();
    for (const r of pmo.rows || []) {
      const tr = document.createElement('tr');
      [r.slug, `${number(r.tau_s, 0)}s`, pct(r.move_pct, 3), number(r.p_up, 3), number(r.up_ask, 2), number(r.down_ask, 2),
        r.side || '—', finite(r.edge) ? `${r.edge >= 0 ? '+' : ''}${number(r.edge * 100, 1)}pp` : '—',
        r.flag ? 'KÈO (sau phí)' : 'không'].forEach((v, i) => tr.append(el('td', i === 8 && r.flag ? 'edge pos' : null, v)));
      tb.append(tr);
    }
    if (!tb.children.length) { const tr = document.createElement('tr'); const td = el('td', null, 'Chưa có dữ liệu Polymarket 15m'); td.colSpan = 9; tr.append(td); tb.append(tr); }
  }
}
const streamFresh = () => Date.now() - (window.__grokLastAt || 0) < 3000;   // terminal.js SSE owns the feed
document.addEventListener('grok:tick', e => {
  const d = e.detail || {};
  if (d.state) applyState(d.state);
  if (d.fast) renderFast(d.fast);
});
async function pollFast() {
  if (streamFresh()) { setTimeout(pollFast, 1000); return; }
  try {
    const res = await fetch('/api/fast', { cache: 'no-store', signal: AbortSignal.timeout(2500) });
    if (res.ok) renderFast(await res.json());
  } catch { /* optional lane */ }
  setTimeout(pollFast, 1000);
}
pollFast();

function applyState(s) {
  render(s);
  beat(s.markets);
  const ts = s.timestamp || s.updated_at || 0;
  const fresh = ts > 0 && Math.abs(Date.now() / 1000 - ts) < 8;
  set('connection', (fresh ? 'TELEMETRY CONNECTED' : 'ENGINE IDLE / STALE') + (window.__grokVia ? ' · ' + window.__grokVia : ''));
  byId('lamp').className = fresh ? 'online' : '';
  set('clock', new Date().toLocaleTimeString());
  byId('kill').hidden = false;
}
async function poll() {
  if (document.hidden) { setTimeout(poll, 2000); return; }
  if (streamFresh()) { setTimeout(poll, 1000); return; }
  try {
    const response = await fetch('/api/state', { cache: 'no-store', signal: AbortSignal.timeout(3500) });
    if (!response.ok) throw new Error('HTTP');
    applyState(await response.json());
  } catch {
    set('connection', 'DISCONNECTED');
    byId('lamp').className = '';
    set('status', 'Telemetry unavailable · last values retained');
  }
  setTimeout(poll, 500);   // engine publishes each 1 s and on every status change
}
poll();

/* ---------- 07 · emergency stop (kill switch) ----------
   Two-step confirm, loopback-only POST /api/shutdown. The engine answers by
   cancelling every working order and closing every position before exiting. */
const kill = byId('kill');
const KILL_LABEL = kill.textContent;
let killArmed = false, killTimer = null;
kill.addEventListener('click', async () => {
  if (!killArmed) {
    killArmed = true;
    kill.textContent = 'NHẤP LẦN NỮA ĐỂ XÁC NHẬN TẮT HỆ THỐNG';
    kill.classList.add('armed');
    killTimer = setTimeout(() => {
      killArmed = false;
      kill.classList.remove('armed');
      kill.textContent = KILL_LABEL;
    }, 6000);
    return;
  }
  clearTimeout(killTimer);
  killArmed = false;
  kill.classList.remove('armed');
  kill.disabled = true;
  kill.textContent = 'ĐANG TẮT · ĐANG ĐÓNG VỊ THẾ…';
  try {
    const response = await fetch('/api/shutdown', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm: 'SHUTDOWN' })
    });
    if (!response.ok) throw new Error('HTTP');
    set('status', 'SHUTDOWN REQUESTED · cancelling orders & flattening positions');
  } catch {
    set('status', 'Shutdown request failed · bot may already be stopped');
    kill.disabled = false;
    kill.textContent = KILL_LABEL;
  }
});
