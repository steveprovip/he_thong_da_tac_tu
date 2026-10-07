'use strict';
/* GROKTOPUS Pro Terminal deck.
   Data: Server-Sent Events (/api/stream, pushed on change <= 4 Hz); falls back to
   1 s polling of /api/state + /api/fast when the stream is down. Re-broadcasts each
   payload as `grok:tick` so app.js renders the legacy panels from the same frame.
   Drawing: ONE requestAnimationFrame loop, canvases redrawn only when dirty or while
   a tween is settling; DPR-aware, paused by the browser when the tab is hidden.
   CSP-safe: no inline styles (CSSOM setProperty only), no eval, same-origin fetch. */
(() => {
  const $ = id => document.getElementById(id);
  if (!$('cv-pnl')) return;
  const C = { cyan: '#00f2fe', em: '#00ffa3', cr: '#ff4b4b', gold: '#ffb703', dim: '#5f828b',
    grid: 'rgba(130,200,220,.09)', text: '#d8f3f0' };
  const fin = v => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v));
  const fx = (v, d = 2) => fin(v) ? Number(v).toFixed(d) : '—';
  const sg = (v, d = 4) => fin(v) ? (v >= 0 ? '+' : '') + Number(v).toFixed(d) : '—';
  const usdK = v => !fin(v) ? '—' : Math.abs(v) >= 1e6 ? '$' + (v / 1e6).toFixed(2) + 'M'
    : Math.abs(v) >= 1e3 ? '$' + (v / 1e3).toFixed(1) + 'k' : '$' + Number(v).toFixed(0);
  const txt = (id, v) => { const n = $(id); if (n && n.textContent !== v) n.textContent = v; };
  const cls = (id, c) => { const n = $(id); if (n) n.className = c; };
  const hms = t => fin(t) ? new Date(t * 1000).toLocaleTimeString('en-GB') : '—';
  const NAMES = ['VESKA', 'NORO', 'LUMEN', 'TIDAL', 'ZEPHR', 'RUNE', 'OKAPI', 'MARIN'];
  const ROLES = { VESKA: 'Exec · AuthToken', NORO: 'Fair-value arb', LUMEN: 'Flow · OBI/CVD', TIDAL: 'Scanner',
    ZEPHR: 'Cost gate', RUNE: 'Risk veto', OKAPI: 'Kelly sizer', MARIN: 'Exit guard' };

  /* ------------------------------------------------------------ canvas infra */
  class Surface {
    constructor(id, draw) {
      this.c = $(id); this.ctx = this.c.getContext('2d'); this.base = Number(this.c.getAttribute('height')) || 60;
      this.draw = draw; this.w = 0; this.dirty = true;
      this.fit(); new ResizeObserver(() => this.fit()).observe(this.c);
    }
    fit() {
      const w = Math.max(1, Math.round(this.c.getBoundingClientRect().width));
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      if (w === this.w && dpr === this.dpr) return;
      this.w = w; this.h = this.base; this.dpr = dpr;
      this.c.width = Math.round(w * dpr); this.c.height = Math.round(this.h * dpr);
      this.c.style.setProperty('height', this.h + 'px');
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0); this.dirty = true;
    }
    render() { if (!this.dirty) return; this.dirty = false; this.ctx.clearRect(0, 0, this.w, this.h); this.draw(this.ctx, this.w, this.h); }
  }
  const tweens = new Map();               // key -> {v, target, k}
  const tw = (key, target, k = 140) => {
    const t = tweens.get(key);
    if (!t) { tweens.set(key, { v: target, target, k }); return; }
    t.target = target;
  };
  const tv = key => (tweens.get(key) || { v: 0 }).v;

  function spark(ctx, w, h, vals, color, zero) {
    const pts = vals.filter(fin).map(Number);
    ctx.strokeStyle = C.grid; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, h - .5); ctx.lineTo(w, h - .5); ctx.stroke();
    if (pts.length < 2) return;
    let lo = Math.min(...pts), hi = Math.max(...pts);
    if (zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    if (hi - lo < 1e-12) { hi += 1e-6; lo -= 1e-6; }
    const X = i => i / (pts.length - 1) * (w - 2) + 1, Y = v => h - 3 - (v - lo) / (hi - lo) * (h - 6);
    if (zero) { ctx.strokeStyle = 'rgba(255,183,3,.35)'; ctx.setLineDash([3, 4]); ctx.beginPath(); ctx.moveTo(0, Y(0)); ctx.lineTo(w, Y(0)); ctx.stroke(); ctx.setLineDash([]); }
    const g = ctx.createLinearGradient(0, 0, 0, h); g.addColorStop(0, color + '55'); g.addColorStop(1, color + '00');
    ctx.beginPath(); pts.forEach((v, i) => i ? ctx.lineTo(X(i), Y(v)) : ctx.moveTo(X(i), Y(v)));
    ctx.lineTo(X(pts.length - 1), h); ctx.lineTo(X(0), h); ctx.closePath(); ctx.fillStyle = g; ctx.fill();
    ctx.beginPath(); pts.forEach((v, i) => i ? ctx.lineTo(X(i), Y(v)) : ctx.moveTo(X(i), Y(v)));
    ctx.strokeStyle = color; ctx.lineWidth = 1.6; ctx.shadowColor = color; ctx.shadowBlur = 8; ctx.stroke(); ctx.shadowBlur = 0;
    ctx.fillStyle = color; ctx.beginPath(); ctx.arc(X(pts.length - 1), Y(pts[pts.length - 1]), 2.6, 0, 7); ctx.fill();
  }

  /* ------------------------------------------------------------ state */
  let fast = null, state = null, lastTradeKey = null;
  const pnlCurve = [], cvdSeries = [];

  const sPnl = new Surface('cv-pnl', (ctx, w, h) => {
    const curve = (fast && fast.pnl && fast.pnl.curve) || [];
    const vals = curve.length > 1 ? curve : pnlCurve;
    const up = vals.length < 2 || vals[vals.length - 1] >= vals[0];
    spark(ctx, w, h, vals, up ? C.em : C.cr, false);
  });
  const sCvd = new Surface('cv-cvd', (ctx, w, h) => spark(ctx, w, h, cvdSeries, C.cyan, true));
  const sGauge = new Surface('cv-gauge', (ctx, w, h) => {
    const cx = w / 2, cy = h - 18, r = Math.min(w * .42, h - 30);
    const ang = v => Math.PI + (Math.max(-1, Math.min(1, v)) + 1) / 2 * Math.PI;
    const grad = ctx.createLinearGradient(cx - r, 0, cx + r, 0);
    grad.addColorStop(0, C.cr); grad.addColorStop(.5, C.gold); grad.addColorStop(1, C.em);
    ctx.lineCap = 'round';
    ctx.lineWidth = 12; ctx.strokeStyle = 'rgba(255,255,255,.05)'; ctx.beginPath(); ctx.arc(cx, cy, r, Math.PI, 2 * Math.PI); ctx.stroke();
    ctx.lineWidth = 3; ctx.strokeStyle = grad; ctx.beginPath(); ctx.arc(cx, cy, r, Math.PI, 2 * Math.PI); ctx.stroke();
    for (let i = -1; i <= 1.001; i += .25) {
      const a = ang(i); ctx.strokeStyle = Math.abs(i) < 1e-9 ? C.gold : C.grid; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(cx + Math.cos(a) * (r - 14), cy + Math.sin(a) * (r - 14)); ctx.lineTo(cx + Math.cos(a) * (r + 7), cy + Math.sin(a) * (r + 7)); ctx.stroke();
    }
    const pr = tv('pressure'), ob = tv('obi');
    const inner = r - 22; ctx.lineWidth = 6; ctx.strokeStyle = ob >= 0 ? C.em : C.cr; ctx.globalAlpha = .55;
    ctx.beginPath(); ctx.arc(cx, cy, inner, ang(0), ang(ob), ob < 0); ctx.stroke(); ctx.globalAlpha = 1;
    const a = ang(pr), col = pr > .15 ? C.em : pr < -.15 ? C.cr : C.gold;
    ctx.strokeStyle = col; ctx.lineWidth = 2.4; ctx.shadowColor = col; ctx.shadowBlur = 14;
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + Math.cos(a) * (r - 4), cy + Math.sin(a) * (r - 4)); ctx.stroke(); ctx.shadowBlur = 0;
    ctx.fillStyle = col; ctx.beginPath(); ctx.arc(cx, cy, 5, 0, 7); ctx.fill();
    ctx.font = '700 22px "JetBrains Mono", ui-monospace, monospace'; ctx.textAlign = 'center'; ctx.fillStyle = col;
    ctx.fillText((pr >= 0 ? '+' : '') + (pr * 100).toFixed(0), cx, cy - r * .38);
    ctx.font = '600 9px "JetBrains Mono", ui-monospace, monospace'; ctx.fillStyle = C.dim;
    ctx.fillText(pr > .15 ? 'BUY PRESSURE' : pr < -.15 ? 'SELL PRESSURE' : 'BALANCED', cx, cy - r * .38 + 14);
    ctx.textAlign = 'left'; ctx.fillText('SELL', cx - r - 4, cy + 14); ctx.textAlign = 'right'; ctx.fillText('BUY', cx + r + 4, cy + 14);
  });
  const sLiq = new Surface('cv-liq', (ctx, w, h) => {
    const series = (fast && fast.sniper && fast.sniper.liq_series) || [];
    const now = Math.floor(Date.now() / 1000), span = 120, mid = h / 2, bw = w / span;
    ctx.strokeStyle = C.grid; ctx.beginPath(); ctx.moveTo(0, mid); ctx.lineTo(w, mid); ctx.stroke();
    let mx = 1; for (const b of series) mx = Math.max(mx, b[1], b[2]);
    const sc = v => Math.sqrt(v / mx) * (mid - 3);
    for (const [sec, lo, sh] of series) {
      const x = w - (now - sec + 1) * bw; if (x < -bw) continue;
      if (lo > 0) { const hh = sc(lo); ctx.fillStyle = C.cr; ctx.shadowColor = C.cr; ctx.shadowBlur = lo === mx ? 12 : 0; ctx.fillRect(x, mid - hh, Math.max(1, bw - 1), hh); }
      if (sh > 0) { const hh = sc(sh); ctx.fillStyle = C.em; ctx.shadowColor = C.em; ctx.shadowBlur = sh === mx ? 12 : 0; ctx.fillRect(x, mid + 1, Math.max(1, bw - 1), hh); }
    }
    ctx.shadowBlur = 0; ctx.fillStyle = C.dim; ctx.font = '9px "JetBrains Mono", monospace'; ctx.textAlign = 'right';
    ctx.fillText('peak ' + usdK(mx) + '/s', w - 4, 10); ctx.textAlign = 'left'; ctx.fillText('-120s', 2, h - 3);
  });
  const surfaces = [sPnl, sCvd, sGauge, sLiq];

  /* ------------------------------------------------------------ widgets */
  const matrix = $('swarm-matrix'), rows = {};
  for (const n of NAMES) {
    const r = document.createElement('div'); r.className = 'mrow'; r.setAttribute('role', 'row');
    r.innerHTML = '<span class="mname"></span><span class="mrole"></span><span class="mvote">WAIT</span><span class="mconf"><b></b></span><span class="mpct">—</span>';
    r.querySelector('.mname').textContent = n; r.querySelector('.mrole').textContent = ROLES[n];
    matrix.append(r); rows[n] = r;
  }

  function renderPnl(p) {
    if (!p) return;
    if (!tweens.has('bal')) txt('pnl-balance', (Number(p.balance) || 0).toFixed(4));
    tw('bal', Number(p.balance) || 0, 260);
    const d = (Number(p.realized) || 0) + (Number(p.unrealized) || 0);
    txt('pnl-delta', sg(d, 4) + ' U'); cls('pnl-delta', 'num ' + (d >= 0 ? 'up' : 'down'));
    txt('pnl-real', sg(p.realized, 4)); txt('pnl-unreal', sg(p.unrealized, 4));
    txt('pnl-wins', `${p.wins ?? 0} / ${p.trades ?? 0}`); txt('pnl-cap', fx(p.max_loss_per_trade, 2) + ' U');
    const t = p.last_trade;
    if (t) {
      txt('pnl-last', `${hms(t.closed)} · ${t.symbol} ${t.side} ${t.reason} ${sg(t.net_pct, 2)}% · ${sg(t.pnl, 4)} U · ${fx(t.hold_s, 0)}s`);
      const key = `${t.symbol}|${t.closed}`;
      if (lastTradeKey !== null && key !== lastTradeKey) {
        const card = $('pnl-card'); card.classList.remove('pulse-win', 'pulse-loss'); void card.offsetWidth;
        card.classList.add(t.pnl >= 0 ? 'pulse-win' : 'pulse-loss');
      }
      lastTradeKey = key;
    }
    pnlCurve.push(Number(p.balance)); if (pnlCurve.length > 180) pnlCurve.shift();
    sPnl.dirty = true;
  }

  /* engine (python -m core.engine) drives T1/T2 when no research fast lane is running */
  function renderEngineHero(s) {
    const pos = s.position, up = pos ? Number(pos.upnl) || 0 : 0, wallet = Number(s.wallet);
    if (!fin(wallet)) return;
    const sess = s.session || {}, prof = s.profile || {};
    renderPnl({ balance: wallet + up, realized: sess.realized, unrealized: up, wins: sess.wins, trades: sess.trades,
      max_loss_per_trade: prof.max_loss_usdt ?? null, last_trade: (s.trade_history || [])[0]
        ? Object.assign({}, s.trade_history[0], { closed: s.trade_history[0].t, net_pct: s.trade_history[0].pnl_pct }) : null });
    if (!fin(prof.max_loss_usdt)) txt('pnl-cap', `SL ${fx(prof.sl_pct, 2)}%`);
    txt('pnl-mode', `${String(s.engine_mode || '').toUpperCase()} · ${s.venue || ''}`);
    const f = s.features || {};
    if (!fast || !fast.flow) {
      const obi = fin(f.obi) ? +f.obi : 0, cvd = fin(f.cvd_norm) ? +f.cvd_norm : 0;
      renderFlow({ symbol: s.symbol, price: f.price, obi_ema: f.obi, cvd_window: f.cvd_60s, cvd_session: null, cvd_velocity: f.cvd_vel_10s,
        buy_usd: null, sell_usd: null, pressure: (obi + cvd) / 2, series: (cvdHist.push([0, f.cvd_60s]), cvdHist.slice(-120)) });
      if (cvdHist.length > 240) cvdHist.splice(0, 120);
    }
  }
  const cvdHist = [];

  function renderFlow(f) {
    if (!f) { txt('flow-sym', 'OFF'); return; }
    txt('flow-sym', `${f.symbol} · ${fin(f.price) ? fx(f.price, 1) : '—'}`);
    tw('pressure', fin(f.pressure) ? Number(f.pressure) : 0, 180); tw('obi', fin(f.obi_ema) ? Number(f.obi_ema) : 0, 180);
    txt('flow-obi', fin(f.obi_ema) ? sg(f.obi_ema, 3) : '—'); txt('flow-cvd', fin(f.cvd_window) ? (f.cvd_window >= 0 ? '+' : '−') + usdK(Math.abs(f.cvd_window)) : '—');
    cls('flow-cvd', 'num ' + (Number(f.cvd_window) >= 0 ? 'up' : 'down'));
    txt('flow-bs', `${usdK(f.buy_usd)} / ${usdK(f.sell_usd)}`); if (fin(f.cvd_velocity)) { txt('flow-cvds', (f.cvd_velocity >= 0 ? '+' : '−') + usdK(Math.abs(f.cvd_velocity)) + '/s'); cls('flow-cvds', 'num ' + (Math.abs(f.cvd_velocity) >= 50000 ? (f.cvd_velocity > 0 ? 'up' : 'down') : '')); }
    else { txt('flow-cvds-label', 'CVD SESSION'); txt('flow-cvds', fin(f.cvd_session) ? (f.cvd_session >= 0 ? '+' : '−') + usdK(Math.abs(f.cvd_session)) : '—'); }
    cvdSeries.length = 0; for (const s of f.series || []) cvdSeries.push(s[1]);
    sCvd.dirty = true; sGauge.dirty = true;
  }

  function renderSwarm(s) {
    if (!s) return;
    const ex = fin(s.threshold) ? +s.threshold : fin(s.execute_gate) ? +s.execute_gate : .768;
    const vg = fin(s.veto_threshold) ? +s.veto_threshold : fin(s.veto_gate) ? +s.veto_gate : .70;
    const score = Math.max(0, Math.min(1, Number(s.score) || 0));
    txt('swarm-score', (score * 100).toFixed(1) + '%');
    cls('swarm-score', 'num ' + (score >= ex ? 'up' : score >= vg ? 'warn' : 'down'));
    const fill = $('swarm-fill'); fill.style.setProperty('--w', (score * 100).toFixed(1) + '%');
    fill.className = score >= ex ? 'pass' : score >= vg ? 'near' : 'blocked';
    document.querySelector('.swarm-bar .mk.exec').style.setProperty('--x', ex * 100 + '%');
    document.querySelector('.swarm-bar .mk.veto').style.setProperty('--x', vg * 100 + '%');
    const votes = s.agents || [];
    let approve = 0;
    for (const n of NAMES) {
      const v = votes.find(a => a.agent === n), r = rows[n];
      const stance = v ? (v.label || (v.veto ? 'VETO' : v.side || 'HOLD')) : 'WAIT';
      const st = v && v.state ? v.state.toLowerCase() : (v ? (v.veto ? 'veto' : (v.side || 'hold').toLowerCase()) : 'wait');
      if (v && !v.veto && (v.side === 'BUY' || v.side === 'SELL')) approve++;
      const vote = r.querySelector('.mvote'); vote.textContent = stance;
      vote.className = 'mvote ' + st + (v && (v.side === 'BUY' || v.side === 'SELL') ? ' ' + v.side.toLowerCase() : '');
      const c = v && fin(v.confidence) ? Number(v.confidence) : 0;
      r.querySelector('.mconf b').style.setProperty('--w', (c * 100).toFixed(0) + '%');
      r.querySelector('.mpct').textContent = v ? (c * 100).toFixed(0) + '%' : '—';
      r.title = (v && v.reason) || 'awaiting engine';
    }
    txt('swarm-quorum', votes.length ? `${approve}/8 DIRECTIONAL` : 'ENGINE IDLE');
    const live = fin(s.auth_at) && (Date.now() / 1000 - s.auth_at) < 6;
    txt('veska-token', `VESKA · ${live ? 'AUTHTOKEN LIVE (HMAC 5s)' : 'TOKEN ×' + (s.authorized ?? 0)}`);
    cls('veska-token', 'chip ' + (live ? 'cyan blink' : ''));
    const rune = votes.find(a => a.agent === 'RUNE');
    const guarding = rune && rune.state === 'POSITION', idle = rune && rune.state === 'SCANNING';
    const veto = !guarding && !idle && ((rune && rune.veto) || (votes.length && score < vg && !rune?.state));
    txt('rune-veto', 'RUNE · ' + (guarding ? 'GUARDING POSITION' : veto ? 'VETO: ' + String(rune?.label || 'ACTIVE').slice(0, 32)
      : votes.length ? 'CLEAR' : 'STANDBY'));
    cls('rune-veto', 'chip ' + (guarding ? 'cyan' : veto ? 'red blink' : votes.length ? 'green' : ''));
  }

  const seen = new Set();
  function renderLiq(o) {
    const sn = (o && o.sniper) || {}, st = sn.stats || {}, ws = (o && o.ws) || {};
    txt('liq-ws', ws.connected ? `WS LIVE · ${st.liquidations ?? 0} liq` : 'WS DOWN');
    cls('liq-ws', 'chip ' + (ws.connected ? 'green' : 'red'));
    txt('liq-total', `Σ ${usdK(st.liq_usd)} · cascade ${st.cascades ?? 0} · GTX ${st.maker_filled ?? 0}/${st.maker_posted ?? 0} · JEV ≤ ${fx(st.score_us_max, 0)}µs`);
    const list = $('liq-stream');
    const feed = (sn.feed || []).slice().reverse();
    for (const e of feed) {
      const key = `${e.symbol}|${e.recv}|${e.notional}`;
      if (seen.has(key)) continue; seen.add(key);
      const li = document.createElement('li'); li.className = 'new ' + (e.liquidated === 'LONG' ? 'long' : 'short');
      li.innerHTML = '<time></time><b></b><span class="side"></span><span class="usd"></span><span class="px"></span><i></i>';
      li.querySelector('time').textContent = hms(e.recv); li.querySelector('b').textContent = e.symbol.replace('USDT', '');
      li.querySelector('.side').textContent = e.liquidated; li.querySelector('.usd').textContent = usdK(e.notional);
      li.querySelector('.px').textContent = fx(e.price, e.price < 1 ? 5 : 2);
      li.querySelector('i').style.setProperty('--w', Math.min(100, Math.sqrt(e.notional / 500000) * 100).toFixed(0) + '%');
      list.prepend(li);
      li.addEventListener('animationend', () => li.classList.remove('new'), { once: true });
    }
    while (list.children.length > 14) list.lastChild.remove();
    if (seen.size > 500) { const keep = [...seen].slice(-200); seen.clear(); keep.forEach(k => seen.add(k)); }
    const c = (sn.cascades || [])[0], pend = sn.pending, pos = sn.position;
    txt('liq-cascade', pos ? `ĐANG GIỮ ${pos.symbol} ${pos.side} @ ${fx(pos.entry, 5)} · TP ${fx(pos.tp, 5)} · SL ${fx(pos.sl, 5)} · uPnL ${sg(pos.upnl, 4)} U · ${fx(pos.age_s, 0)}s`
      : pend ? `GTX ${pend.symbol} ${pend.side} @ ${fx(pend.limit, 5)} chờ khớp ${fx(pend.age_s, 1)}s`
        : c ? `${hms(c.t)} CASCADE ${c.symbol} ${c.liquidated} ${usdK(c.usd_window)} ×${c.count} · wick ${fx(c.move_pct, 2)}% · JEV ${fx(c.jev && c.jev.score, 1)} → ${c.action}`
          : 'Chưa có cascade ≥ ngưỡng');
    sLiq.dirty = true;
  }

  function renderPm(o) {
    const pmo = (o && o.pm15) || {}, sc = pmo.score || {}, tb = $('pm-radar');
    txt('pm-score', sc.n ? `n=${sc.n} · hit ${(sc.hit_rate * 100).toFixed(0)}% · ROI ${sg(sc.mean_roi * 100, 1)}%`
      : `${sc.open ?? 0} open · scan ${fx(pmo.scan_ms, 0)}ms`);
    const rowsPm = pmo.rows || [];
    if (!rowsPm.length) return;
    tb.replaceChildren();
    for (const r of rowsPm) {
      const tr = document.createElement('tr'); if (r.flag) tr.className = 'hot';
      const m = Math.max(0, Math.floor((r.tau_s || 0) / 60)), s = Math.max(0, Math.floor((r.tau_s || 0) % 60));
      const cells = [r.asset.toUpperCase(), `${m}:${String(s).padStart(2, '0')}`, sg(r.move_pct, 3) + '%',
        fin(r.impulse_pct) ? sg(r.impulse_pct, 3) + '%' : '—', fx(r.p_up, 3), fx(r.up_ask, 2), fx(r.down_ask, 2),
        fin(r.edge) ? sg(r.edge * 100, 1) + 'pp' : '—', r.trigger ? `${r.trigger} ${r.side}` : '—'];
      cells.forEach((v, i) => {
        const td = document.createElement('td'); td.textContent = v;
        if (i === 3 && fin(r.impulse_pct)) td.className = Math.abs(r.impulse_pct) >= 0.3 ? 'imp' : '';
        if (i === 7 && fin(r.edge)) td.className = r.edge > 0 ? 'up' : 'down';
        if (i === 8 && r.trigger) td.className = 'trig';
        tr.append(td);
      });
      tb.append(tr);
    }
  }

  /* ------------------------------------------------------------ data plane */
  function handle(payload, via) {
    if (!payload) return;
    window.__grokLastAt = Date.now(); window.__grokVia = via;
    if (payload.state) { state = payload.state; renderSwarm(state); if (state.engine_mode) renderEngineHero(state); }
    if (payload.fast && payload.fast.status !== 'no fast lane yet' && fin(payload.fast.updated_at)
        && Date.now() / 1000 - payload.fast.updated_at < 30) {
      fast = payload.fast; if (!(state && state.engine_mode)) renderPnl(fast.pnl);
      renderFlow(fast.flow); renderLiq(fast); renderPm(fast);
      document.querySelectorAll('.liq-card,.pm-card').forEach(n => n.classList.remove('dormant'));
      if (!(state && state.engine_mode)) txt('pnl-mode', `SHADOW · ${via}`);
    }
    document.dispatchEvent(new CustomEvent('grok:tick', { detail: payload }));
  }
  let es = null;
  function connect() {
    try {
      es = new EventSource('/api/stream');
      es.addEventListener('tick', e => { try { handle(JSON.parse(e.data), 'SSE'); } catch { /* torn frame */ } });
      es.addEventListener('hb', () => { window.__grokLastAt = Date.now(); window.__grokVia = 'SSE'; });
    } catch { es = null; }
  }
  connect();
  async function fallback() {
    if (!document.hidden && Date.now() - (window.__grokLastAt || 0) > 3000) {
      try {
        const [a, b] = await Promise.all(['/api/state', '/api/fast'].map(u =>
          fetch(u, { cache: 'no-store', signal: AbortSignal.timeout(2500) }).then(r => r.ok ? r.json() : null).catch(() => null)));
        if (a || b) handle({ state: a, fast: b }, 'POLL');
      } catch { /* offline */ }
    }
    setTimeout(fallback, 1000);
  }
  setTimeout(fallback, 1500);

  /* ------------------------------------------------------------ frame loop */
  let last = performance.now(), liqTick = 0;
  function frame(now) {
    const dt = Math.min(100, now - last); last = now;
    for (const [key, t] of tweens) {
      if (Math.abs(t.target - t.v) > 1e-7) {
        t.v += (t.target - t.v) * (1 - Math.exp(-dt / t.k));
        if (Math.abs(t.target - t.v) < 1e-6) t.v = t.target;
        if (key === 'bal') txt('pnl-balance', t.v.toFixed(4)); else sGauge.dirty = true;
      }
    }
    if (now - liqTick > 1000) { liqTick = now; sLiq.dirty = true; }   // scroll the 120 s window
    for (const s of surfaces) s.render();
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
})();
