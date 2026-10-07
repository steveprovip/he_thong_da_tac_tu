'use strict';
/* Trade desk: 2-mode switch, FORCE TEST, close, Binance-style position / orders / history,
   trigger strip, neon win/loss flashes and WebAudio cues. Data = `grok:tick` from terminal.js. */
(() => {
  const $ = id => document.getElementById(id);
  if (!$('btn-force')) return;
  const fin = v => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v));
  const fx = (v, d = 2) => fin(v) ? Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }) : '—';
  const pdp = v => !fin(v) ? 2 : Math.abs(v) < 1 ? 5 : Math.abs(v) < 100 ? 4 : 1;
  const sg = (v, d = 4) => fin(v) ? (v >= 0 ? '+' : '') + Number(v).toFixed(d) : '—';
  const hms = t => fin(t) && t > 0 ? new Date(t * 1000).toLocaleTimeString('en-GB') : '—';
  const txt = (id, v) => { const n = $(id); if (n && n.textContent !== v) n.textContent = v; };
  let S = {}, lastHist = null, sound = false, ctx = null, closeArmed = 0;
  try { sound = localStorage.getItem('grok.sound') === '1'; } catch { /* storage blocked */ }

  /* ---------------- sound: synthesized, no assets, starts only after a user click ---------------- */
  function tone(seq) {
    if (!sound) return;
    try {
      ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
      let t = ctx.currentTime;
      for (const [f, d, type = 'sine', g = 0.08] of seq) {
        const o = ctx.createOscillator(), a = ctx.createGain();
        o.type = type; o.frequency.value = f; a.gain.setValueAtTime(g, t); a.gain.exponentialRampToValueAtTime(0.0001, t + d);
        o.connect(a).connect(ctx.destination); o.start(t); o.stop(t + d); t += d * 0.9;
      }
    } catch { /* audio unavailable */ }
  }
  const SFX = { win: [[660, .09], [880, .09], [1320, .18]], loss: [[220, .22, 'sawtooth', .05], [165, .3, 'sawtooth', .05]],
    fill: [[990, .06, 'square', .04]], err: [[180, .15, 'square', .04]] };
  function paintSound() { const b = $('btn-sound'); b.textContent = `♪ ÂM THANH: ${sound ? 'BẬT' : 'TẮT'}`; b.setAttribute('aria-pressed', String(sound)); b.classList.toggle('on', sound); }
  $('btn-sound').addEventListener('click', () => { sound = !sound; try { localStorage.setItem('grok.sound', sound ? '1' : '0'); } catch { /* */ } paintSound(); tone(SFX.fill); });
  paintSound();

  function flash(kind) {
    document.body.classList.remove('flash-win', 'flash-loss'); void document.body.offsetWidth;
    document.body.classList.add(kind === 'win' ? 'flash-win' : 'flash-loss');
    setTimeout(() => document.body.classList.remove('flash-win', 'flash-loss'), 1800);
  }
  function msg(text, kind = '') { const n = $('ctl-msg'); n.textContent = text; n.className = 'ctl-msg ' + kind; }

  async function post(path, body) {
    try {
      const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        signal: AbortSignal.timeout(60000) });
      const j = await r.json().catch(() => ({}));
      return Object.assign({ ok: r.ok }, j);
    } catch (e) { return { ok: false, error: 'mất kết nối engine' }; }
  }

  /* ---------------- controls ---------------- */
  async function forceTest(b) {
    const side = b.dataset.side, at = side === 'BUY' ? 'best bid' : 'best ask';
    b.disabled = true; msg(`Đặt Post-Only GTX ${side === 'BUY' ? 'LONG' : 'SHORT'} tại ${at} (tối đa 3 s, không đuổi giá)…`);
    const r = await post('/api/force_test_trade', { side });
    b.disabled = false;
    if (r.ok) { msg(`MAKER FILL ${r.side} ${r.qty} @ ${fx(r.entry, pdp(r.entry))} (GTX) · ${fx(r.latency_ms, 0)} ms · TP1 ${fx(r.tp, pdp(r.tp))} / SL ${fx(r.sl, pdp(r.sl))} · micro-BE +0.08% · flow-exit $500k/15s`, 'ok'); tone(SFX.fill); }
    else { msg('Lỗi: ' + (r.error || 'không rõ'), 'err'); tone(SFX.err); }
  }
  $('btn-force').addEventListener('click', () => forceTest($('btn-force')));
  $('btn-force-short').addEventListener('click', () => forceTest($('btn-force-short')));
  $('btn-close').addEventListener('click', async () => {
    const b = $('btn-close');
    if (Date.now() - closeArmed > 4000) { closeArmed = Date.now(); b.textContent = 'NHẤP LẦN NỮA ĐỂ ĐÓNG'; b.classList.add('armed');
      setTimeout(() => { b.textContent = '✕ ĐÓNG VỊ THẾ'; b.classList.remove('armed'); }, 4000); return; }
    closeArmed = 0; b.textContent = '✕ ĐÓNG VỊ THẾ'; b.classList.remove('armed');
    const r = await post('/api/close_position', {});
    msg(r.ok ? 'Đã gửi lệnh đóng reduce-only' : 'Lỗi: ' + r.error, r.ok ? 'ok' : 'err');
  });
  async function switchTo(mode, confirm = '') {
    msg(`Đang chuyển sang ${mode.toUpperCase()}…`);
    const r = await post('/api/switch_mode', { mode, confirm });
    msg(r.ok ? `Đã chuyển: ${mode.toUpperCase()}` : 'Không chuyển được: ' + r.error, r.ok ? 'ok' : 'err');
    if (!r.ok) tone(SFX.err);
  }
  $('btn-demo').addEventListener('click', () => { if (S.engine_mode !== 'demo') switchTo('demo'); });
  $('btn-live').addEventListener('click', () => {
    if (S.engine_mode === 'live') return;
    if (!S.live_ready) { msg('LIVE khoá: ' + (S.live_reason || 'engine chưa chạy'), 'err'); tone(SFX.err); return; }
    $('live-form').hidden = false; $('live-confirm').value = ''; $('live-confirm').focus();
  });
  $('live-cancel').addEventListener('click', () => { $('live-form').hidden = true; });
  $('live-form').addEventListener('submit', e => {
    e.preventDefault(); const v = $('live-confirm').value.trim(); $('live-form').hidden = true;
    if (v !== 'LIVE') { msg('Huỷ: phải gõ đúng LIVE', 'err'); return; }
    switchTo('live', v);
  });
  document.querySelectorAll('.sym-switch .sym').forEach(b => b.addEventListener('click', async () => {
    if (b.classList.contains('on')) return;
    msg(`Đang chuyển sang ${b.dataset.symbol}…`);
    const r = await post('/api/switch_symbol', { symbol: b.dataset.symbol });
    msg(r.ok ? `Đã chuyển coin: ${r.symbol}` : 'Không đổi được coin: ' + r.error, r.ok ? 'ok' : 'err');
    if (!r.ok) tone(SFX.err);
  }));
  $('scan-radar').addEventListener('click', async ev => {
    const b = ev.target.closest('.radar-chip');
    if (!b || b.classList.contains('focus')) return;
    const r = await post('/api/switch_symbol', { symbol: b.dataset.symbol });
    msg(r.ok ? `Đang xem ${r.symbol} (scanner vẫn quét cả 3 cặp)` : 'Không đổi được: ' + r.error, r.ok ? 'ok' : 'err');
  });
  document.querySelectorAll('.desk-tabs .tab').forEach(btn => btn.addEventListener('click', () => {
    document.querySelectorAll('.desk-tabs .tab').forEach(b => { b.classList.toggle('on', b === btn); b.setAttribute('aria-selected', String(b === btn)); });
    $('tab-orders').hidden = btn.dataset.tab !== 'orders'; $('tab-history').hidden = btn.dataset.tab !== 'history';
  }));

  /* ---------------- render ---------------- */
  function rows(tbody, list, cols, empty, rowClass) {
    const tb = $(tbody); tb.replaceChildren();
    if (!list.length) { const tr = document.createElement('tr'); const td = document.createElement('td'); td.colSpan = cols; td.className = 'empty'; td.textContent = empty; tr.append(td); tb.append(tr); return; }
    for (const item of list) {
      const tr = document.createElement('tr'); if (rowClass) tr.className = rowClass(item) || '';
      for (const [v, cls] of item.cells) { const td = document.createElement('td'); td.textContent = v; if (cls) td.className = cls; tr.append(td); }
      tb.append(tr);
    }
  }

  function render(s) {
    if (!s || !s.engine_mode) return;
    S = s;
    const mode = s.engine_mode, prof = s.profile || {};
    $('btn-demo').classList.toggle('active', mode === 'demo'); $('btn-demo').setAttribute('aria-pressed', String(mode === 'demo'));
    $('btn-live').classList.toggle('active', mode === 'live'); $('btn-live').setAttribute('aria-pressed', String(mode === 'live'));
    document.body.dataset.mode = mode;
    txt('demo-sub', s.demo_keys ? 'demo-fapi · key OK' : 'MOCK · thiếu key demo');
    txt('live-sub', s.live_ready ? 'đã ARM · gõ LIVE' : 'KHOÁ · run.bat live');
    $('btn-live').classList.toggle('locked', !s.live_ready);
    const pos = s.position;
    const reg = s.regime || {}, allow = reg.allow || ['BUY', 'SELL'];
    for (const [id, side] of [['btn-force', 'BUY'], ['btn-force-short', 'SELL']]) {
      const b = $(id), banned = !allow.includes(side);
      b.disabled = mode !== 'demo' || !!pos || s.phase === 'EXECUTING' || banned;
      b.classList.toggle('banned', banned);
      b.title = mode !== 'demo' ? 'Chỉ có ở chế độ DEMO' : pos ? 'Đang có vị thế'
        : banned ? `CẤM ${side}: ${reg.label} (${reg.why})`
        : `Post-Only GTX ${side} tại ${side === 'BUY' ? 'best bid' : 'best ask'} · micro-BE +0.08% · BE +0.12% · TP1 50% +0.35% · trail > +0.50% · flow-exit`;
    }
    document.querySelectorAll('.sym-switch .sym').forEach(b => {
      const on = b.dataset.symbol === s.symbol; b.classList.toggle('on', on); b.setAttribute('aria-pressed', String(on));
      b.disabled = !!pos;
    });
    const fm = s.fee_model;
    txt('fee-note', fm && fm.ok ? `${fm.qty} ≈ $${fx(fm.notional, 2)} · phí maker 2 đầu ${fx(fm.maker_rt_usdt, 4)} U · hoà phí khi giá đi ≥ ${fx(fm.fee_breakeven_pct, 2)}%`
      : fm ? 'OKAPI: ' + fm.why : 'phí —');
    const rb = $('ctl-regime'), rn = String(reg.name || 'NEUTRAL').toLowerCase();
    rb.className = 'regime-badge ' + rn;
    rb.querySelector('b').textContent = reg.name === 'DOWN' ? '🔴 ' + reg.label : reg.name === 'UP' ? '🟢 ' + reg.label
      : reg.name ? '⚪ ' + reg.label : 'REGIME —';
    const ch = s.chop || {};
    const kerTxt = fin(ch.ker) ? `KER ${fx(ch.ker, 2)} (${ch.locked ? 'CHOPPY - LOCKED' : 'TRENDING'})` : `KER — (${ch.locked ? 'WARMING - LOCKED' : '—'})`;
    rb.querySelector('small').textContent = `${reg.why || 'CVD 60s —'} · ${kerTxt}`;
    const hk = $('hdr-ker'); if (hk) { hk.textContent = kerTxt; hk.className = 'hero-ker ' + (ch.locked ? 'chop' : 'trend'); hk.title = ch.reason || `khoá < ${ch.lock} · mở ≥ ${ch.unlock}`; }
    document.body.dataset.regime = rn;
    const hr = $('hdr-regime'); if (hr) { hr.textContent = rb.querySelector('b').textContent; hr.className = 'hero-regime ' + rn; }
    $('btn-close').disabled = !pos;
    txt('ctl-venue', `${s.venue} · ${s.symbol} · ${prof.margin_type === 'CROSSED' ? 'Cross' : 'Isolated'} ${prof.leverage}x · ${String(prof.entry || '').toUpperCase()}`);
    txt('ctl-phase', `${s.phase} · ${s.ws && s.ws.connected ? 'WS LIVE' : 'WS DOWN'}`);
    $('ctl-phase').className = 'chip ' + (s.phase === 'POSITION' ? 'green' : s.phase === 'EXECUTING' ? 'cyan blink' : (s.ws && s.ws.connected) ? '' : 'red');
    const lat = s.latency_ms || {};
    txt('ctl-lat', `LATENCY ${fin(lat.last) ? fx(lat.last, 0) + ' ms' : '—'} · p50 ${fin(lat.p50) ? fx(lat.p50, 0) : '—'}`);

    /* triggers */
    const f = s.features || {}, sig = s.signals || {};
    const trig = (k, val) => {
      const n = $('trig-' + k), x = sig[k];
      n.className = 'trig' + (x ? ' fire ' + x.side.toLowerCase() : '');
      n.querySelector('span').textContent = x ? `${x.side} ${(x.strength * 100).toFixed(0)}% · ${x.why}` : val;
    };
    trig('FLOW', `OBI ${sg(f.obi, 2)} · vel ${fin(f.cvd_vel_10s) ? sg(f.cvd_vel_10s / 1e3, 0) + 'k/s' : '—'} · L10 ${sg(f.depth10_imb, 2)}`);
    if (sig.SURGE && !sig.FLOW) { const n = $('trig-FLOW'); n.className = 'trig fire ' + sig.SURGE.side.toLowerCase(); n.querySelector('span').textContent = `${sig.SURGE.side} SURGE · ${sig.SURGE.why}`; }
    if (sig.TREND) { const n = $('trig-VWAP'); n.className = 'trig fire ' + sig.TREND.side.toLowerCase();
      n.querySelector('span').textContent = `${sig.TREND.side} ${(sig.TREND.strength * 100).toFixed(0)}% · ${sig.TREND.why}`; }
    else trig('VWAP', `lệch ${fin(f.vwap_dev_pct) ? sg(f.vwap_dev_pct, 3) + '%' : '—'} · ${(s.regime || {}).mr_ok ? 'MR mở' : 'MR khoá (dòng tiền)'}`);
    const banned = sig.BANNED || [];
    if (banned.length) { const n = $('trig-DEC'); n.title = banned.map(b => b.why).join('\n'); }
    trig('LIQ', `box ${fin(f.box_pct) ? fx(f.box_pct, 3) + '%' : '—'} · L $${fx(f.liq_long_usd, 0)} · S $${fx(f.liq_short_usd, 0)}`);
    if (sig.SQUEEZE) { const n = $('trig-LIQ'); n.className = 'trig fire ' + sig.SQUEEZE.side.toLowerCase(); n.querySelector('span').textContent = sig.SQUEEZE.why; }
    const d = s.decision || {};
    const dn = $('trig-DEC'); dn.className = 'trig decision' + (d.allowed ? ' fire ' + String(d.side).toLowerCase() : '');
    dn.querySelector('span').textContent = pos ? 'GIỮ VỊ THẾ · swarm chờ MARIN đóng lệnh'
      : `${fx((d.score || 0) * 100, 1)}% · JEV ${fx(d.jev, 1)} · hội tụ ${(d.confluence || {}).count ?? 0}/3 · ${d.reason || '—'}`;

    /* position */
    txt('pos-count', pos ? '1' : '0');
    rows('pos-rows', pos ? [{ cells: [
      [`${pos.symbol} ${pos.margin_type} ${pos.leverage}x`, pos.side === 'LONG' ? 'buy' : 'sell'],
      [`${pos.side === 'LONG' ? '' : '-'}${pos.size}`, pos.side === 'LONG' ? 'buy' : 'sell'],
      [fx(pos.entry, pdp(pos.entry))], [fx(pos.mark, pdp(pos.mark))], [fin(pos.liq) && pos.liq > 0 ? fx(pos.liq, pdp(pos.liq)) : '—', 'warn'],
      [fx(pos.margin, 2) + ' U'], [`${sg(pos.upnl, 4)} (${sg(pos.roe_pct, 2)}%)`, pos.upnl >= 0 ? 'buy glow' : 'sell glow'],
      [`${fin(pos.tp) ? fx(pos.tp, pdp(pos.tp)) : '—'}${pos.tp1_filled ? ' ✓' : ''} / SL sàn ${fin(pos.sl) ? fx(pos.sl, pdp(pos.sl)) : '—'}`,
        pos.be_locked ? 'buy' : ''],
      [pos.exiting ? `MAKER EXIT ${pos.exiting.reason} @ ${fx(pos.exiting.px, pdp(pos.exiting.px))} · re-peg x${pos.exiting.reprices}`
        : pos.trail_on ? `TRAILING (soft ${fx(pos.soft_stop, pdp(pos.soft_stop))}) · MFE ${sg(pos.mfe_pct, 2)}%`
        : pos.be_locked ? `BE ${pos.soft_exits ? 'soft ' + fx(pos.soft_stop, pdp(pos.soft_stop)) : 'LOCKED'} · MFE ${sg(pos.mfe_pct, 2)}%`
        : pos.mbe_locked ? `MICRO-BE · MFE ${sg(pos.mfe_pct, 2)}% · flow ngược $${fx((pos.adverse_usd || 0) / 1e3, 0)}k/${fx((pos.flow_exit_usd || 0) / 1e3, 0)}k`
        : pos.tag === 'EXTERNAL' ? 'EXTERNAL' : `micro-BE khi +${fx(pos.microbe_trigger_pct, 2)}% · MFE ${sg(pos.mfe_pct, 2)}% · flow ngược $${fx((pos.adverse_usd || 0) / 1e3, 0)}k/${fx((pos.flow_exit_usd || 0) / 1e3, 0)}k · ${fx(pos.age_s, 0)}s${pos.breath_left_s > 0 ? ` · THỞ ${fx(pos.breath_left_s, 0)}s` : ''}`,
        pos.trail_on ? 'buy glow' : pos.be_locked ? 'buy' : 'warn']] }] : [], 9, 'Không có vị thế');
    const st = s.stats || {};
    const wr = $('winrate');
    wr.textContent = st.n ? `WIN RATE ${fx(st.win_rate * 100, 1)}% · ${st.wins}W / ${st.losses}L · Avg Win ${sg(st.avg_win, 3)}U · Avg Loss ${sg(st.avg_loss, 3)}U · PF ${st.profit_factor == null ? (st.no_losses ? '∞' : '—') : fx(st.profit_factor, 2)} · E ${sg(st.expectancy, 4)}U/lệnh` : 'WIN RATE — (chưa có lệnh)';
    wr.className = 'winrate ' + ((st.net || 0) >= 0 ? 'up' : 'down');
    const gv = s.governor;
    if (gv) wr.title = `CHURN GOVERNOR · ${gv.trades_hour}/${gv.max_trades_hour} lệnh/h · nghỉ ${fx(gv.cooldown_s, 0)}s sau đóng · vào khi ≥${fx(gv.min_score * 100, 0)}% & edge ≥${fx(gv.min_edge_bps, 0)}bps · flow exit $${fx(gv.flow_exit_usd / 1e6, 1)}M sau ${fx(gv.flow_breath_s, 0)}s · regime-flip ±$${fx(gv.regime_flip_usd / 1e6, 1)}M`;
    txt('gov', gv ? `GOV ${gv.trades_hour}/${gv.max_trades_hour}/h · ≥${fx(gv.min_score * 100, 0)}% · edge ≥${fx(gv.min_edge_bps, 0)}bps` : 'GOV —');
    const ab = s.absorption || {};
    const abn = $('absorb-badge');
    if (abn) {
      abn.textContent = ab.state === 'TRAPPED' ? `ABSORPTION: TRAPPED (cấm ${ab.blocks})` : `ABSORPTION: ${ab.state || '—'}`;
      abn.className = 'chip ' + (ab.state === 'TRAPPED' ? 'red' : ab.state === 'CLEAR' ? 'green' : '');
      abn.title = ab.why || '';
    }
    const mc = s.macro || {};
    const mcn = $('macro-chip');
    if (mcn) {
      mcn.textContent = `MACRO 5m: ${mc.name || '—'}`;
      mcn.className = 'chip ' + (mc.name === 'LONG ONLY' ? 'green' : mc.name === 'SHORT ONLY' ? 'red' : '');
      mcn.title = mc.why || '';
    }
    const hun = $('hurdle-chip');
    if (hun) {
      const g = pos ? pos.gross_pct : null, h = pos ? pos.hurdle_pct : 0.08;
      hun.textContent = !pos || !fin(g) ? 'HURDLE: —'
        : !pos.fee_hurdle ? 'HURDLE: OFF'
        : String(pos.hurdle || '').startsWith('HOLD') ? `HOLDING: MIN ${pos.hurdle.slice(5)}`
        : g >= h ? 'PROFIT: UNLOCKED'
        : g <= -h ? `EXIT: ARMED (${sg(g, 3)}% ≤ -${fx(h, 2)}%)`
        : `HOLDING: FEE HURDLE (${sg(g, 3)}% < +${fx(h, 2)}%)`;
      hun.className = 'chip ' + (pos && fin(g) ? (g >= h ? 'green' : g <= -h ? 'red' : '') : '');
      hun.title = pos ? `Flow/Regime exit bị giữ ${pos.hurdle_blocks || 0} lần · SL cứng vẫn chạy` : '';
    }
    const sc = s.scanner || {}, rad = $('scan-radar');
    if (rad) {
      const esc = v => String(v).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
      const cls = l => /OPEN|ORDER/.test(l) ? 'open' : /^TREND (BUY|SELL)/.test(l) ? 'trend' : /^MR (BUY|SELL)/.test(l) ? 'mr'
        : /CHOP/.test(l) ? 'chop' : 'idle';
      const html = (sc.rows || []).map(r => `<button type="button" class="radar-chip ${cls(r.label)}${r.symbol === sc.focus ? ' focus' : ''}"`
        + ` data-symbol="${esc(r.symbol)}" title="${esc(`${r.regime || '—'} · KER ${fin(r.ker) ? fx(r.ker, 2) : '—'} · ${r.reason || ''}`)}">`
        + `${esc(r.symbol.replace('USDT', ''))}: ${esc(r.label)}</button>`).join('<i>|</i>');
      const lead = sc.on ? '<b>SCANNER</b>' : '<b>1 CẶP</b>';
      if (rad.dataset.html !== html) { rad.innerHTML = lead + (html || ' —'); rad.dataset.html = html; }
    }
    const fl = s.flow || {};
    const fln = $('flow-chip');
    if (fln) {
      const t = fl.drift && fl.drift_lock ? 'FLOW: DRIFT LOCK (60s/3m CONFLICT)'
        : fl.state === 'BUY' ? 'FLOW: CONCORDANT BUY' : fl.state === 'SELL' ? 'FLOW: CONCORDANT SELL'
        : fl.state === 'CONFLICT' ? 'FLOW: CONFLICT (LOCKED)' : 'FLOW: — (chờ OFI)';
      if (fln.textContent !== t) fln.textContent = t;
      const ok = (fl.state === 'BUY' || fl.state === 'SELL') && !(fl.drift && fl.drift_lock);
      const bad = fl.state === 'CONFLICT' || (fl.drift && fl.drift_lock);
      fln.className = 'chip' + (ok ? ' green' : bad ? ' red' : '');
      fln.title = `${fl.why || ''} · BUY cần CVD15>0, CVD60≥+300k, OFI≥+0.35 (SELL ngược lại)`;
    }
    const qv = s.quant || {}, br = qv.brackets || {}, kl = qv.kelly || {};
    const chip = (id, text, cls) => { const n = $(id); if (!n) return; if (n.textContent !== text) n.textContent = text; n.className = 'chip ' + (cls || ''); };
    chip('ofi-chip', fin(qv.ofi) ? `OFI L10 ${sg(qv.ofi, 2)}` : `OFI — (${qv.ofi_n || 0} ev)`,
      fin(qv.ofi) && Math.abs(qv.ofi) >= 0.65 ? (qv.ofi > 0 ? 'green' : 'red') : '');
    chip('vpin-chip', fin(qv.vpin) ? `VPIN ${fx(qv.vpin, 2)} · ${qv.tox}${qv.toxic_left > 0 ? ` ${fx(qv.toxic_left, 0)}s` : ''}` : 'VPIN — (warmup)',
      qv.tox === 'TOXIC' || qv.toxic_left > 0 ? 'red' : qv.tox === 'CLEAN' ? 'green' : '');
    chip('rr-chip', fin(br.sl_pct) ? `${br.src} SL ${fx(br.sl_pct, 2)}% · TP1 ${fx(br.tp_pct, 2)}% · RR ${fx(br.rr_gross, 1)} gộp / ${fin(br.rr_net) ? fx(br.rr_net, 2) : '—'} sau phí · hoà vốn ≥ ${fin(br.be_wr) ? fx(br.be_wr * 100, 0) : '—'}% thắng` : 'RR —',
      br.src === 'ATR' ? 'green' : '');
    chip('kelly-chip', kl.state === 'SAMPLE' ? `KELLY ¼: ${kl.n || 0}/${kl.min_n || 20} lệnh mẫu`
      : kl.state === 'EDGE' ? `KELLY ¼: ${fx(kl.f * 100, 1)}% vốn · f* ${sg(kl.f_star, 2)}`
      : `KELLY ¼: f* ${sg(kl.f_star, 2)} → khối lượng tối thiểu`, kl.state === 'EDGE' ? 'green' : kl.state === 'NO EDGE' ? 'red' : '');
    const shn = $('shield-chip');
    if (shn) {
      shn.textContent = !pos ? 'SHIELD: —' : pos.plock ? `SHIELD: PROFIT LOCK (+${fx(pos.lock_trigger_pct, 2)}%)`
        : pos.shield ? `SHIELD: ARMED (+${fx(pos.fee_shield_trigger_pct, 2)}%)` : `SHIELD: chờ MFE +${fx(pos.fee_shield_trigger_pct, 2)}%`;
      shn.className = 'chip ' + (pos && (pos.shield || pos.plock) ? 'green' : '');
    }
    const ss = s.session || {};
    const split = $('pnl-split');
    split.textContent = `GROSS ${sg(ss.gross, 4)} · PHÍ ${sg(-(ss.fees || 0), 4)} · NET ${sg(ss.realized, 4)} U`;
    split.className = 'pnl-split ' + ((ss.realized || 0) >= 0 ? 'up' : 'down');

    const ords = s.open_orders || [];
    txt('ord-count', String(ords.length));
    rows('ord-rows', ords.map(o => ({ cells: [[o.type], [o.side, o.side === 'BUY' ? 'buy' : 'sell'], [fx(o.price, pdp(o.price))],
      [o.qty ? String(o.qty) : 'Close position'], [o.reduce ? 'Yes' : 'No'], [String(o.id || '').slice(0, 14)]] })), 6, 'Không có lệnh chờ');

    const hist = s.trade_history || [];
    const head = hist[0] ? `${hist[0].t}|${hist[0].pnl}` : null, fresh = lastHist !== null && head && head !== lastHist;
    txt('hist-count', String(hist.length));
    rows('hist-rows', hist.map(h => ({ cells: [[hms(h.t)], [h.symbol], [h.side, h.side === 'BUY' ? 'buy' : 'sell'], [String(h.qty)],
      [fx(h.entry, pdp(h.entry))], [fx(h.exit, pdp(h.exit))], [sg(h.gross ?? h.pnl, 4), (h.gross ?? h.pnl) >= 0 ? 'buy' : 'sell'],
      [fin(h.fee) ? sg(-h.fee, 4) : '—', 'warn'], [sg(h.pnl, 4), h.pnl >= 0 ? 'buy glow' : 'sell glow'], [sg(h.pnl_pct, 2) + '%'],
      [h.reason + (h.exit_mode === 'maker' ? ' · maker' : ''), /TP|TRAIL|BE/.test(h.reason) ? 'buy' : h.reason === 'SL' ? 'sell' : /FLOW/.test(h.reason) ? 'warn' : ''],
      [fin(h.maker_fills) ? `${h.maker_fills}M / ${h.taker_fills}T · vào ${h.entry_type || 'GTX'}` : (h.entry_type || '—'),
        h.entry_type === 'IOC' ? 'warn' : ''], [`${h.tag}/${h.path}`]] })),
      13, 'Chưa có lệnh khớp', h => fresh && h === hist[0] ? 'new ' + (h.pnl >= 0 ? 'win' : 'loss') : '');
    if (fresh) {
      const win = hist[0].pnl >= 0; flash(win ? 'win' : 'loss'); tone(win ? SFX.win : SFX.loss);
      msg(`${win ? 'CHỐT LỜI' : 'CẮT LỖ BẢO TOÀN VỐN'} ${hist[0].reason} · gross ${sg(hist[0].gross ?? hist[0].pnl, 4)} · phí ${sg(-(hist[0].fee || 0), 4)} · net ${sg(hist[0].pnl, 4)} U`, win ? 'ok' : 'err');
    }
    lastHist = head;
  }
  document.addEventListener('grok:tick', e => render((e.detail || {}).state));
})();
