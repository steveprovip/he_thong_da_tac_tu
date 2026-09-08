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
    t.chip.textContent = m.status === 'WAITING' || m.status === 'WARMUP' ? 'WAIT' : stance;
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
    t.root.classList.toggle('authorized', m.status === 'AUTHORIZED');
    order.push(t.root);
  }
  strip.replaceChildren(...order);
}

/* ---------- 03 · equity trace ---------- */
let trace = [];
function renderEquity(history) {
  const points = history.filter(p => finite(p.value));
  const line = byId('equity-line'), area = byId('equity-area');
  if (!points.length) { line.setAttribute('d', ''); area.setAttribute('d', ''); return; }
  trace = points;
  const values = points.map(p => p.value);
  const min = Math.min(...values), max = Math.max(...values), range = max - min || 1;
  const X = i => points.length < 2 ? 0 : i / (points.length - 1) * 900;
  const Y = v => max === min ? 70 : 125 - (v - min) / range * 110;
  // Straight segments at 600 points read as a smooth curve and stay jank-free;
  // a spline is only worth its cost while the trace is sparse.
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
  set('equity-range', number(min) + ' — ' + number(max) + ' USDT');
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
  if (!lab || !lab.probes) {
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

  const positions = byId('positions');
  positions.replaceChildren();
  if (!(s.positions ?? []).length) positions.textContent = 'No active positions';
  for (const p of s.positions ?? [])
    positions.append(el('div', 'position', `${p.symbol} / ${p.side} · ${number(p.filled, 4)} · SL ${number(p.stop)} · ${p.protection}`));

  const events = byId('events');
  events.replaceChildren();
  for (const e of s.events ?? []) {
    const li = document.createElement('li'), time = document.createElement('time');
    time.textContent = new Date(e.t * 1000).toLocaleTimeString();
    li.append(time, document.createTextNode(e.message));
    events.append(li);
  }
}

async function poll() {
  if (document.hidden) { setTimeout(poll, 2000); return; }
  try {
    const response = await fetch('/api/state', { cache: 'no-store', signal: AbortSignal.timeout(3500) });
    if (!response.ok) throw new Error('HTTP');
    const s = await response.json();
    render(s);
    const fresh = s.updated_at && Date.now() / 1000 - s.updated_at < 5;
    set('connection', fresh ? 'TELEMETRY CONNECTED' : 'ENGINE IDLE / STALE');
    byId('lamp').className = fresh ? 'online' : '';
    set('clock', new Date().toLocaleTimeString());
    byId('kill').hidden = false;
  } catch {
    set('connection', 'DISCONNECTED');
    byId('lamp').className = '';
    set('status', 'Telemetry unavailable · last values retained');
  }
  setTimeout(poll, 1000);
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
