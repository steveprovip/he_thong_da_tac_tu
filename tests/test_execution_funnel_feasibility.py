"""Execution funnel, lane feasibility, scheduler fairness and entry lifecycle (2026-10-08v, form KHOP_LENH_NET_EV).

Software behaviour only: none of these tests says anything about profitability."""
import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from core import engine as eng
from core import exec_funnel as xf
from core import micro_swarm as ms
from tests.test_multi_asset_scanner import SOL, fill_sol, scanner, sol_feed
from tests.test_two_mode_engine import depth, fill_through, mock_engine, sig

ROOT = Path(__file__).resolve().parents[1]
BTC, DOGE = 'BTCUSDT', 'DOGEUSDT'
BUY = ms.evaluate(sig(FLOW=('BUY', 1.0, 20), TREND=('BUY', 1.0, 20)), ms.Gates(cost_bps=4))[0]
DEMO = eng.PROFILES['demo']
PROD = {k: getattr(DEMO, k) for k in (                       # production demo gates (mock_engine relaxes them)
    'min_edge_bps', 'ker_gate', 'min_velocity_pct', 'concordance_gate', 'vfr_gate', 'mr_flow_gate', 'net_rr_min',
    'chop_scalper', 'drift_accel', 'observe_only')}


def prod_engine(tmp_path, sym=SOL, feed=None, **kw):
    e, venue, feed = mock_engine(tmp_path, feed=feed or sol_feed(), **{**PROD, **kw})
    e.p = replace(e.p, symbol=sym, qty=eng.SYMBOL_QTY.get(sym, 0.0))
    e.spec = dict(eng.FALLBACK_SPECS[sym])
    from core import micro_signals as msig
    e.sig_cfg = e.feed.cfg = msig.config_for(sym)
    return e, venue, feed


def passing_state(e, side='BUY'):
    """Inputs under which every production churn gate is open for a TREND entry."""
    e.sig = {'TREND': {'side': side, 'strength': 1.0, 'edge_bps': 40, 'why': 'T'}}
    e.features = {'atr15_pct': 0.60, 'atr15_src': 'test', 'spread_bps': 0.9, 'atr_pct': 0.20, 'ker': 0.8,
                  'px_vel_15s': 0.05 if side == 'BUY' else -0.05, 'ofi_10s': 0.3, 'cvd_60s': 0.0}
    e.chop_locked = False
    e.reg = {'name': 'UP' if side == 'BUY' else 'DOWN'}
    e.macro = {'name': 'MIXED', 'allow': ('BUY', 'SELL')}
    e.absorb = {'blocks': None}
    e.flow = {'buy_ok': True, 'sell_ok': True}
    d = replace(BUY, score=0.95)
    return d, {'consensus': 0.95, 'jev': 0.0, 'gates_ok': True, 'edge_bps': 40.0}


def rows(tmp_path):
    p = tmp_path / 'engine_orders.jsonl'
    return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines()] if p.exists() else []


# ============================================================================ A. lane feasibility
def test_trend_lane_has_a_path_through_every_production_gate(tmp_path):
    e, _, _ = prod_engine(tmp_path)
    d, diag = passing_state(e)
    assert e.lane(d) == 'TREND'
    assert e.churn_checks(d, diag)[0] == [] and e.churn_gate(d, diag) == (True, '')


async def test_trend_lane_reaches_submit_and_fill_with_production_execution_checks(tmp_path):
    """After the gates: OKAPI plan, ZEPHR net RR in execute, RUNE portfolio, VESKA token -> a GTX order."""
    e, venue, feed = prod_engine(tmp_path)
    await e.sync()
    d, _ = passing_state(e)
    res = await fill_sol(e, feed, e.execute('BUY', d, 'SWARM'))
    assert res['ok'] and e.pos and rows(tmp_path)[-1]['outcome'] == 'FILLED'
    assert e.authorized == 1


def test_feasibility_matrix_from_the_real_demo_config(tmp_path):
    e, _, _ = prod_engine(tmp_path)
    m = e.lane_feasibility()
    assert m['TREND']['state'] == 'ENABLED' and m['TREND']['feasible'] is True and m['TREND']['rr_min'] >= 1.30
    assert m['MR']['feasible'] is False and m['MR']['rr_max'] == pytest.approx(0.875, abs=0.001)
    assert m['SCALP']['state'] == 'SHADOW' and m['SCALP']['feasible'] is None and m['SCALP']['gate'] is None


def test_mr_lane_is_infeasible_for_every_vwap_deviation(tmp_path):
    """No input reaches net RR 1.30 on MR: TP is clamped at 0.18 %, SL 0.08 % + 0.08 % fees -> max 0.875."""
    e, _, _ = prod_engine(tmp_path)
    best = max(e.bracket_plan('MR', {'vwap_dev_pct': v / 100})['rr_net'] for v in range(0, 500, 5))
    assert best == pytest.approx(0.875, abs=0.001) and best < e.p.net_rr_min


def test_live_lane_is_infeasible_at_the_loss_budget(tmp_path):
    e, _, _ = mock_engine(tmp_path, mode='live')
    e.p = replace(e.p, symbol=DOGE, net_rr_min=1.30)
    e.spec = dict(eng.FALLBACK_SPECS[DOGE])
    m = e.lane_feasibility(price=0.0885)
    assert m['TREND']['feasible'] is False and m['TREND']['rr_max'] < 0.6


def test_btc_observer_lanes_are_marked_observer(tmp_path):
    e, _, _ = prod_engine(tmp_path, sym=BTC)
    assert e.observer and all(v['state'] == 'OBSERVER' and v['feasible'] is None for v in e.lane_feasibility().values())


# ============================================================================ A. all blockers, first unchanged
def test_every_blocker_is_listed_and_the_first_is_the_returned_reason(tmp_path):
    e, _, _ = prod_engine(tmp_path)
    d, diag = passing_state(e)
    e.chop_locked = True                                            # KER
    e.flow = {'buy_ok': False, 'sell_ok': False, 'why': 'x'}       # FLOW
    diag = {**diag, 'edge_bps': 5.0}                                # EDGE
    out, _ = e.churn_checks(d, diag)
    assert [k for k, _ in out] == ['KER', 'FLOW', 'EDGE']
    ok, why = e.churn_gate(d, diag)
    assert not ok and why == out[0][1] and why.startswith('CHOP LOCKED') and e.blockers == ['KER', 'FLOW', 'EDGE']


def test_drift_probe_is_only_set_when_the_entry_passes(tmp_path):
    e, _, _ = prod_engine(tmp_path, drift_lock=True, drift_accel=True)
    d, diag = passing_state(e)
    e.reg = {'name': 'DRIFT'}
    e.features.update(cvd_60s=5e6, cvd_3m=1e5)
    e.drift_probe = None
    assert e.churn_gate(d, diag)[0] and e.drift_probe and e.drift_probe.startswith('DRIFT ACCEL')
    e.drift_probe = None
    assert not e.churn_gate(d, {**diag, 'edge_bps': 1.0})[0] and e.drift_probe is None


def test_gate_classes():
    assert {xf.GATE_CLASS[k] for k in ('ZEPHR', 'RUNE', 'OKAPI', 'MARIN', 'VESKA')} == {xf.HARD}
    assert xf.GATE_CLASS['NET_RR'] == xf.GATE_CLASS['VFR'] == xf.COST
    assert all(xf.GATE_CLASS[k] == xf.STAT for k in ('KER', 'FLOW', 'VPIN', 'DRIFT', 'SCORE', 'EDGE', 'WEAK'))
    assert xf.GATE_CLASS['OBSERVER'] == xf.CONFIG


async def test_funnel_samples_each_stage_once_per_second(tmp_path):
    e, venue, feed = prod_engine(tmp_path)
    await e.sync()
    clock = [1000.0]
    e.funnel = xf.Funnel(clock=lambda: clock[0])
    for _ in range(5):                                              # 5 ticks inside one second -> 1 sample
        await e.tick()
    assert sum(e.funnel.stages.values()) == 1
    clock[0] += 1.0
    await e.tick()
    assert sum(e.funnel.stages.values()) == 2
    v = e.funnel.view()
    assert v['samples_every_s'] == 1.0 and set(v) >= {'stages', 'first', 'all', 'only', 'class'}


def test_funnel_record_counts_all_first_and_only():
    f = xf.Funnel(clock=lambda: 0.0)
    f.record('TREND', 'CHURN', ['VFR', 'NET_RR'])
    f.record('TREND', 'CHURN', ['NET_RR'])
    assert f.first[('TREND', 'VFR')] == 1 and f.all[('TREND', 'NET_RR')] == 2 and f.only[('TREND', 'NET_RR')] == 1


async def test_observer_candidate_is_counted_as_observer_not_submitted(tmp_path):
    e, venue, feed = prod_engine(tmp_path, sym=BTC, feed=None, observe_only=(BTC,))
    e.p = replace(e.p, symbol=BTC)
    d, diag = passing_state(e)
    e.funnel_sample(d, [], diag, True, 'OBSERVER')
    assert e.funnel.stages[('TREND', 'OBSERVER')] == 1 and 'OBSERVER' in e.funnel.last_blockers


# ============================================================================ dashboard schema
async def test_scanner_view_publishes_the_counters_app_js_reads(tmp_path):
    es, venue, feeds = await scanner(tmp_path)
    r = eng.Runner('demo', False, env={}, serve=False)
    r.engines, r.engine = es, es[SOL]
    sv = r.scanner_view(es[SOL])
    for k in ('intents', 'fills', 'cancels', 'fill_rate', 'trades_per_hour', 'rows', 'funnel', 'feasibility'):
        assert k in sv
    assert sv['intents'] == 0 and sv['fills'] == 0 and sv['fill_rate'] is None      # unknown rate, not 0 %
    json.dumps(sv)                                                                    # SSE-serialisable


async def test_counters_follow_the_order_log(tmp_path):
    e, venue, feed = mock_engine(tmp_path, gtx_ttl_s=0.3)
    await e.sync()
    await e.execute('BUY', BUY, 'SWARM')                            # nobody trades through: UNFILLED
    e._shadow_row({'outcome': 'SHADOW_SCALP', 'side': 'BUY'})
    c = e.funnel.counters()
    assert [r['outcome'] for r in rows(tmp_path)] == ['UNFILLED', 'SHADOW_SCALP']
    assert c['intents'] == 1 and c['cancels'] == 1 and c['fills'] == 0 and c['fill_rate'] == 0.0 and c['shadow'] == 1


def test_app_js_never_prints_undefined_scanner_counters():
    js = (ROOT / 'web_ui' / 'app.js').read_text(encoding='utf-8')
    assert '${sc.intents}' not in js and '${sc.fills}' not in js and '${sc.cancels}' not in js
    assert "n(sc.intents)" in js and "'—'" in js


# ============================================================================ scheduler: UI focus != permission
async def test_btc_observer_in_focus_does_not_slow_sol_and_doge(tmp_path):
    es, venue, feeds = await scanner(tmp_path, observe_only=(BTC,))
    r = eng.Runner('demo', False, env={}, serve=False)
    r.engines = es
    r.engine = es[BTC]                                              # the dashboard shows BTC
    assert r.idle(es[BTC]) and not r.idle(es[SOL]) and not r.idle(es[DOGE])
    assert r.cadence(es[BTC], 1.2) == (3.0, 4.0)
    assert r.cadence(es[SOL], 1.2) == r.cadence(es[DOGE], 1.2) == (2.0, 3.0)
    r.engine = es[SOL]
    assert r.cadence(es[SOL], 1.2) == (1.0, 1.2) and r.cadence(es[DOGE], 1.2) == (2.0, 3.0)
    es[DOGE].busy = True
    assert r.cadence(es[DOGE], 1.2) == (0.3, 1.2)


async def test_default_focus_is_the_first_tradable_symbol(tmp_path):
    es, venue, feeds = await scanner(tmp_path, observe_only=(BTC,))
    r = eng.Runner('demo', False, env={}, serve=False)
    r.engines = es
    assert r.default_focus() == SOL


def weight_per_min(r, es):
    w = 0.0
    for e in es.values():
        book_s, sync_s = r.cadence(e, 1.2)
        w += 60 / (sync_s + 0.6) * 12 + 60 / (book_s + 0.15) * 2
    return w


def old_cadence(r, e):
    """Pre-10-08v: idle = flat and not the focused engine."""
    if e.pos or e.busy:
        return 0.3, 1.2
    idle = len(r.engines) > 1 and e is not r.engine
    return (3.0, 4.0) if idle else (1.0, 1.2)


@pytest.mark.parametrize('hot', [None, SOL, DOGE])
async def test_rest_weight_budget_never_exceeds_the_previous_schedule(tmp_path, hot):
    """Weight/min estimate: sync 12 (positionRisk 5 + account 5 + 2 order lists), bookTicker 2, ~0.6 s REST per
    sync, 0.15 s per book.  Focus follows the hot engine (Runner.refocus).  Flat: < 1 000 (client brake); with a
    position / order in flight: not above the pre-10-08v schedule."""
    es, venue, feeds = await scanner(tmp_path, observe_only=(BTC,))
    r = eng.Runner('demo', False, env={}, serve=False)
    r.engines = es
    if hot:
        es[hot].busy = True
    focuses = [hot] if hot else list(es)
    for focus in focuses:
        r.engine = es[focus]
        new = weight_per_min(r, es)
        old = sum(60 / (s + 0.6) * 12 + 60 / (b + 0.15) * 2 for b, s in (old_cadence(r, e) for e in es.values()))
        assert new <= old + 1e-6 if hot else new < 1000


async def test_decisions_do_not_depend_on_focus(tmp_path):
    """decide_loop ticks every engine every 0.2 s on the WS feed: the gates read the feed, not the REST view."""
    es, venue, feeds = await scanner(tmp_path, observe_only=(BTC,), ker_gate=False, min_edge_bps=0.0,
                                     concordance_gate=False, min_velocity_pct=0.0, fee_hurdle=False)
    d = await fill_sol(es[SOL], feeds[SOL], es[SOL].tick())         # BTC focus or not: SOL decides and fills
    assert d.allowed and es[SOL].pos


# ============================================================================ B. entry lifecycle
class PartialOnCancel(eng.MockVenue):
    """Cancel of the first resting entry reports a partial fill that raced the cancel."""

    def __init__(self, *a, part=0.001, **kw):
        super().__init__(*a, **kw)
        self.posts, self.part, self.done = [], part, False

    async def limit_gtx(self, symbol, side, qty, price, cid):
        self.posts.append((cid, float(price), float(qty)))
        await super().limit_gtx(symbol, side, qty, price, cid)

    async def cancel(self, symbol, cid):
        o = self.orders.get(cid)
        if o and o['status'] == 'NEW' and o['kind'] == 'ENTRY' and not self.done:
            self.done = True
            o['status'], o['filled'] = 'CANCELED', self.part
            self._fill(symbol, o['side'], self.part, o['price'], self.cfg.maker_bps)
            return
        await super().cancel(symbol, cid)

    async def order(self, symbol, cid):
        o = self.orders.get(cid, {})
        if o.get('filled'):
            return {'status': o['status'], 'filled': o['filled'], 'avg': o['price']}
        return await super().order(symbol, cid)


def partial_engine(tmp_path, **prof):
    e, _, feed = mock_engine(tmp_path, gtx_ttl_s=3.0, **prof)
    v = PartialOnCancel(e.p, quote=lambda s: (feed.bid or feed.price(), feed.ask or feed.price()))
    e.venue = v
    return e, v, feed


async def test_partial_fill_racing_a_peg_cancel_is_kept_and_never_reposted_full(tmp_path):
    e, venue, feed = partial_engine(tmp_path)
    await e.sync()
    task = asyncio.ensure_future(e.execute('BUY', BUY, 'SWARM'))
    await asyncio.sleep(0.15)
    bid0 = feed.bid
    for _ in range(3):
        feed.on_message(depth(bid0 + 2.0, bid0 + 2.1, bq=6.0, aq=1.0))      # touch walks away -> peg cancels
    res = await task
    assert res['ok'] and len(venue.posts) == 1                               # no re-post of the full quantity
    assert e.pos['qty'] == pytest.approx(0.001) and rows(tmp_path)[-1]['outcome'] == 'PARTIAL'
    assert rows(tmp_path)[-1]['qty_filled'] == pytest.approx(0.001)
    assert any(o['kind'] == 'SL' and o['status'] == 'NEW' for o in venue.orders.values())   # stop on the partial


class FailingPoll(eng.MockVenue):
    """REST error while polling the resting entry (the order itself is live on the venue)."""

    def __init__(self, *a, fill_first=False, **kw):
        super().__init__(*a, **kw)
        self.calls, self.fill_first, self.cancels = 0, fill_first, []

    async def order(self, symbol, cid):
        o = self.orders.get(cid, {})
        if o.get('kind') == 'ENTRY' and o.get('status') in ('NEW', 'FILLED') and not self.cancels:
            self.calls += 1
            if self.calls == 2:
                if self.fill_first:
                    o['status'] = 'FILLED'
                    self._fill(symbol, o['side'], o['qty'], o['price'], self.cfg.maker_bps)
                raise RuntimeError('-1001 internal error (test)')
        return await super().order(symbol, cid)

    async def cancel(self, symbol, cid):
        self.cancels.append(cid)
        await super().cancel(symbol, cid)


def failing_engine(tmp_path, **kw):
    e, _, feed = mock_engine(tmp_path, gtx_ttl_s=1.0)
    v = FailingPoll(e.p, quote=lambda s: (feed.bid or feed.price(), feed.ask or feed.price()), **kw)
    e.venue = v
    return e, v, feed


async def test_error_while_polling_cancels_the_resting_entry(tmp_path):
    """Reproduced: before 10-08v the except path returned with the GTX entry still NEW on the venue."""
    e, venue, feed = failing_engine(tmp_path)
    await e.sync()
    res = await e.execute('BUY', BUY, 'SWARM')
    assert not res['ok'] and 'RuntimeError' in res['error']
    entries = [o for o in venue.orders.values() if o['kind'] == 'ENTRY']
    assert entries and all(o['status'] != 'NEW' for o in entries) and venue.cancels
    assert rows(tmp_path)[-1]['outcome'] == 'ERROR' and not e.busy


async def test_late_fill_on_an_errored_entry_is_adopted_with_a_stop(tmp_path):
    e, venue, feed = failing_engine(tmp_path, fill_first=True)
    await e.sync()
    res = await e.execute('BUY', BUY, 'SWARM')
    assert not res['ok'] and any('had filled' in ev['message'] for ev in e.events)
    await e.sync()                                                          # reconciliation: ADOPT + stop
    assert e.pos and e.pos['path'] == 'ADOPT'
    assert any(o['kind'] == 'SL' and o['status'] == 'NEW' for o in venue.orders.values())


async def test_unfilled_deadline_is_finite_and_cancels(tmp_path):
    import time
    e, venue, feed = mock_engine(tmp_path, gtx_ttl_s=0.4)
    await e.sync()
    t0 = time.monotonic()
    res = await e.execute('BUY', BUY, 'SWARM')
    assert not res['ok'] and time.monotonic() - t0 < 1.5
    assert all(o['status'] != 'NEW' for o in venue.orders.values() if o['kind'] == 'ENTRY')


def test_order_quantity_respects_min_notional_and_step():
    spec = dict(eng.FALLBACK_SPECS[DOGE])
    pl = eng.plan('BUY', 0.0885, spec, replace(DEMO, symbol=DOGE, qty=0.0, sl_pct=0.5))
    assert pl['ok'] and pl['qty'] * 0.0885 >= spec['min_notional'] and pl['qty'] == int(pl['qty'])
    pl = eng.plan('BUY', 115.13, dict(eng.FALLBACK_SPECS[SOL]), replace(DEMO, symbol=SOL, qty=0.003, sl_pct=0.35))
    assert pl['qty'] * 115.13 >= 5.0 and round(pl['qty'] / 0.01, 9) == int(round(pl['qty'] / 0.01))


async def test_each_attempt_uses_a_fresh_client_id(tmp_path):
    """No duplicate retry: the client never re-sends a POST (no retry loop) and every attempt has a new cid."""
    e, venue, feed = mock_engine(tmp_path, gtx_ttl_s=0.2)
    await e.sync()
    await e.execute('BUY', BUY, 'SWARM')
    await e.execute('BUY', BUY, 'SWARM')
    cids = [c for c, o in venue.orders.items() if o['kind'] == 'ENTRY']
    assert len(cids) == len(set(cids)) == 2


async def test_stale_feed_blocks_the_swarm_as_a_hard_guard(tmp_path):
    e, venue, feed = mock_engine(tmp_path)
    gz = e.gates({'stale': True, 'spread_bps': 1.0, 'price': 100.0})
    assert gz.as_map()['ZEPHR'][0] is False and xf.GATE_CLASS['ZEPHR'] == xf.HARD


async def test_feed_stale_tick_is_counted_as_a_hard_veto(tmp_path):
    e, venue, feed = mock_engine(tmp_path)
    await e.sync()
    e.funnel = xf.Funnel(clock=lambda: 5.0)
    d = BUY
    votes = [ms.Vote('ZEPHR', 'HOLD', 0.0, 'STALE', veto=True)]
    e.funnel_sample(replace(d, allowed=False), votes, {'side': 'BUY'}, False, None)
    assert e.funnel.stages[('TREND', 'HARD')] == 1 and e.funnel.first[('TREND', 'ZEPHR')] == 1


# ============================================================================ offline replay tool
def test_gtx_placement_replay_counts_unfilled_opportunities():
    import sys
    sys.path.insert(0, str(ROOT / 'tools'))
    import replay_gtx_placement as rg
    t = [1000.0 + i * 0.1 for i in range(2000)]
    b = [100.00] * 2000
    a = [100.01] * 2000
    bq = [50.0] * 2000
    aq = [50.0] * 2000
    for i in range(400, 2000):                                     # sellers clear the bid after 40 s
        b[i], a[i] = 99.99, 100.00
    bk = rg.Book(t, b, a, bq, aq)
    x = rg.run_policy(bk, 'JOIN_T3', 'clear', 1000.5, 'BUY')
    assert x['filled'] is False                                    # nothing traded within 3 s
    x = rg.run_policy(bk, 'JOIN_T3', 'clear', 1038.0, 'BUY')
    assert x['filled'] and x['px'] == pytest.approx(100.00)
    x = rg.run_policy(bk, 'JOIN_T3', 'front', 1000.5, 'BUY')
    assert x['filled'] is False                                    # queue never moved
    rws = rg.evaluate(bk, [(1000.5, 'BUY'), (1038.0, 'BUY')])
    s = rg.summarize(rws)
    assert s['JOIN_T3|clear']['n'] == 2 and s['JOIN_T3|clear']['fill_rate'] == 0.5
    assert s['SKIP|clear']['net30_per_opp'] == 0.0
