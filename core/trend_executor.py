"""4H trend lane on the DEMO venue + the micro swarm as its execution timer - 10-09 (dd).

Profile.trend_lane == 'active' (demo only; a live venue or a non-demo profile is refused in code):
  * every `trend_active_every_s` the Runner fetches closed 4H bars + funding (public REST) and computes the trend
    lane target (core/trend_lane: TSMOM 30/120/360 x 4h + Donchian 120, vol target 20 %) per symbol;
  * only the SIGN acts (enter / exit / flip) on a NEW closed bar - size is fixed at entry, as in the event-based
    backtest (backtest_brackets with the disaster stop), so no fee-burning re-sizing;
  * size = |weight| x wallet, clipped to [trend_notional_min, trend_notional_max] ($150-$300 on demo);
  * exits: the signal (default - the replay of the requested TP1 1.35 % / SL 1.5 ATR / chandelier brackets turned
    OOS Sharpe +0.63 into -0.48), plus an exchange-side DISASTER stop at trend_disaster_atr x ATR(14, 4h)
    (8 ATR ~ 9-18 %: OOS Sharpe +0.59, it exists for engine downtime, not for P&L).  Optional brackets
    (trend_tp1_pct, trend_sl_atr, trend_trail) are implemented but off by default;
  * execution policy trend_exec: 'taker' | 'maker' (post-only at the venue touch, re-peg 30 s) | 'swarm'
    (maker only while the micro flow pulls back against us, take when strong flow leaves with us) - all fall
    back to an IOC at the window end (trend_exec_window_s), so a signal is never silently skipped.
10-09 (ee) regime breakthrough (tools/trend_regime_replay.py):
  * entry gate trend_regime / trend_confirm (KER10 + ADX14 regime, volume > 1.2 x SMA20 + bar CVD with the side);
    a blocked entry waits for a later bar of the same signal (Cash / Observer meanwhile); trend_chop_exit goes
    flat on a CHOP close.  Exits by signal are never gated;
  * capital_allocation_pct: notional = min(alloc, risk_pct / (disaster ATR stop distance)) x wallet - low ATR hits
    the 10 % ceiling, high ATR shrinks it so the loss at the disaster stop stays <= trend_risk_pct of the wallet;
  * soft profit lock trend_lock_pct: armed at a best close >= +2 %, then a close beyond best -/+ 3.5 ATR (or the
    Donchian mid) exits at once (the exchange disaster stop stays where it is).
State: data/trend_active.json (positions, cids, last bar); trades: data/trend_trades.jsonl (never the micro
ledger).  The micro engine of a symbol the trend lane holds does not adopt that position (Engine.trend_owned).
"""
from __future__ import annotations

import asyncio
import json
import math
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Optional

from core import trend_lane as tl

ACTIVE_BAR = '4h'


def _fmt(x: float, step: float) -> str:
    n = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return f'{math.floor(x / step + 1e-9) * step:.{n}f}'


class TrendExecutor:
    def __init__(self, engines: dict, state_path: Path, trades_path: Path, clock=time.time, sleep=asyncio.sleep):
        self.engines = engines
        self.state_path, self.trades_path = Path(state_path), Path(trades_path)
        self.clock, self.sleep = clock, sleep
        self.state = self._load()
        self.last = {}
        self.events = []

    # ------------------------------------------------------------------ persistence
    def _load(self) -> dict:
        try:
            d = json.loads(self.state_path.read_text(encoding='utf-8'))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.state, default=float), encoding='utf-8')
            tmp.replace(self.state_path)
        except OSError:
            pass

    def _log_trade(self, row: dict) -> None:
        try:
            self.trades_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.trades_path, 'a', encoding='utf-8') as fh:
                fh.write(json.dumps(row, default=float) + '\n')
        except OSError:
            pass

    def say(self, e, msg: str) -> None:
        self.events.append({'t': self.clock(), 'message': msg})
        self.events = self.events[-50:]
        e.event(f'TREND 4H · {msg}')

    # ------------------------------------------------------------------ guards
    @staticmethod
    def allowed(e) -> Optional[str]:
        if e.p.mode != 'demo':
            return 'trend lane active is demo-only'
        if type(e.venue).__name__ == 'LiveVenue' or 'MAINNET' in str(getattr(e.venue, 'label', '')):
            return 'trend lane refuses a mainnet venue'
        return None

    # ------------------------------------------------------------------ signal
    @staticmethod
    def gate_of(p) -> Optional[tl.RegimeGate]:
        if p.trend_regime == 'off' and not p.trend_confirm and not p.trend_chop_exit:
            return None
        return tl.RegimeGate(mode=p.trend_regime, confirm=p.trend_confirm, chop_exit=p.trend_chop_exit)

    def targets(self, data: dict, now: float) -> dict:
        p = next(iter(self.engines.values())).p
        cfg = tl.TrendConfig(bar=ACTIVE_BAR)
        hours = tl.BAR_HOURS[ACTIVE_BAR]
        closed = {s: tl.closed_only(b, now, hours) for s, b in data['bars'].items()}
        closed = {s: b for s, b in closed.items() if len(b['c']) > 400}
        if not closed:
            return {}
        tw, comp = tl.weights_series(closed, cfg, data.get('funding'))
        g = self.gate_of(p) or tl.RegimeGate(mode='off', confirm=False)
        dn = 20 * (24 // tl.BAR_HOURS[ACTIVE_BAR])
        out = {}
        for s, b in closed.items():
            atr = tl.atr_series(b['h'], b['l'], b['c'])[-1]
            info = tl.regime_info(b, g)
            side = tl.sign(tw[s][-1])
            ok, why = tl.gate_entry(info, len(b['c']) - 1, side, self.gate_of(p)) if side else (False, 'flat signal')
            out[s] = {'w': tw[s][-1], 'side': side, 'bar_t': b['t'][-1], 'close': b['c'][-1],
                      'atr': atr, 'comp': comp[s][-1], 'regime': info['regime'][-1], 'ker': info['ker'][-1],
                      'adx': info['adx'][-1], 'vol_ratio': info['vol_ratio'][-1], 'cvd': info['cvd'][-1],
                      'gate_ok': ok, 'gate_why': why,
                      'dmid': (max(b['h'][-dn:]) + min(b['l'][-dn:])) / 2 if len(b['c']) >= dn else None}
        return out

    def size(self, e, w: float, price: float, atr: Optional[float] = None) -> float:
        wallet = float((e.account or {}).get('wallet') or e.p.mock_balance or 0.0)
        if e.p.capital_allocation_pct:                # 10-09 (ee): 10 % ceiling, <= 1 % wallet lost at the disaster stop
            frac = tl.Sizing(e.p.capital_allocation_pct, e.p.trend_risk_pct, e.p.trend_disaster_atr).fraction(atr, price)
            notional = min(frac * wallet, e.p.trend_notional_max)
        else:
            notional = min(max(abs(w) * wallet, e.p.trend_notional_min), e.p.trend_notional_max)
        step = (e.spec or {}).get('step', 0.001)
        return math.floor(notional / price / step + 1e-9) * step

    # ------------------------------------------------------------------ execution
    async def execute(self, e, side: str, qty: float, reduce: bool = False) -> tuple:
        """Work one order on the venue with the configured policy.  -> (avg_fill, filled_qty, how)."""
        p, sym = e.p, e.p.symbol
        step, tick = e.spec['step'], e.spec['tick']
        policy = p.trend_exec
        t_end = self.clock() + (p.trend_exec_window_s if policy != 'taker' else 0.0)
        filled, notional, how = 0.0, 0.0, policy
        cid, quote, posted = None, None, 0.0
        d = 1 if side == 'BUY' else -1
        while policy != 'taker' and self.clock() < t_end and filled < qty - step / 2:
            await e.refresh_book(0.0)
            bk = e.book()
            bid, ask = bk.get('bid'), bk.get('ask')
            if not bid or not ask:
                await self.sleep(1.0)
                continue
            want_post = True
            if policy == 'swarm':
                f = e.features or {}
                scale = getattr(e.feed.cfg, 'usd_scale', 1.0)
                c15, c60 = f.get('cvd_15s'), f.get('cvd_60s')
                if c60 is not None and d * c60 > 400_000 * scale:          # the move is leaving: take now
                    how = 'swarm: flow with us -> take'
                    break
                toxic = (f.get('vpin') or 0) >= 0.6 and f.get('vpin_side') == ('SELL' if d > 0 else 'BUY')
                want_post = c15 is not None and d * c15 < 0 and not toxic
            px = bid if d > 0 else ask
            if cid and (not want_post or px != quote or self.clock() - posted >= 30.0):
                try:
                    await e.venue.cancel(sym, cid)
                except Exception:
                    pass
                st = await e.venue.order(sym, cid)
                got = float(st.get('filled') or 0.0) if st.get('status') in ('FILLED', 'CANCELED', 'PARTIALLY_FILLED') else 0.0
                if got:
                    filled += got
                    notional += got * float(st.get('avg') or quote)
                cid = None
            if want_post and cid is None and filled < qty - step / 2:
                cid = 'gT-' + uuid.uuid4().hex[:28]
                try:
                    await e.venue.limit_gtx(sym, side, _fmt(qty - filled, step), _fmt(px, tick), cid)
                    quote, posted = px, self.clock()
                except Exception:
                    cid = None
            await self.sleep(1.0)
            if cid:
                st = await e.venue.order(sym, cid)
                if st.get('status') == 'FILLED':
                    got = float(st.get('filled') or 0.0)
                    filled += got
                    notional += got * float(st.get('avg') or quote)
                    cid = None
        if cid:
            try:
                await e.venue.cancel(sym, cid)
            except Exception:
                pass
            st = await e.venue.order(sym, cid)
            got = float(st.get('filled') or 0.0) if st.get('status') in ('FILLED', 'CANCELED', 'PARTIALLY_FILLED') else 0.0
            if got:
                filled += got
                notional += got * float(st.get('avg') or quote)
        rest = qty - filled
        if rest >= step / 2:                                  # window over / taker policy: IOC the remainder
            await e.refresh_book(0.0)
            bk = e.book()
            ref = (bk.get('ask') if d > 0 else bk.get('bid')) or e.feed.price()
            lim = ref * (1 + d * 0.0015)                       # 15 bps protection band
            avg, got = await e.venue.ioc(sym, side, _fmt(rest, step), _fmt(lim, tick), 'gT-' + uuid.uuid4().hex[:28])
            if got:
                filled += got
                notional += got * avg
                how = how + ('+taker' if policy != 'taker' else '')
        return (notional / filled if filled else 0.0), filled, how

    # ------------------------------------------------------------------ position life-cycle
    async def open(self, e, tgt: dict) -> None:
        sym, side = e.p.symbol, 'BUY' if tgt['side'] > 0 else 'SELL'
        px = e.book().get('price') or e.feed.price() or tgt['close']        # size on the venue price, not the bar
        qty = self.size(e, tgt['w'], px, tgt.get('atr'))
        if qty <= 0 or qty * px < (e.spec or {}).get('min_notional', 5.0):
            return
        opened = self.clock()
        avg, got, how = await self.execute(e, side, qty)
        if not got:
            self.say(e, f'{sym} {side} not filled ({how})')
            return
        d = 1 if side == 'BUY' else -1
        stop_px = avg - d * e.p.trend_disaster_atr * tgt['atr'] if tgt.get('atr') else avg * (1 - d * 0.15)
        sl_cid = 'gS-' + uuid.uuid4().hex[:28]
        try:
            await e.venue.stop(sym, 'SELL' if d > 0 else 'BUY', _fmt(stop_px, e.spec['tick']), sl_cid)
        except Exception as exc:
            self.say(e, f'{sym} disaster stop rejected ({type(exc).__name__}) -> closing')
            await self.execute(e, 'SELL' if d > 0 else 'BUY', got, reduce=True)
            return
        self.state[sym] = {'side': side, 'qty': got, 'entry': avg, 'opened': opened, 'opened_ms': int(opened * 1000) - 1000,
                           'bar_t': tgt['bar_t'], 'sl_cid': sl_cid, 'stop': stop_px, 'how': how, 'w': tgt['w'],
                           'best': avg, 'regime': tgt.get('regime')}
        e.trend_owned = True
        self._save()
        self.say(e, f'OPEN {side} {got:g} {sym} @ {avg:g} ({how}) · weight {tgt["w"]:+.3f} · disaster stop '
                    f'{stop_px:.6g} ({e.p.trend_disaster_atr:g} ATR) · {got * avg:.0f} USDT · regime {tgt.get("regime")}')

    async def close(self, e, why: str) -> None:
        sym = e.p.symbol
        st = self.state.get(sym)
        if not st:
            return
        try:
            await e.venue.cancel_stop(st['sl_cid'])
        except Exception:
            pass
        view = await e.venue.position_view(sym)
        amt = abs(float(view.get('amt') or 0.0))
        avg, got = 0.0, 0.0
        how = 'flat already'
        if amt > 0:
            avg, got, how = await self.execute(e, 'SELL' if st['side'] == 'BUY' else 'BUY', amt, reduce=True)
        await self.settle(e, why if amt > 0 else 'STOP', avg, how)

    async def settle(self, e, why: str, exit_px: float, how: str) -> None:
        sym = e.p.symbol
        st = self.state.pop(sym, None)
        if not st:
            return
        parts = {}
        if hasattr(e.venue, 'realized_parts'):
            try:
                parts = await e.venue.realized_parts(sym, st['opened_ms'])
            except Exception:
                parts = {}
        notional = st['qty'] * st['entry']
        row = {'t': self.clock(), 'lane': 'TREND_4H', 'symbol': sym, 'side': st['side'], 'qty': st['qty'],
               'entry': st['entry'], 'exit': exit_px or None, 'reason': why, 'entry_how': st.get('how'),
               'exit_how': how, 'gross': parts.get('gross'), 'fee': parts.get('fee'), 'pnl': parts.get('net'),
               'net_bps': (parts['net'] / notional * 1e4) if parts.get('net') is not None and notional else None,
               'hold_h': (self.clock() - st['opened']) / 3600, 'weight': st.get('w'), 'mode': e.p.mode,
               'regime_entry': st.get('regime'), 'best': st.get('best')}
        self._log_trade(row)
        e.trend_owned = False
        self._save()
        self.say(e, f'CLOSE {sym} {why} ({how}) net {row["pnl"] if row["pnl"] is not None else "?"} U')

    async def step(self, data: dict) -> dict:
        """One pass: act on each symbol whose last CLOSED 4H bar is new to us."""
        now = self.clock()
        tg = self.targets(data, now)
        for sym, e in self.engines.items():
            if sym not in tg or self.allowed(e) or not e.started or not e.spec:
                continue
            t = tg[sym]
            self.last[sym] = {k: t.get(k) for k in ('w', 'side', 'bar_t', 'regime', 'ker', 'adx', 'vol_ratio',
                                                    'gate_ok', 'gate_why')}
            st = self.state.get(sym)
            if st:                                            # the exchange stop may have closed it meanwhile
                view = await e.venue.position_view(sym)
                if not float(view.get('amt') or 0.0):
                    blocked = st['side']
                    await self.settle(e, 'DISASTER-STOP', 0.0, 'exchange stop')
                    self.state[f'_blocked_{sym}'] = blocked     # no re-entry into the stop on the same side
                    st = None
            if self.state.get(f'_seen_{sym}') == t['bar_t']:
                continue
            if e.busy or e.pos:                               # a micro order / position: wait for the next pass
                continue
            self.state[f'_seen_{sym}'] = t['bar_t']
            want = 'BUY' if t['side'] > 0 else ('SELL' if t['side'] < 0 else None)
            if self.state.get(f'_blocked_{sym}') and self.state[f'_blocked_{sym}'] != want:
                self.state.pop(f'_blocked_{sym}', None)       # the signal changed: the stop block is lifted
            e.busy = True                                     # micro lane / adopt stay off while we work
            try:
                if st and st['side'] != want:
                    await self.close(e, 'SIGNAL' if want is None else 'FLIP')
                    st = None
                if st:
                    why = self.exit_check(e.p, st, t)
                    if why:
                        await self.close(e, why)
                        if why == 'PROFIT-LOCK':
                            self.state[f'_blocked_{sym}'] = st['side']      # re-entry only on a new signal sign
                        st = None
                    else:
                        st['bar_t'] = t['bar_t']
                elif want and self.state.get(f'_blocked_{sym}') != want:
                    view = await e.venue.position_view(sym)
                    if float(view.get('amt') or 0.0):
                        self.say(e, f'{sym} venue position not ours - no trend entry')
                    elif not t.get('gate_ok', True):
                        self.say(e, f'{sym} {want} signal held back: {t.get("gate_why")} -> observer')
                    else:
                        await self.open(e, t)
            finally:
                e.busy = False
            self._save()
        return {'targets': {s: {'w': round(v['w'], 4), 'bar_t': v['bar_t']} for s, v in tg.items()},
                'positions': {k: v for k, v in self.state.items() if not k.startswith('_')}}

    @staticmethod
    def exit_check(p, st: dict, t: dict) -> Optional[str]:
        """On a new closed bar while holding: 'CHOP' (chop exit) | 'PROFIT-LOCK' (soft trail) | None."""
        d = 1 if st['side'] == 'BUY' else -1
        close = t['close']
        st['best'] = max(st.get('best') or st['entry'], close) if d > 0 else min(st.get('best') or st['entry'], close)
        if p.trend_chop_exit and t.get('regime') == 'CHOP':
            return 'CHOP'
        if p.trend_lock_pct is None or d * (st['best'] / st['entry'] - 1) * 100 < p.trend_lock_pct:
            return None
        if p.trend_lock_trail == 'donchian_mid':
            lvl = t.get('dmid')
        else:
            lvl = st['best'] - d * p.trend_lock_atr * t['atr'] if t.get('atr') else None
        if lvl is not None and d * (close - lvl) < 0:
            return 'PROFIT-LOCK'
        return None

    def view(self) -> dict:
        return {'mode': 'ACTIVE (demo)', 'targets': self.last,
                'positions': {k: v for k, v in self.state.items() if not k.startswith('_')},
                'log': self.events[-5:]}
