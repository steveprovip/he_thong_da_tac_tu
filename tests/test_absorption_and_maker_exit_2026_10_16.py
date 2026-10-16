"""10-16 RFC-Q4.2: burst-lane absorption gate + maker-pegged time exit, router CHOP-exit hysteresis + BURST cooldown,
stat-arb spread quote snapshots.  Synthetic prints / features, temp files; no network, no real ledger."""
import json

import pytest

from core import capital_router as cr
from core import engine as eng
from core import micro_signals as msig
from core.stat_arb_lane import StatArbLane
from tests.test_autonomy_self_learning import _engines
from tests.test_burst_lane import T0, book, setup, spike_up, trade, warm


# ============================================================================ absorption shield
def test_absorption_reports_price_impact_per_unit_flow():
    cfg = msig.config_for('SOLUSDT')                                   # usd_scale 0.1 -> $50k limit
    ab = msig.absorption({'cvd_15s': 200_000.0, 'px_vel_15s': 0.0}, cfg)
    assert ab['state'] == 'TRAPPED' and ab['blocks'] == 'BUY' and ab['lambda_bps_m'] == pytest.approx(0.0)
    ok = msig.absorption({'cvd_15s': 200_000.0, 'px_vel_15s': 0.10}, cfg)
    assert ok['state'] == 'CLEAR' and ok['lambda_bps_m'] == pytest.approx(10.0 / 2.0)  # 10 bps per scaled $2M
    assert msig.absorption({'cvd_15s': None, 'px_vel_15s': 0.1}, cfg)['state'] == 'UNKNOWN'


async def test_absorption_blocks_burst_entry_on_the_absorbed_side(tmp_path):
    lane, e, feed, venue, events, clk = setup(tmp_path, burst_absorb_gate=True)
    warm(feed, clk)
    spike_up(feed, clk)                                                # MR -> SELL at the ask
    e.features = {'cvd_15s': -400_000.0, 'px_vel_15s': 0.0}            # heavy selling, price not falling
    await lane.step()
    assert lane.last['burst'] and not lane.orders and 'ABSORPTION TRAP SELL' in lane.last['skip']


async def test_absorption_gate_off_or_other_side_still_enters(tmp_path):
    lane, e, feed, venue, events, clk = setup(tmp_path, burst_absorb_gate=False)
    warm(feed, clk)
    spike_up(feed, clk)
    e.features = {'cvd_15s': -400_000.0, 'px_vel_15s': 0.0}
    await lane.step()
    assert lane.orders and lane.orders[0]['side'] < 0 and lane.orders[0]['absorb'] == 'TRAPPED'
    lane2, e2, feed2, _, _, clk2 = setup(tmp_path / 'b', burst_absorb_gate=True)
    warm(feed2, clk2)
    spike_up(feed2, clk2)
    e2.features = {'cvd_15s': 400_000.0, 'px_vel_15s': 0.0}            # absorbed BUYING does not block a SELL
    await lane2.step()
    assert lane2.orders and lane2.orders[0]['side'] < 0


# ============================================================================ maker-pegged time exit
async def _short_then_hold(tmp_path, **kw):
    lane, e, feed, venue, events, clk = setup(tmp_path, **kw)
    warm(feed, clk)
    spike_up(feed, clk)
    await lane.step()
    trade(feed, 100.32, T0 + 1.5, False)
    clk.t = T0 + 2.0
    await lane.step()
    assert lane.pos and lane.pos['side'] < 0
    return lane, feed, clk


def _rows(tmp_path):
    f = tmp_path / 'micro_trades_shadow.jsonl'
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


async def test_maker_time_exit_never_dumps_at_the_old_taker_deadline(tmp_path):
    lane, feed, clk = await _short_then_hold(tmp_path, burst_time_exit='maker')
    clk.t = T0 + 63.0
    await lane.step()
    assert lane.exit_phase == 'maker' and any(o['kind'] == 'X' for o in lane.orders)
    clk.t = T0 + 74.0                                                  # 'taker' mode would have dumped here
    await lane.step()
    assert lane.pos is not None and not _rows(tmp_path)
    x = next(o for o in lane.orders if o['kind'] == 'X')
    trade(feed, x['px'] - 0.02, T0 + 75.0, True)                       # a seller trades through our exit bid
    clk.t = T0 + 75.5
    await lane.step()
    row = _rows(tmp_path)[-1]
    p = eng.PROFILES['demo']
    assert row['exit_how'] == 'MAKER-EXIT' and row['net_bps'] == pytest.approx(row['gross_bps'] - 2 * p.maker_bps)


async def test_maker_time_exit_caps_with_time_cap_taker(tmp_path):
    lane, feed, clk = await _short_then_hold(tmp_path, burst_time_exit='maker', burst_exit_cap_s=30.0)
    clk.t = T0 + 63.0
    await lane.step()
    clk.t = T0 + 94.0
    await lane.step()
    row = _rows(tmp_path)[-1]
    p = eng.PROFILES['demo']
    assert row['exit_how'] == 'TIME-CAP'
    assert row['net_bps'] == pytest.approx(row['gross_bps'] - p.maker_bps - p.taker_bps)


async def test_stop_loss_still_taker_in_maker_mode(tmp_path):
    lane, feed, clk = await _short_then_hold(tmp_path, burst_time_exit='maker')
    clk.t = T0 + 63.0
    await lane.step()
    book(feed, 100.63, 100.64, T0 + 63.5)
    trade(feed, 100.64, T0 + 63.6, False)                              # +34 bps against the short
    clk.t = T0 + 64.0
    await lane.step()
    assert _rows(tmp_path)[-1]['exit_how'] == 'SL' and lane.pos is None


def test_demo_profile_uses_maker_exit_and_gate_live_lane_off():
    d, live = eng.PROFILES['demo'], eng.PROFILES['live']
    assert d.burst_lane == 'shadow' and d.burst_time_exit == 'maker' and d.burst_absorb_gate
    assert live.burst_lane == 'off'
    assert eng.Profile('x', 'BTCUSDT').burst_time_exit == 'taker'      # default = the registered replay policy


# ============================================================================ router hysteresis
def _set(es, ker, cvd=0.0):
    for e in es.values():
        e.features = {'ker': ker, 'cvd_60s': cvd}


def _to_chop(es, a, c):
    _set(es, 0.10)
    a.route()
    c.t += 61
    assert a.route()['regime'] == 'CHOP'


def test_router_chop_exit_needs_sustained_ker(tmp_path):
    es, a, c = _engines(tmp_path)
    _to_chop(es, a, c)
    _set(es, 0.30)                                                     # above CHOP_KER, below CHOP_EXIT_KER
    c.t += 600
    assert a.route()['regime'] == 'CHOP'
    _set(es, 0.45)
    assert a.route()['regime'] == 'CHOP'
    c.t += 120
    _set(es, 0.30)                                                     # the run breaks -> clock restarts
    assert a.route()['regime'] == 'CHOP'
    _set(es, 0.45)
    a.route()
    c.t += cr.CHOP_EXIT_HOLD_S - 1
    assert a.route()['regime'] == 'CHOP'
    c.t += 1
    assert a.route()['regime'] == 'NORMAL'


def test_router_oscillating_tape_flips_far_less(tmp_path):
    es, a, c = _engines(tmp_path)
    switches, prev = 0, 'NORMAL'
    for k in range(230 * 12):                                          # 230 min of 5 s polls
        _set(es, 0.10 if (k // 24) % 2 == 0 else 0.45)                 # 2-min alternation chop / trend
        c.t += 5
        r = a.route()['regime']
        switches += r != prev
        prev = r
    assert switches == 1                                               # one entry into CHOP, no 2-min exits


def test_router_burst_cooldown_after_chop(tmp_path):
    es, a, c = _engines(tmp_path)
    _to_chop(es, a, c)
    _set(es, 0.60, cvd=5_000_000.0)                                    # BURST features
    a.route()
    c.t += cr.CHOP_EXIT_HOLD_S
    r = a.route()
    assert r['regime'] == 'NORMAL' and 'after CHOP' in r['why']
    c.t += cr.BURST_COOLDOWN_S - 61                                    # still inside the cooldown
    assert a.route()['regime'] == 'NORMAL'
    c.t += 61                                                          # cooldown over; BURST must persist 60 s
    a.route()
    c.t += 61
    assert a.route()['regime'] == 'BURST'


def test_chop_exit_unknown_ker_never_confirms():
    assert not cr.chop_exit_confirmed({'A': {'ker': None}, 'B': {}})
    assert cr.chop_exit_confirmed({'A': {'ker': 0.41}, 'B': {'ker': None}})


# ============================================================================ stat-arb quote snapshots
class _F:
    def __init__(self, p):
        self.p = p

    def price(self):
        return self.p


class _E:
    def __init__(self, p):
        self.feed = _F(p)

    def event(self, m):
        pass


async def test_stat_arb_writes_one_quote_per_minute_outside_trade_ledger(tmp_path):
    lane = StatArbLane({'SOLUSDT': _E(150.0), 'DOGEUSDT': _E(0.12)}, tmp_path)
    for i in range(130):
        await lane.step(now=1000.0 + i)
    rows = [json.loads(x) for x in (tmp_path / 'stat_arb_quotes.jsonl').read_text().splitlines()]
    assert [r['t'] for r in rows] == [1000.0, 1060.0, 1120.0]
    q = rows[-1]
    assert q['type'] == 'QUOTE' and q['pair_id'] == 'SOLUSDT_DOGEUSDT' and q['reason']
    assert q['spread_std_bps_min'] == 42.0
    assert {'z_score', 'z_entry', 'beta', 'spread_std_bps', 'half_life', 'px_a', 'px_b'} <= set(q)
    assert not (tmp_path / 'stat_arb_shadow.jsonl').exists()           # no entry -> trade ledger untouched
