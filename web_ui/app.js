'use strict';
/* GROKTOPUS cockpit (2026-11-07). Renders what terminal.js (P&L hero, OBI/CVD gauge, swarm matrix) and desk.js
   (mode, coin, test and close controls) do not own: session net / win rate / profit factor, active position, equity
   trace, regime banner, carry table + simulated carry book, recent fills, risk bar, header and status bar, and the
   two-step emergency stop. Data: the same `grok:tick` frame (SSE, terminal.js); /api/state polling only while the
   stream is stale. CSP-safe: textContent, setAttribute on SVG paths, same-origin fetch. */
(() => {
  const $ = id => document.getElementById(id);
  const fin = v => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v));
  const fx = (v, d = 2) => fin(v) ? Number(v).toFixed(d) : '—';
  const sg = (v, d = 4) => fin(v) ? (v >= 0 ? '+' : '') + Number(v).toFixed(d) : '—';
  const pdp = v => !fin(v) ? 2 : Math.abs(v) < 1 ? 5 : Math.abs(v) < 100 ? 4 : 1;
  const hms = t => fin(t) && t > 0 ? new Date(t * 1000).toLocaleTimeString('en-GB') : '—';
  const txt = (id, v) => { const n = $(id); if (n && n.textContent !== v) n.textContent = v; };
  const cls = (id, c) => { const n = $(id); if (n && n.className !== c) n.className = c; };
  const tone = v => fin(v) ? (v > 0 ? 'up' : v < 0 ? 'down' : '') : '';
  const n = v => fin(v) ? String(Math.round(Number(v))) : '—';     // counters: UNKNOWN is '—', never 'undefined'

  function fill(id, rows, cols, empty) {
    const tb = $(id);
    if (!tb) return;
    tb.replaceChildren();
    if (!rows.length) {
      const tr = document.createElement('tr'), td = document.createElement('td');
      td.colSpan = cols; td.className = 'empty'; td.textContent = empty; tr.append(td); tb.append(tr); return;
    }
    for (const r of rows) {
      const tr = document.createElement('tr');
      if (r.cls) tr.className = r.cls;
      for (const [v, c] of r.cells) { const td = document.createElement('td'); td.textContent = v; if (c) td.className = c; tr.append(td); }
      tb.append(tr);
    }
  }

  function hero(s) {
    const net = fin(s.pnl) ? Number(s.pnl) : (s.session || {}).realized;
    txt('ck-net', sg(net, 4) + ' U'); cls('ck-net', 'num big ' + tone(net));
    const st = s.stats || {};
    if (fin(st.n) && st.n > 0) {
      txt('ck-wl', `${st.wins}W / ${st.losses}L · ${fx(st.win_rate * 100, 0)}%`);
      txt('ck-pf', st.no_losses ? '∞ (no loss)' : fx(st.profit_factor, 2)); cls('ck-pf', 'num ' + (st.profit_factor >= 1 ? 'up' : 'down'));
    } else { txt('ck-wl', '—'); txt('ck-pf', '—'); }
  }

  function position(s) {
    const pf = s.portfolio || {};
    const p = (pf.rows && pf.rows[0]) || (s.positions || [])[0] || null;
    if (!p) {
      txt('ck-pos-side', 'FLAT'); cls('ck-pos-side', 'chip');
      for (const k of ['sym', 'qty', 'entry', 'mark', 'upnl', 'roe', 'tp', 'sl', 'liq', 'margin']) txt('ck-pos-' + k, '—');
      cls('ck-pos-upnl', 'num'); cls('ck-pos-roe', 'num');
      txt('ck-pos-note', `No open position · ${String(s.phase || 'IDLE')}`);
      return;
    }
    txt('ck-pos-tp', fin(p.tp) ? fx(p.tp, pdp(p.tp)) + (p.tp1_filled ? ' ✓' : '') : '—');
    txt('ck-pos-sl', fin(p.sl) ? fx(p.sl, pdp(p.sl)) : '—');
    txt('ck-pos-liq', fin(p.liq) && p.liq > 0 ? fx(p.liq, pdp(p.liq)) : '—');
    txt('ck-pos-margin', fin(p.margin) ? fx(p.margin, 2) + ' U' : '—');
    txt('ck-pos-note', `${p.margin_type || ''} ${p.leverage ? p.leverage + 'x' : ''} · MARIN ${p.marin || p.stage || 'guarding'}`.trim());
    const long = p.side === 'LONG' || p.side === 'BUY';
    txt('ck-pos-side', long ? 'LONG' : 'SHORT'); cls('ck-pos-side', 'chip ' + (long ? 'cyan' : 'amber'));
    txt('ck-pos-sym', String(p.symbol || '—')); txt('ck-pos-qty', String(p.size ?? p.qty ?? '—'));
    txt('ck-pos-entry', fx(p.entry, pdp(p.entry))); txt('ck-pos-mark', fx(p.mark, pdp(p.mark)));
    txt('ck-pos-upnl', sg(p.upnl, 4) + ' U'); cls('ck-pos-upnl', 'num ' + tone(p.upnl));
    txt('ck-pos-roe', sg(p.roe_pct, 2) + '%'); cls('ck-pos-roe', 'num ' + tone(p.roe_pct));
  }

  function trace(history) {
    const pts = (history || []).map(h => Number(h.value)).filter(Number.isFinite);
    const line = $('ck-eq-line'), area = $('ck-eq-area');
    if (!line || pts.length < 2) { txt('ck-eq-range', 'AWAITING DATA'); return; }
    const lo = Math.min(...pts), hi = Math.max(...pts), span = hi - lo || Math.max(Math.abs(hi) * 1e-6, 1e-9);
    const W = 600, H = 150, pad = 8;
    const xy = pts.map((v, i) => [i / (pts.length - 1) * W, H - pad - (v - lo) / span * (H - 2 * pad)]);
    const d = 'M' + xy.map(([x, y]) => x.toFixed(1) + ' ' + y.toFixed(1)).join('L');
    line.setAttribute('d', d);
    area.setAttribute('d', d + `L${W} ${H}L0 ${H}Z`);
    line.setAttribute('stroke', pts[pts.length - 1] >= pts[0] ? '#00f2fe' : '#ff3b69');
    txt('ck-eq-range', `${fx(lo, 2)} – ${fx(hi, 2)} · ${pts.length} pts`);
  }

  function regime(s) {
    const q = s.quant || {}, rs = q.regime_state || null, gate = String(q.regime_gate || 'off').toUpperCase();
    const st = rs ? rs.state : 'UNKNOWN';
    const reason = rs ? String(rs.reason || '') : '';
    const warming = st === 'UNKNOWN' && gate !== 'OFF' && (!rs || reason.startsWith('WARMUP'));
    for (const n of document.querySelectorAll('.rg')) n.classList.toggle('on', n.dataset.state === st);
    txt('ck-regime-gate', `GATE ${gate}${st === 'UNKNOWN' ? ' · UNKNOWN' : ''}`);
    cls('ck-regime-gate', 'chip ' + (gate === 'ON' ? 'cyan' : gate === 'SHADOW' ? 'amber' : ''));
    const warmup = warming && fin(q.regime_warmup_required_s)
      ? `WARMUP ${Math.floor(Math.max(0, Number(q.regime_warmup_elapsed_s) || 0))}s/${Math.ceil(q.regime_warmup_required_s)}s`
        + (reason.startsWith('WARMUP after ') ? ` · ${reason.slice(7)}` : '') : null;
    txt('ck-regime-why', warmup || (rs ? `${st} · ${reason}${fin(rs.vol_ratio) ? ' · vol ×' + fx(rs.vol_ratio, 2) : ''}`
      + `${fin(rs.efficiency) ? ' · ER ' + fx(rs.efficiency, 2) : ''}` : 'no classifier state (gate off or warming up)'));
  }

  function carry(s) {
    const sim = s.carry_sim || s.carry_lane, shadow = s.funding_carry || {};   // the engine publishes carry_sim (carry_lane: 11-07 name)
    const rotation = (sim || {}).rotation || {};
    const rows = sim && sim.top ? sim.top.map(r => ({ cls: String(r.status || '').toLowerCase(), cells: [
      [r.coin], [r.short ? `${r.short} / ${r.long}` : '—'], [r.status === 'UNKNOWN' ? '—' : sg(r.spread_8h, 2)],
      [fin(r.apr_pct) ? fx(r.apr_pct, 1) + '%' : '—'], [r.status === 'HELD' ? 'HELD' : r.status === 'ENTRY' ? 'AUTO-ENTRY' : r.status]] }))
      : (shadow.top || []).map(r => ({ cls: r.eligible ? 'entry' : 'below', cells: [
        [r.coin], [`${r.short} / ${r.long}`], [sg(r.spread_8h_bps, 2)], [fx(r.apr_pct, 1) + '%'], [r.eligible ? 'ELIGIBLE' : 'BELOW']] }));
    fill('ck-carry', rows, 5, shadow.error ? 'Funding quotes: ' + shadow.error : 'Chưa có báo giá funding');
    txt('ck-carry-mode', sim ? 'SIMULATED' : shadow.mode ? 'SHADOW' : '—'); cls('ck-carry-mode', 'chip ' + (sim ? 'green' : ''));
    if (sim) {
      const pos = sim.positions || [];
      // one nowrap line that ellipsizes: the two yield numbers first, the full text in the tooltip
      txt('ck-carry-book', `SIM ${pos.length}/${(sim.rule || {}).slots ?? '—'} · P&L ${sg(sim.pnl, 4)} U`
        + (fin(sim.funding_total_usdt) ? ` · funding ${sg(sim.funding_total_usdt, 4)} U` : '')
        + (fin(sim.fees_total_usdt) ? ` · fees ${fx(sim.fees_total_usdt, 4)} U` : '') + ` · equity ${fx(sim.equity, 2)}`
        + (pos.length ? ' · ' + pos.map(p => `${p.coin} ${sg(p.net_usdt, 3)}`).join(' · ') : '') + (sim.kill ? ' · KILL: ' + sim.kill : ''));
      cls('ck-carry-book', 'book ' + tone(sim.pnl));
      $('ck-carry-book').title = $('ck-carry-book').textContent;
      const mins = fin(rotation.next_scan) ? Math.max(0, (rotation.next_scan - Date.now() / 1000) / 60) : null;
      const frz = sim.basis_freeze;            // 11-10 (g): a held pair's basis jumped against it -> no new pair for one interval
      txt('ck-carry-note', (frz ? `BASIS FREEZE ${frz.coin} ${sg(frz.drag_bps, 1)} bps · ` : '')
        + `ROTATION ${fx((rotation.interval_s || 3600) / 60, 0)}m · next ${mins === null ? '—' : fx(mins, 0) + 'm'}`
        + ` · ${(rotation.targets || []).join('/') || 'no target'} · BASIS STOP ${sg(rotation.basis_stop_bps, 1)} bps`);
      $('ck-carry-note').title = $('ck-carry-note').textContent;
    } else {
      txt('ck-carry-book', 'SIM BOOK off');
      txt('ck-carry-note', 'T6 public quote scan · instantaneous APR is not a forecast');
    }
  }

  function fills(s) {
    const h = (s.trade_history || []).slice(0, 5);
    fill('ck-fills', h.map(t => ({ cells: [
      [hms(t.t)], [String(t.symbol || '').replace('USDT', '')], [t.side, t.side === 'BUY' ? 'buy' : 'sell'],
      [sg(t.gross, 4), tone(t.gross)], [fin(t.fee) ? '-' + fx(Math.abs(t.fee), 4) : 'UNKNOWN', 'down'], [sg(t.pnl, 4), tone(t.pnl)]] })),
      6, 'Chưa có lệnh đóng');
    const ss = s.session || {};
    txt('ck-fills-sum', fin(ss.trades) ? `${ss.trades} · gross ${sg(ss.gross, 3)} · fee ${fx(ss.fees, 3)}` : '—');
  }

  function risk(s) {
    const a = s.analytics || {}, q = s.quant || {}, mk = (q.maker_markout || {})['5s'] || {};
    txt('ck-dd', fin(a.current_drawdown) ? fx(a.current_drawdown * 100, 2) + '%' : '—');
    cls('ck-dd', 'num ' + (a.current_drawdown > 0.05 ? 'down' : ''));
    const b = mk.BUY, sl = mk.SELL;
    txt('ck-mk5', `${fin(b) ? sg(b, 2) : '—'} / ${fin(sl) ? sg(sl, 2) : '—'}`);
    cls('ck-mk5', 'num ' + (Math.min(fin(b) ? b : 0, fin(sl) ? sl : 0) <= -1.5 ? 'down' : ''));
    txt('ck-vpin', fin(q.vpin) ? fx(q.vpin, 2) : '—'); cls('ck-vpin', 'num ' + (q.vpin > 0.65 ? 'down' : ''));
    const last = q.tox_last;
    txt('ck-tox', `${String(q.tox_guard || 'off').toUpperCase()}${Array.isArray(last) ? ' · ' + last[1] : ''}`);
    cls('ck-tox', Array.isArray(last) && last[1] !== 'QUOTE' ? 'warn' : '');
  }

  // 11-07 (d2): the other writer's 09 RISK & EXECUTION ANALYTICS panel, kept in the fills card
  function analytics(s) {
    const a = s.analytics || {}, att = a.agent_vote_attribution || {}, names = ['NORO', 'LUMEN', 'TIDAL'];
    txt('ck-attr', names.some(k => (att[k] || {}).trades) ? names.map(k => {
      const it = att[k] || {};
      return `${k} ${sg(it.net_usdt, 4)} U (n=${n(it.trades)} · aligned ${n(it.aligned)})`;
    }).join(' · ') : 'Waiting for settled alpha votes');
    const fr = a.execution_friction || {};
    txt('ck-fric', `cross p50 ${fx(fr.crossing_entry_slippage_median_bps, 2)} / p95 ${fx(fr.crossing_entry_slippage_p95_bps, 2)}`
      + ` (n=${n(fr.crossing_entry_slippage_n)}) · maker 5s ${fx(fr.maker_5s_markout_median_bps, 2)} (n=${n(fr.maker_5s_markout_n)})`);
    const mr = s.market_risk || {};
    txt('ck-mrisk', `${mr.regime || 'UNKNOWN'} · ${mr.reason || 'awaiting data'}`);
    cls('ck-mrisk', mr.allowed === false ? 'down' : '');
    const dd = $('ck-dd');
    if (dd) dd.title = fin(a.max_drawdown) ? `max ${fx(a.max_drawdown * 100, 2)}% · peak ${fx(a.equity_peak, 2)} U` : '';
    const sc = s.scanner;
    txt('ck-scan', sc ? `SCAN intents ${n(sc.intents)} · GTX fills ${n(sc.fills)} · cancels ${n(sc.cancels)}` : 'SCAN —');
  }

  function header(s) {
    const ts = s.timestamp || s.updated_at || 0, fresh = ts > 0 && Math.abs(Date.now() / 1000 - ts) < 8;
    txt('status', String(s.status || 'Waiting for engine'));
    txt('mode', String(s.mode || 'OFFLINE')); cls('mode', 'chip ' + (s.engine_mode === 'live' ? 'red' : s.engine_mode ? 'cyan' : ''));
    txt('connection', fresh ? 'TELEMETRY LIVE' : 'ENGINE IDLE / STALE'); cls('lamp', fresh ? 'online' : '');
    txt('clock', new Date().toLocaleTimeString('en-GB'));
    const lat = (s.latency_ms || {}).last, ws = s.ws || {};
    txt('ck-ping', `PING ${fin(lat) ? fx(lat, 0) + ' ms' : '—'}`);
    txt('ck-ws', `WS ${ws.connected ? 'CONNECTED' : 'DOWN'}${ws.reconnects ? ' · ' + ws.reconnects + ' rc' : ''}`);
    cls('ck-ws', ws.connected ? 'ok' : 'bad');
    const live = fin(s.auth_at) && Date.now() / 1000 - s.auth_at < 6;
    txt('ck-token', `AUTHTOKEN ${live ? 'LIVE (HMAC 5s)' : '×' + (s.authorized ?? 0)}`);
    txt('ck-killsw', s.shutdown ? 'KILL SWITCH FIRED' : s.armed === false ? 'KILL SWITCH —' : 'KILL SWITCH ARMED');
    cls('ck-killsw', s.shutdown ? 'bad' : 'ok');
  }

  function render(s) {
    if (!s) return;
    for (const f of [header, hero, position, regime, carry, fills, risk, analytics]) {
      try { f(s); } catch (err) { console.error('cockpit', f.name, err); }
    }
    trace(s.history);
  }

  document.addEventListener('grok:tick', e => { const d = e.detail || {}; if (d.state) render(d.state); });
  const streamFresh = () => Date.now() - (window.__grokLastAt || 0) < 3000;
  async function poll() {
    if (!document.hidden && !streamFresh()) {
      try {
        const r = await fetch('/api/state', { cache: 'no-store', signal: AbortSignal.timeout(3500) });
        if (r.ok) render(await r.json());
      } catch { txt('connection', 'DISCONNECTED'); cls('lamp', ''); }
    }
    setTimeout(poll, 1000);
  }
  poll();

  /* emergency stop: two clicks within 6 s; loopback POST /api/shutdown (the engine cancels and flattens, then exits) */
  const kill = $('kill');
  if (kill) {
    const label = kill.textContent;
    let armed = false, timer = null;
    kill.addEventListener('click', async () => {
      if (!armed) {
        armed = true; kill.classList.add('armed'); kill.textContent = 'CLICK AGAIN TO STOP';
        timer = setTimeout(() => { armed = false; kill.classList.remove('armed'); kill.textContent = label; }, 6000);
        return;
      }
      clearTimeout(timer); armed = false; kill.classList.remove('armed'); kill.disabled = true; kill.textContent = 'STOPPING…';
      try {
        const r = await fetch('/api/shutdown', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirm: 'SHUTDOWN' }) });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        txt('status', 'SHUTDOWN REQUESTED · cancelling orders and flattening');
      } catch (err) {
        txt('status', 'Shutdown request failed · ' + err.message);
        kill.disabled = false; kill.textContent = label;
      }
    });
  }
})();
