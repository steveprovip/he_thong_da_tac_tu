"""Lane 2: sub-minute volatility-burst scalper next to the 4H trend lane (request 10-10).  Pure maker entries.

Burst (same definition as tools/burst_scalper_replay.py): sigma_1m >= 15 bps OR |TFI 10 s| > 0.85 (taker-flow
imbalance over >= 20 prints, the replay's proxy for OFI L10 - kept identical so shadow rows test the replayed policy;
the real depth OFI L10 is reported next to it) OR 1-min volume > 3x the mean minute of the previous 20 min.  Signal MR (fade the 10 s move, DEV-best) or MOM (follow the 10 s CVD).
Entry: post-only at our touch, re-peg every repeg_s while the burst lasts.  Exits: post-only TP at +/- tp_bps,
stop at -sl_bps (taker), hold_s time stop -> post-only at the touch for exit_maker_s -> taker.
10-16 (RFC-Q4.2, shadow-tested only): burst_time_exit='maker' keeps that post-only exit pegged until it fills, the
SL trips or burst_exit_cap_s passes (TIME-CAP taker); burst_absorb_gate skips an entry on the side the engine's
ABSORPTION TRAP blocks (micro_signals.absorption: |CVD 15 s| >= $500k x scale without a 0.025 % move).  Both change
the policy away from the registered replay (tools/burst_scalper_replay.py CFG) - re-run it before any verdict.
10-16 (b, RFC-Q4.3): burst_chop_gate hibernates entries while the capital router is CHOP or KER 120 s < 0.25;
burst_vpin_veto_s vetoes entries after the engine's VPIN TOXIC mark (either side).  Open positions are unaffected.
10-16 (c, RFC-Q4.4): hibernation has the router's hysteresis - it starts at router CHOP or KER < 0.25 and ends only
when the router is not CHOP AND KER >= 0.40 (unknown KER never wakes it); one OBSERVE line per episode, one WAKE line.

Modes (Profile.burst_lane): 'off' | 'shadow' | 'active'.
  shadow  virtual orders filled by the live prints with the replay's conservative rule (a print must trade THROUGH
          the resting price); rows -> data/micro_trades_shadow.jsonl.  No venue call.
  active  real GTX / IOC orders on the DEMO venue; rows -> data/micro_trades.jsonl.  Refused (falls back to shadow)
          unless the symbol's replay verdict in config/burst_verdict.json is ACTIVE-ELIGIBLE (holdout net bps CI95
          lower bound > 0, written by tools/burst_scalper_replay.py --write-verdict).  Replay 10-10: every variant
          negative on SOL/DOGE USDT and USDC -> SHADOW.
Coexistence with the trend lane (one-way position mode = ONE net position per symbol): no entry while the symbol
has a trend / micro position or order (e.trend_owned / e.pos / e.busy), and none in the trend_quiet_s after each 4H
close, so the trend lane always finds the symbol free.  Budget: open lane notional <= burst_budget_pct x wallet;
daily loss breaker burst_daily_kill_usdt (realised, per lane).
"""
from __future__ import annotations

import json
import math
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Optional

from core import micro_signals as msig
from core.capital_router import CHOP_EXIT_KER, CHOP_KER

VERDICT = Path(__file__).resolve().parents[1] / 'config' / 'burst_verdict.json'
INF = float('inf')


def _fmt(x: float, step: float) -> str:
    n = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return f'{round(x / step) * step:.{n}f}'


class BurstDetector:
    """Per-second buckets from the live prints: sigma_1m, 10 s CVD / move, 1-min volume surge."""

    def __init__(self, sig1m_bps: float = 15.0, ofi: float = 0.85, surge: float = 3.0):
        self.sig1m_bps, self.ofi_lim, self.surge_lim = sig1m_bps, ofi, surge
        self.px = deque(maxlen=80)          # (second, last price)
        self.flow = deque(maxlen=12)        # (second, buy usd, sell usd, prints)
        self.vol = deque(maxlen=1300)       # (second, usd)

    def on_print(self, t: float, px: float, q: float, buyer_maker: bool) -> None:
        s, usd = int(t), px * q
        if self.px and self.px[-1][0] == s:
            self.px[-1] = (s, px)
        else:
            self.px.append((s, px))
        if self.flow and self.flow[-1][0] == s:
            _, b, a, n = self.flow[-1]
            self.flow[-1] = (s, b + (0 if buyer_maker else usd), a + (usd if buyer_maker else 0), n + 1)
        else:
            self.flow.append((s, 0 if buyer_maker else usd, usd if buyer_maker else 0, 1))
        if self.vol and self.vol[-1][0] == s:
            self.vol[-1] = (s, self.vol[-1][1] + usd)
        else:
            self.vol.append((s, usd))

    def state(self, now: float, ofi: Optional[float] = None) -> dict:
        s = int(now) - 1                                   # last COMPLETE second
        last = {k: v for k, v in self.px if k <= s}
        sig = None
        if last and s - min(last) >= 60:
            ref, acc, prev = None, 0.0, None
            for k in range(s - 60, s + 1):
                p = last.get(k, prev)
                if p and prev:
                    acc += (math.log(p / prev) * 1e4) ** 2
                prev = p or prev
            sig = math.sqrt(acc)
        win = [(b, a, n) for k, b, a, n in self.flow if s - 10 < k <= s]
        cvd = sum(b - a for b, a, _ in win)
        gross, prints = sum(b + a for b, a, _ in win), sum(n for _, _, n in win)
        tfi = cvd / gross if gross > 0 and prints >= 20 else None
        p10 = next((p for k, p in reversed(self.px) if k <= s - 10), None)
        p0 = next((p for k, p in reversed(self.px) if k <= s), None)
        move = (p0 / p10 - 1) if (p0 and p10) else None
        v60 = sum(u for k, u in self.vol if s - 60 < k <= s)
        old = [u for k, u in self.vol if s - 1260 < k <= s - 60]
        surge = None
        if self.vol and s - self.vol[0][0] >= 1260 and old:
            base = sum(old) / 20.0
            surge = v60 / base if base > 0 else None
        why = [w for w, ok in (('sigma', sig is not None and sig >= self.sig1m_bps),
                               ('tfi', tfi is not None and abs(tfi) > self.ofi_lim),
                               ('surge', surge is not None and surge > self.surge_lim)) if ok]
        return {'burst': bool(why), 'why': '+'.join(why), 'sig1m': sig, 'tfi': tfi, 'ofi_l10': ofi, 'surge': surge,
                'cvd10': cvd,
                'move10': move}


class BurstLane:
    def __init__(self, e, ledger_dir: Path, clock=time.time, verdict_path: Path = VERDICT):
        p = e.p
        self.e, self.clock = e, clock
        self.det = BurstDetector(p.burst_sig1m_bps, p.burst_ofi, p.burst_surge)
        self.prints = []
        self.mode = self._mode(p.burst_lane, verdict_path)
        name = 'micro_trades.jsonl' if self.mode == 'active' else 'micro_trades_shadow.jsonl'
        self.ledger = Path(ledger_dir) / name
        self.pos = None          # {'side', 'qty', 'entry', 't', 'tp', 'sl'}
        self.orders = []         # {'side', 'px', 'qty', 'active', 'dead', 'posted', 'kind', 'cid'}
        self.exit_phase, self.exit_since, self.exit_how = None, 0.0, 'TIME-TAKER'
        self.day, self.day_pnl, self.n = None, 0.0, 0
        self.last = {}
        self.hibernating = False
        e.feed.print_hooks.append(self.on_print)

    # ------------------------------------------------------------------ guards
    def _mode(self, want: str, verdict_path: Path) -> str:
        if want != 'active':
            return want
        why = None
        if self.e.p.mode != 'demo' or 'MAINNET' in str(getattr(self.e.venue, 'label', '')):
            why = 'active burst lane is demo-only'
        else:
            try:
                v = json.loads(Path(verdict_path).read_text(encoding='utf-8')).get(self.e.p.symbol, {})
            except (OSError, ValueError):
                v = {}
            if v.get('verdict') != 'ACTIVE-ELIGIBLE':
                why = f'replay verdict {v.get("verdict", "UNKNOWN")} for {self.e.p.symbol}'
        if why:
            self.e.event(f'BURST lane active refused ({why}) -> shadow')
            return 'shadow'
        return 'active'

    def on_print(self, t, px, q, buyer_maker):
        self.det.on_print(t, px, q, buyer_maker)
        if self.mode == 'shadow':
            self.prints.append((t, px, bool(buyer_maker)))

    def _book(self):
        if self.mode == 'shadow':                            # shadow fills use mainnet prints -> mainnet touch
            return self.e.feed.bid, self.e.feed.ask
        bk = self.e.book()                                   # active: the venue's own touch (demo book)
        return bk.get('bid'), bk.get('ask')

    def _free(self, now: float) -> Optional[str]:
        e, p = self.e, self.e.p
        if e.trend_owned or e.pos or e.busy:
            return 'symbol held by the trend / micro lane'
        if now % 14400 < p.burst_trend_quiet_s:
            return '4H close window reserved for the trend lane'
        if self.day_pnl <= -p.burst_daily_kill_usdt:
            return f'daily loss breaker {self.day_pnl:+.2f} U'
        return None

    def _gate(self, now: float) -> Optional[str]:
        """10-16 (b): reason to hold off new entries (CHOP hibernation / VPIN veto), else None.
        10-16 (c): hibernation is a state with hysteresis (was re-armed on every KER flicker: 30 OBSERVE lines in
        under a minute on the 16:51 run, and 4 entries slipped through 17:01-17:03)."""
        e, p = self.e, self.e.p
        if p.burst_chop_gate:
            r = getattr(getattr(e, 'autonomy', None), 'router', None) or {}
            k = (e.features or {}).get('ker')
            chop = r.get('regime') == 'CHOP' or (k is not None and k < CHOP_KER)
            hib = chop or (self.hibernating and (k is None or k < CHOP_EXIT_KER))
            if hib and not self.hibernating:
                e.event(f'BURST OBSERVE {p.symbol}: CHOP regime active (KER < {CHOP_KER}) -> entry hibernated')
            elif self.hibernating and not hib:
                e.event(f'BURST WAKE {p.symbol}: router {r.get("regime", "—")} · KER {k:.2f} >= {CHOP_EXIT_KER}')
            self.hibernating = hib
            if hib:
                return f'CHOP hibernation (enter KER < {CHOP_KER} / router CHOP, wake KER >= {CHOP_EXIT_KER})'
        if p.burst_vpin_veto_s > 0 and getattr(e, 'toxic_vpin', None) is not None:
            age = now - (e.toxic_until - p.vpin_pause_s)                      # seconds since the last VPIN mark
            if 0 <= age < p.burst_vpin_veto_s:
                return f'VPIN TOXIC {e.toxic_vpin:.2f} {e.toxic_side or "?"} flow {age:.0f}s ago -> entry vetoed'
        return None

    # ------------------------------------------------------------------ broker
    async def _post(self, side: int, px: float, qty: float, kind: str, now: float, why: str = '',
                    absorb: Optional[str] = None) -> None:
        o = {'side': side, 'px': px, 'qty': qty, 'active': now, 'dead': INF, 'posted': now, 'kind': kind, 'cid': None,
             'why': why, 'absorb': absorb}
        if self.mode == 'active':
            e = self.e
            o['cid'] = 'gB-' + uuid.uuid4().hex[:28]
            try:
                await e.venue.limit_gtx(e.p.symbol, 'BUY' if side > 0 else 'SELL', _fmt(qty, e.spec['step']),
                                        _fmt(px, e.spec['tick']), o['cid'])
            except Exception:
                return                                   # post-only reject: the next pass re-pegs
        else:
            o['active'] = now + self.e.p.burst_lat_s
        self.orders.append(o)

    async def _cancel(self, o: dict, now: float) -> float:
        """-> qty filled before the cancel (active) / 0 (shadow: in flight for one latency)."""
        if self.mode == 'active':
            e = self.e
            try:
                await e.venue.cancel(e.p.symbol, o['cid'])
            except Exception:
                pass
            st = await e.venue.order(e.p.symbol, o['cid'])
            self.orders.remove(o)
            return float(st.get('filled') or 0.0)
        o['dead'] = min(o['dead'], now + self.e.p.burst_lat_s)
        return 0.0

    async def _fills(self, now: float) -> list:
        """-> [(order, fill px, qty)] since the last pass."""
        out = []
        if self.mode == 'active':
            for o in list(self.orders):
                st = await self.e.venue.order(self.e.p.symbol, o['cid'])
                if st.get('status') == 'FILLED':
                    out.append((o, float(st.get('avg') or o['px']), float(st.get('filled') or o['qty'])))
                    self.orders.remove(o)
            return out
        prints, self.prints = self.prints, []
        for k, (t, px, bm) in enumerate(prints):
            for o in list(self.orders):
                if not (o['active'] <= t < o['dead']):
                    continue
                if (o['side'] > 0 and bm and px < o['px']) or (o['side'] < 0 and not bm and px > o['px']):
                    out.append((o, o['px'], o['qty']))
                    self.orders.remove(o)
                    self.prints = prints[k + 1:] + self.prints     # one fill per pass; the rest next pass
                    return out
            if self.pos and self.pos.get('sl') and ((self.pos['side'] > 0 and px <= self.pos['sl']) or
                                                    (self.pos['side'] < 0 and px >= self.pos['sl'])):
                self.pos['sl_hit'] = True
        self.orders = [o for o in self.orders if o['dead'] > now]
        return out

    async def _taker(self, side: int, qty: float) -> float:
        bid, ask = self._book()
        if self.mode == 'active':
            e = self.e
            ref = ask if side > 0 else bid
            avg, got = await e.venue.ioc(e.p.symbol, 'BUY' if side > 0 else 'SELL', _fmt(qty, e.spec['step']),
                                         _fmt(ref * (1 + side * 0.0015), e.spec['tick']), 'gB-' + uuid.uuid4().hex[:28])
            return avg if got else 0.0
        return ask if side > 0 else bid

    # ------------------------------------------------------------------ ledger
    def _close(self, px: float, how: str, now: float) -> None:
        p, ps = self.e.p, self.pos
        mk, tk = p.maker_bps, p.taker_bps
        gross = ps['side'] * (px - ps['entry']) / ps['entry'] * 1e4
        net = gross - mk - (tk if how in ('SL', 'TIME-TAKER', 'TIME-CAP') else mk)
        usd = net / 1e4 * ps['qty'] * ps['entry']
        self.day_pnl += usd
        self.n += 1
        row = {'t': now, 'lane': 'BURST', 'mode': self.mode, 'symbol': p.symbol, 'side': 'BUY' if ps['side'] > 0 else 'SELL',
               'qty': ps['qty'], 'entry': ps['entry'], 'exit': px, 'exit_how': how, 'hold_s': now - ps['t'],
               'gross_bps': gross, 'net_bps': net, 'net_usd': usd, 'why': ps.get('why'),
               'partial_qty': ps.get('partial_qty'), 'absorb': ps.get('absorb'),
               'fees_model': f'maker {mk:g} / taker {tk:g} bps'}
        try:
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            with open(self.ledger, 'a', encoding='utf-8') as fh:
                fh.write(json.dumps(row, default=float) + '\n')
        except OSError:
            pass
        self.e.event(f'BURST {"SHADOW " if self.mode == "shadow" else ""}{row["side"]} {p.symbol} {how} '
                     f'{net:+.1f} bps ({usd:+.3f} U)')
        self.pos, self.orders, self.exit_phase = None, [], None

    async def _open(self, o: dict, px: float, qty: float, now: float) -> None:
        p, tick = self.e.p, self.e.spec['tick']
        raw = px * (1 + o['side'] * p.burst_tp_bps / 1e4)
        tp = (math.ceil(raw / tick) if o['side'] > 0 else math.floor(raw / tick)) * tick
        self.pos = {'side': o['side'], 'qty': qty, 'entry': px, 't': now, 'why': o.get('why'), 'tp_px': tp,
                    'absorb': o.get('absorb'),
                    'sl': px * (1 - o['side'] * p.burst_sl_bps / 1e4)}
        self.orders = []
        await self._post(-o['side'], tp, qty, 'TP', now)

    async def _flush_exits(self, now: float) -> None:
        for o in list(self.orders):
            got = await self._cancel(o, now)
            if got and self.pos:                             # an exit that partly filled before its cancel
                self.pos['qty'] -= got
                self.pos['partial_qty'] = self.pos.get('partial_qty', 0.0) + got

    async def _exit_taker(self, how: str, now: float) -> None:
        await self._flush_exits(now)
        ps = self.pos
        bid, ask = self._book()
        if ps['qty'] < self.e.spec['step'] / 2:
            self._close(ps['tp_px'] if ps.get('tp_px') else (ask if ps['side'] > 0 else bid), 'TP', now)
            return
        px = await self._taker(-ps['side'], ps['qty'])
        if not px:
            self.exit_phase, self.exit_how = 'taker', how    # IOC outside its band: retry next pass
            self.e.event(f'BURST {self.e.p.symbol} taker exit not filled -> retry')
            return
        self._close(px, how, now)

    # ------------------------------------------------------------------ one pass
    async def step(self) -> dict:
        e, p = self.e, self.e.p
        now = self.clock()
        d = time.strftime('%Y-%m-%d', time.localtime(now))
        if d != self.day:
            self.day, self.day_pnl = d, 0.0
        st = self.det.state(now, (e.features or {}).get('ofi_10s'))
        ab = msig.absorption(e.features or {}, getattr(e, 'sig_cfg', None))
        self.last = {**st, 'absorb': ab['state'], 'absorb_blocks': ab['blocks'], 'mode': self.mode, 'pos': self.pos,
                     'orders': len(self.orders), 'day_pnl': self.day_pnl, 'n': self.n}
        bid, ask = self._book()
        if not bid or not ask or not e.spec:
            return self.last
        for o, px, qty in await self._fills(now):
            if self.pos is None and o['kind'] == 'E':
                for x in list(self.orders):
                    got = await self._cancel(x, now)
                    qty += got if x['side'] == o['side'] else 0.0      # an in-flight entry that also filled
                await self._open(o, px, qty, now)
            elif self.pos is not None and o['kind'] in ('TP', 'X'):
                self._close(px, 'TP' if o['kind'] == 'TP' else 'MAKER-EXIT', now)
        if self.pos is None:
            want, why = 0, self._free(now)
            if not why and st['burst']:
                if p.burst_signal == 'MOM':
                    want = (st['cvd10'] > 0) - (st['cvd10'] < 0)
                elif st['move10'] is not None:
                    want = -((st['move10'] > 0) - (st['move10'] < 0))
                if want and p.burst_absorb_gate and ab['blocks'] == ('BUY' if want > 0 else 'SELL'):
                    self.last['skip'] = f'ABSORPTION TRAP {ab["blocks"]}: {ab["why"]}'
                    want = 0
            gate = self._gate(now)
            if gate:
                self.last['skip'] = gate
                want = 0
            live = [o for o in self.orders if o['dead'] == INF]
            target = bid if want > 0 else ask
            for o in live:
                if not want or o['side'] != want or (o['px'] != target and now - o['posted'] >= p.burst_repeg_s):
                    got = await self._cancel(o, now)
                    if got:                                      # partially filled before the cancel: adopt it
                        await self._open(o, o['px'], got, now)
                        return self.last
            if want and not [o for o in self.orders if o['dead'] == INF]:
                wallet = float((e.account or {}).get('wallet') or p.mock_balance or 0.0)
                notional = min(p.burst_notional_usdt, p.burst_budget_pct * wallet)
                qty = math.floor(notional / target / e.spec['step'] + 1e-9) * e.spec['step']
                if qty * target >= (e.spec or {}).get('min_notional', 5.0):
                    await self._post(want, target, qty, 'E', now, st['why'], ab['state'])
            return self.last
        # holding
        ps = self.pos
        if self.exit_phase == 'taker':
            await self._exit_taker(self.exit_how, now)
            return self.last
        if ps.get('sl_hit') or (self.mode == 'active' and ((ps['side'] > 0 and bid <= ps['sl']) or
                                                            (ps['side'] < 0 and ask >= ps['sl']))):
            await self._exit_taker('SL', now)
            return self.last
        if self.exit_phase is None and now - ps['t'] >= p.burst_hold_s:
            self.exit_phase, self.exit_since = 'maker', now
            await self._flush_exits(now)
        if self.exit_phase == 'maker':
            pegged = p.burst_time_exit == 'maker'            # SL above still trips; only time exits stay passive
            if now - self.exit_since >= (p.burst_exit_cap_s if pegged else p.burst_exit_maker_s):
                await self._exit_taker('TIME-CAP' if pegged else 'TIME-TAKER', now)
                return self.last
            target = ask if ps['side'] > 0 else bid
            live = [o for o in self.orders if o['dead'] == INF]
            if live and live[0]['px'] != target and now - live[0]['posted'] >= p.burst_repeg_s:
                await self._flush_exits(now)
                live = []
            if not live and self.pos:
                await self._post(-ps['side'], target, ps['qty'], 'X', now)
        return self.last
