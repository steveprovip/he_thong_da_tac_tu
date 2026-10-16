"""Autonomy hub: online swarm learner + parameter bandit + hot tunables + dreaming trigger + capital router.

One instance per Runner (and per replay).  Engines call three hooks (all no-ops when Profile.autonomy == 'off'):
  refresh(e, f)        while flat: recompute the effective Profile = baseline -> validated tunables ->
                       bandit arms -> router observer; new bandit draw after every settle
  sig_for_vote(e, sig) alpha-agent strengths x learned weights ('on'); 'shadow' only counts disagreements
  on_settle(e, row)    learner + bandit update, persistence, dreaming counter
The Runner (or the replay shim) runs autonomy_loop(): every 5 s it polls config/live_tunables.json (mtime),
computes the capital router and, every 15 min or after 3 new closed trades, runs one dreaming pass.
Every adaptation emits '⚡ AI SELF-ADAPTED: ...' as an engine event (telemetry) and is kept in self.log.
Hard limits: risk can only go down versus the baseline Profile (core/tunables.LIMITS); gate agents (ZEPHR,
RUNE, OKAPI, MARIN, VESKA) are never re-weighted; live profile has autonomy 'off'.
"""
from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Optional

from core import capital_router, dreaming_daemon, tunables
from core.param_bandit import ARMS, ParamBandit, regime_of
from core.swarm_learner import ALPHA_AGENTS, SwarmLearner, reweight

ADAPTED = '⚡ AI SELF-ADAPTED'


class Autonomy:
    def __init__(self, data_dir: Path, config_dir: Path, ledger: Path, clock=time.time, seed: Optional[int] = None,
                 dream_every_s: float = 900.0, dream_every_trades: int = 3):
        self.data_dir, self.config_dir, self.ledger, self.clock = Path(data_dir), Path(config_dir), Path(ledger), clock
        self.learner = SwarmLearner(clock=clock)
        self.bandit = ParamBandit(seed=seed, clock=clock)
        self.watcher = tunables.TunableWatcher(self.config_dir / 'live_tunables.json', clock=clock)
        self.engines: dict = {}
        self.base: dict = {}
        self.arms: dict = {}                      # symbol -> current bandit choice (applied while flat)
        self.need_draw: dict = {}
        self.applied_key: dict = {}
        self.router = {'regime': 'NORMAL', 'scalp_alloc': 1.0, 'trend_trail': False, 'why': ''}
        self.router_counts = {'NORMAL': 0, 'CHOP': 0, 'BURST': 0}
        self.router_pending = None
        self.router_hold_s = 60.0
        self.router_said = -1e18
        self.chop_exit_since = None               # first poll of an unbroken KER >= 0.40 run while in CHOP
        self.chop_left_at = -1e18
        self.shadow_disagree = 0
        self.counts = {'dreams': 0, 'traps': {}, 'directives': 0, 'mutes': 0, 'bans': 0, 'router_switches': 0}
        self.settled_since_dream = 0
        self.last_dream = clock()
        self.dream_every_s, self.dream_every_trades = dream_every_s, dream_every_trades
        self.last_dream_result: dict = {}
        self.log: list = []
        self.tun_state = ({}, False, {}, [])

    # ------------------------------------------------------------------ wiring
    def attach(self, engines: dict) -> None:
        self.engines = engines
        rows = []
        for s, e in engines.items():
            self.base[s] = e.p
            self.need_draw[s] = True
            e.autonomy = self
        e0 = next(iter(engines.values()), None)
        if e0 is not None:
            rows = list(e0.all_rows)
            self.learner.warm_start(rows)
            self.bandit.warm_start(rows)

    def mode(self, e) -> str:
        return getattr(self.base.get(e.p.symbol, e.p), 'autonomy', 'off')

    def say(self, e, msg: str) -> None:
        line = f'{ADAPTED}: {msg}'
        self.log.append({'t': self.clock(), 'symbol': getattr(e.p, 'symbol', None), 'message': line})
        self.log = self.log[-200:]
        if e is not None:
            e.event(line)

    # ------------------------------------------------------------------ engine hooks
    def refresh(self, e, f: Optional[dict] = None) -> None:
        if self.mode(e) != 'on' or e.pos or e.busy:
            return
        s = e.p.symbol
        base = self.base[s]
        changes, observe, _, _ = self.tun_state
        if self.need_draw.get(s):
            self.arms[s] = self.bandit.select(regime_of((f or e.features or {}).get('ker'),
                                                        base.cs_chop_ker, base.cs_trend_ker))
            self.need_draw[s] = False
        arm = {k: v for k, v in (self.arms.get(s) or {}).items() if k in ARMS and k not in changes}
        if base.tp1_scaled_pct is None:
            arm.pop('tp1_scaled_pct', None)               # the TP1 split is off in this profile: do not switch it on
        r_obs = self.router.get('scalp_alloc', 1.0) <= 0.0
        key = (tuple(sorted(changes.items())), observe, tuple(sorted(arm.items())), r_obs)
        if self.applied_key.get(s) == key:
            return
        p = tunables.apply_profile(base, {**arm, **changes}, observe or r_obs, [s])
        prev = self.applied_key.get(s)
        e.p = p
        self.applied_key[s] = key
        if prev is not None and (prev[0] != key[0] or prev[1] != key[1]):
            what = ', '.join(f'{k}={v:g}' for k, v in changes.items()) or 'baseline size/risk'
            self.say(e, f'Updated Sizing Policy {s}: {what}' + (' · OBSERVER' if observe else ''))

    def demote(self, e, d, evaluate, alpha_keys: dict):
        """Learned weights may only REMOVE a trade, never add one or make it bigger.
        Replay 10/01-07 (first version, weights inside the vote): down-weighting LUMEN/TIDAL pushed the swarm
        score under 76.8 %, the JEV fast path then authorised the same setup at score ~0.98 and conviction sizing
        gave it the 10 % tier -> 3x larger trades.  Now the unweighted decision (and its size) stands, and the
        weighted re-vote can only veto it.  'shadow' counts the would-be vetoes without acting."""
        m = self.mode(e)
        if m == 'off':
            return d
        w = self.weights()
        if all(abs(x - 1.0) < 1e-9 for x in w.values()):
            return d
        dw = evaluate(reweight(e.sig, w, alpha_keys))
        if dw.allowed and dw.side == d.side:
            return d
        self.shadow_disagree += 1
        if m != 'on':
            return d
        from dataclasses import replace as _r
        muted = [a for a, x in w.items() if x == 0.0]
        return _r(d, allowed=False, reason=f'LEARNER VETO {",".join(muted) + " muted" if muted else ""} '
                                          f'weights {w} -> {dw.reason}'[:200])

    def sig_for_vote(self, e, sig: dict, alpha_keys: dict) -> dict:
        """Diagnostics only (telemetry / tests): the signals as the learner would weight them."""
        return reweight(sig, self.weights(), alpha_keys) if self.mode(e) != 'off' else sig

    def weights(self) -> dict:
        w = self.learner.weights()
        over = self.tun_state[2]
        return {a: over.get(a, w[a]) for a in ALPHA_AGENTS}

    def entry_tags(self, e) -> dict:
        if self.mode(e) == 'off':
            return {}
        return {'arms': dict(self.arms.get(e.p.symbol) or {}) or None, 'agent_w': self.weights()}

    def on_settle(self, e, row: dict) -> None:
        if self.mode(e) == 'off':
            return
        notional = float(row.get('qty') or 0) * float(row.get('entry') or 0)
        net_bps = float(row.get('pnl') or 0) / notional * 1e4 if notional else 0.0
        before = self.weights()
        ev = self.learner.observe(row.get('agents'), row.get('side'), float(row.get('gross') or 0.0), net_bps)
        ev += self.bandit.update(row.get('arms'), net_bps)
        self.need_draw[e.p.symbol] = True
        self.settled_since_dream += 1
        after = self.weights()
        moved = {a: (before[a], after[a]) for a in after if abs(after[a] - before[a]) >= 0.05}
        if moved:
            self.say(e, 'Updated Swarm Weights ' + ' '.join(f'{a} {b:.2f}->{c:.2f}' for a, (b, c) in moved.items()))
        for x in ev:
            self.counts['mutes' if 'muted' in x else 'bans'] += 1
            self.say(e, x)
        self.learner.save(self.data_dir / 'swarm_learner.json')
        self.bandit.save(self.data_dir / 'param_bandit.json')

    # ------------------------------------------------------------------ loop work (Runner / replay shim)
    def poll(self) -> None:
        any_e = next(iter(self.engines.values()), None)
        changed = self.watcher.poll()
        doc = self.watcher.active()
        if any_e is None:
            return
        base = self.base[any_e.p.symbol]
        st = tunables.validate(doc, base) if doc else ({}, False, {}, [])
        if st[:3] != self.tun_state[:3]:
            self.tun_state = st
            msg = (f'tunables v{doc.get("version", "?")} from {doc.get("source", "file")} ({doc.get("reason", "")}): '
                   + (', '.join(f'{k}={v:g}' for k, v in st[0].items()) or 'baseline')
                   + (' · OBSERVER' if st[1] else '') + (f' · swarm {st[2]}' if st[2] else '')) if doc else \
                'tunables expired/cleared -> baseline restored'
            self.say(any_e, msg)
            if st[3]:
                self.say(any_e, 'rejected: ' + '; '.join(st[3]))
        elif changed and st[3]:
            self.say(any_e, 'rejected: ' + '; '.join(st[3]))

    def route(self) -> dict:
        feats = {s: e.features for s, e in self.engines.items() if s not in (self.base[s].observe_only or ())}
        scale = {s: getattr(getattr(e.feed, 'cfg', None), 'usd_scale', 1.0) for s, e in self.engines.items()}
        r = capital_router.route(feats, scale)
        now = self.clock()
        # 10-16: leaving CHOP needs KER >= 0.40 on some symbol for CHOP_EXIT_HOLD_S unbroken (was immediate; RFC log: 13
        # NORMAL<->CHOP flips in 230 min on 10-16); BURST waits BURST_COOLDOWN_S after CHOP ends
        if self.router.get('regime') == 'CHOP' and r['regime'] != 'CHOP':
            if not capital_router.chop_exit_confirmed(feats):
                self.chop_exit_since = None
                return self.router
            if self.chop_exit_since is None:
                self.chop_exit_since = now
            if now - self.chop_exit_since < capital_router.CHOP_EXIT_HOLD_S:
                return self.router
            self.chop_left_at = now
        self.chop_exit_since = None
        if r['regime'] == 'BURST' and now - self.chop_left_at < capital_router.BURST_COOLDOWN_S:
            r = {'regime': 'NORMAL', 'scalp_alloc': 1.0, 'trend_trail': False, 'symbols': [],
                 'why': f'BURST held as NORMAL for {capital_router.BURST_COOLDOWN_S:.0f} s after CHOP'}
        # hysteresis: a new regime must persist ROUTER_HOLD_S before it takes over (no 5 s flapping)
        leaving_chop = self.router.get('regime') == 'CHOP' and r['regime'] != 'CHOP'
        if r['regime'] != self.router.get('regime') and not leaving_chop:   # CHOP exit already held above
            if self.router_pending is None or self.router_pending[0] != r['regime']:
                self.router_pending = (r['regime'], self.clock())
            if self.clock() - self.router_pending[1] < self.router_hold_s:
                return self.router
        self.router_pending = None
        if r['regime'] != self.router.get('regime'):
            self.counts['router_switches'] += 1
        if r['regime'] != self.router.get('regime') and r['regime'] != 'NORMAL' and \
                self.clock() - self.router_said >= 600:          # announce at most every 10 min (no UI spam)
            self.router_said = self.clock()
            e0 = next(iter(self.engines.values()))
            self.say(e0, f'capital router {self.router.get("regime")} -> {r["regime"]}'
                         + (f' ({r["why"]})' if r['why'] else ''))
        self.router = r
        self.router_counts[r['regime']] = self.router_counts.get(r['regime'], 0) + 1
        return r

    def dream_due(self) -> bool:
        return (self.settled_since_dream >= self.dream_every_trades
                or self.clock() - self.last_dream >= self.dream_every_s)

    def dream(self) -> dict:
        res = dreaming_daemon.run_once(self.ledger, self.data_dir / 'brain_learnings.jsonl',
                                       self.config_dir / 'dynamic_state.json',
                                       self.config_dir / 'live_tunables.json', now=self.clock(),
                                       extra_state={'swarm': self.learner.view(), 'router': self.router})
        self.last_dream, self.settled_since_dream = self.clock(), 0
        self.counts['dreams'] += 1
        for n, _ in res['traps']:
            self.counts['traps'][n] = self.counts['traps'].get(n, 0) + 1
        if isinstance(res['directive'], dict):
            self.counts['directives'] += 1
        self.last_dream_result = {'t': self.clock(), 'traps': [n for n, _ in res['traps']],
                                  'promoted': res['promoted'], 'lessons': len(res['lessons'])}
        if res['traps'] or res['promoted']:
            e0 = next(iter(self.engines.values()))
            self.say(e0, f'dreaming: traps {self.last_dream_result["traps"]}'
                         + (f' · promoted {res["promoted"]}' if res['promoted'] else ''))
        return res

    def view(self) -> dict:
        return {'weights': self.weights(), 'agents': self.learner.view(), 'arms': self.arms,
                'learner_vetoes': self.shadow_disagree, 'counts': self.counts,
                'router': self.router, 'router_counts': self.router_counts,
                'tunables': {'version': self.watcher.version, 'active': self.watcher.active(),
                             'error': self.watcher.error, 'rejected': self.tun_state[3]},
                'dream': self.last_dream_result, 'log': self.log[-10:]}
