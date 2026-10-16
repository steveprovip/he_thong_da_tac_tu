"""10-16 (c) RFC-Q4.4: burst hibernation hysteresis (one OBSERVE / one WAKE line), shadow carry book sizing, delta
and accrual, SW1 swing replay rule (offline, rejected), no lane promoted.  Synthetic data only; no network."""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from core import engine as eng
from core import funding_executor as fx
from tests.test_burst_lane import setup, spike_up, warm
from tests.test_chop_burst_gating_2026_10_16 import BN, HL

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import swing_replay_sw1 as sw  # noqa: E402


def _lane(tmp_path, **kw):
    lane, e, feed, venue, events, clk = setup(tmp_path, burst_chop_gate=True, **kw)
    warm(feed, clk)
    spike_up(feed, clk)
    e.autonomy = SimpleNamespace(router={'regime': 'NORMAL'})
    return lane, e, events


def _obs(events, word='OBSERVE'):
    return [m for m in events if m.startswith(f'BURST {word}')]


# ============================================================================ Task 1: log hygiene + hysteresis
async def test_ker_flicker_logs_one_observe_and_never_enters(tmp_path):
    lane, e, events = _lane(tmp_path)
    for k in (0.24, 0.30, 0.22, 0.35, 0.26, 0.39, 0.20) * 5:       # the 16:51 pattern: KER around 0.25
        e.features = {'ker': k}
        await lane.step()
        assert not lane.orders
    assert len(_obs(events)) == 1 and not _obs(events, 'WAKE') and lane.hibernating


async def test_wake_needs_router_normal_and_ker_040_with_one_wake_line(tmp_path):
    lane, e, events = _lane(tmp_path)
    e.autonomy.router = {'regime': 'CHOP'}
    e.features = {'ker': 0.45}
    await lane.step()
    assert lane.hibernating and not lane.orders                       # router CHOP alone keeps it asleep
    e.autonomy.router = {'regime': 'NORMAL'}
    e.features = {'ker': None}
    await lane.step()
    assert lane.hibernating                                           # UNKNOWN KER never wakes it
    e.features = {'ker': 0.41}
    await lane.step()
    await lane.step()
    assert not lane.hibernating and lane.orders
    assert len(_obs(events)) == 1 and _obs(events, 'WAKE') == ['BURST WAKE SOLUSDT: router NORMAL · KER 0.41 >= 0.4']


async def test_vpin_veto_inside_hibernation_adds_no_lines(tmp_path):
    lane, e, events = _lane(tmp_path, burst_vpin_veto_s=60.0)
    e.features = {'ker': 0.1}
    e.toxic_vpin, e.toxic_side = 1.00, 'SELL'
    clk_t = lane.clock()
    e.toxic_until = clk_t + e.p.vpin_pause_s
    for _ in range(20):
        await lane.step()
    assert len(_obs(events)) == 1 and not lane.orders
    assert not [m for m in events if 'VPIN' in m and m.startswith('BURST')]


# ============================================================================ Task 4: carry book
def test_carry_legs_are_delta_neutral_and_cost_is_charged_per_open():
    book = fx.ShadowCarryBook(4919.1996)
    book.step(fx.quotes_from(BN, HL, ('LINK', 'ETH')))
    n = book.notional_per_leg
    assert book.legs() == [('LINK', 'HL', -n), ('LINK', 'BN', n)]
    assert sum(x[2] for x in book.legs()) == 0.0
    assert book.opened == 1 and book.cost_usdt == pytest.approx(fx.HEDGE_ROUND_TRIP_BPS / 1e4 * n)
    add = book.accrue(28_800.0)                                       # one 8 h period at the 3.0 bps spread
    assert add == pytest.approx(3.0 / 1e4 * n) and book.est_accrued_usdt == pytest.approx(add)
    assert book.accrue(-5.0) == 0.0


async def test_funding_loop_resizes_from_synced_wallet_and_accrues(monkeypatch):
    clock = [1_000.0]
    monkeypatch.setattr(eng.time, 'time', lambda: clock[0])
    p = eng.PROFILES['demo']
    e0 = SimpleNamespace(account={}, p=p, event=lambda m: None)       # first pass: account not synced yet
    r = SimpleNamespace(engines={'SOLUSDT': e0}, carry_state=None)
    q = lambda: fx.quotes_from(BN, HL, ('LINK', 'ETH'))               # noqa: E731
    st = await eng.Runner.funding_loop(r, once=True, fetch=q)
    assert st['notional_per_leg'] == pytest.approx((p.mock_balance or 0) * 0.7 / 6, abs=0.01)
    e0.account = {'wallet': 4919.1996}
    clock[0] += 28_800.0
    st = await eng.Runner.funding_loop(r, once=True, fetch=q)
    n = 4919.1996 * 0.7 / 6
    assert st['notional_per_leg'] == pytest.approx(n, abs=0.01)       # was frozen at the mock size
    assert st['net_delta_usdt'] == 0.0 and st['legs'] == 2 and st['opened'] == 1
    assert st['est_accrued_usdt'] == pytest.approx(3.0 / 1e4 * n, abs=1e-4)
    assert st['est_net_usdt'] == pytest.approx(st['est_accrued_usdt'] - st['cost_usdt'], abs=1e-4)


# ============================================================================ Task 2: SW1 (offline, rejected)
def _klines(sign=1, spike=True, n=3000):
    t0 = 1_767_225_600.0                                              # 2026-01-01 00:00 UTC (hour aligned)
    i = np.arange(n, dtype=float)
    c = 100.0 * np.exp(sign * 1e-7 * i * i)                           # accelerating: MACD rising, TSMOM > 0
    qv = np.ones(n)
    if spike:
        qv[-15:] = 3.0                                                # last 15m bar: VR = 3 > 1.5
    return {'t': t0 + 60 * i, 'o': c, 'h': c * 1.0001, 'l': c * 0.9999, 'c': c, 'v': qv, 'qv': qv, 'tbq': qv / 2}


def test_sw1_signal_needs_alignment_and_volume_ratio():
    up = sw.signals(_klines(+1))
    assert up and up[-1][1] == 1 and up[-1][2] == 2999
    dn = sw.signals(_klines(-1))
    assert dn and dn[-1][1] == -1
    assert not sw.signals(_klines(+1, spike=False))


def test_sw1_fees_maker_entry_and_tp_vs_taker_entry():
    m = {k: np.array(v, float) for k, v in {
        't': [0, 60, 120, 180], 'o': [100, 100, 100.1, 100.1], 'h': [100, 100.05, 102.5, 102.5],
        'l': [100, 99.9, 100, 100], 'c': [100, 100, 102, 102], 'qv': [1] * 4}.items()}
    mk = sw.run_symbol(m, [(0, 1, 0)], {'entry': 'MK', 'tp_bps': 200.0, 'sl_bps': 80.0})[0]
    assert mk['exit'] == 'TP' and mk['net_bps'] == pytest.approx(mk['gross_bps'] - 2 * sw.MAKER)
    tk = sw.run_symbol(m, [(0, 1, 0)], {'entry': 'TK', 'tp_bps': 200.0, 'sl_bps': 80.0})[0]
    assert tk['net_bps'] == pytest.approx(tk['gross_bps'] - sw.TAKER - sw.MAKER)


def test_no_lane_promoted_by_this_rfc():
    d = eng.PROFILES['demo']
    assert d.burst_lane == 'shadow' and d.intraday_lane == 'shadow' and d.stat_arb_lane == 'shadow'
    assert not getattr(d, 'vpin_fade', False)                         # replay: fade win 23 %, net < 0
    assert eng.PROFILES['live'].burst_lane == 'off' and not eng.PROFILES['live'].funding_shadow
