"""Fee-hurdle exit gating (2026-10-07i): FLOW / REGIME exits wait outside +-0.08 % and >= 90 s when green;
entries need KER >= 0.48 and price velocity 15 s >= 0.04 % with the trade."""
import json
import time
from pathlib import Path

import pytest

from core import engine as eng
from core import micro_signals as msig
from core import micro_swarm as ms
from tests.test_two_mode_engine import (book_at, btc_long_at, dump, lift, mock_engine, opened_short, sig)
from tests.test_winrate_maximizer import flow_feed

ROOT = Path(__file__).resolve().parents[1]
ENTRY = 84_000.0
HURDLE = {'fee_hurdle': True}


def at(pct):
    return round(ENTRY * (1 + pct / 100), 1)


# ----------------------------------------------------------------------------- the gate itself
def test_hurdle_gate_band_and_hold(tmp_path):
    e, _, _ = mock_engine(tmp_path, **HURDLE)
    now = time.time()
    old, young = {'opened': now - 200}, {'opened': now - 40}
    assert e.hurdle_gate(old, 0.0049, now).startswith('FEE HURDLE')          # trades #20 / #22
    assert e.hurdle_gate(old, 0.0799, now) and e.hurdle_gate(old, -0.0799, now)
    assert e.hurdle_gate(old, 0.08, now) is None and e.hurdle_gate(old, -0.08, now) is None
    assert e.hurdle_gate(old, 0.0963, now) is None                             # trade #21 still exits
    assert e.hurdle_gate(young, 0.15, now).startswith('HOLD 40/90s')           # green -> 90 s minimum
    assert e.hurdle_gate(young, -0.10, now) is None                            # red past the band: no hold
    e.p = eng.replace(e.p, fee_hurdle=False)
    assert e.hurdle_gate(old, 0.0049, now) is None


def test_real_ledger_trades_20_21_22():
    path = ROOT / 'data' / 'engine_trades.jsonl'
    rows = [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()] if path.exists() else []
    if len(rows) < 22:
        pytest.skip('needs the 22-trade ledger')
    t20, t21, t22 = rows[19:22]
    pct = [(r['exit'] / r['entry'] - 1) * 100 * (1 if r['side'] == 'BUY' else -1) for r in (t20, t21, t22)]
    assert [t20['reason'], t21['reason'], t22['reason']] == ['REGIME', 'REGIME', 'FLOW']
    assert abs(pct[0]) < 0.08 and pct[1] >= 0.08 and abs(pct[2]) < 0.08   # #20/#22 held now, #21 unchanged
    assert t22['adverse_usd'] > 9e6                                        # the exit we now suppress fought $9.8M
    # the new entry rules would have refused all three (velocity < 0.04 %, #22 also KER 0.40 < 0.48)
    assert all(abs(r['px_vel_15s']) < 0.04 for r in (t20, t21, t22)) and t22['ker'] < 0.48


# ----------------------------------------------------------------------------- verification gate
async def test_buy_84000_tick_to_84005_flow_jerk_holds_until_008(tmp_path):
    """BUY 84,000 -> 84,005 (+0.006 %) and $4M is dumped on it: no exit. At +0.083 % the exit is released."""
    e, venue, feed = await btc_long_at(tmp_path, ENTRY, regime_flip_exit=False, **HURDLE)
    e.pos['opened'] -= 100
    book_at(feed, at(0.006))
    dump(feed, at(0.006), 4_000_000)
    await e.tick()
    p = e.pos
    assert not p.get('exiting') and p['adverse_usd'] >= p['flow_limit'] and p['hurdle'].startswith('FEE HURDLE')
    assert p['hurdle_blocks'] == 1 and p['hurdle_first_pct'] == pytest.approx(0.006, abs=0.001)
    await e.tick()
    assert not e.pos.get('exiting') and e.pos['hurdle_blocks'] == 1            # one block, not one per tick
    assert e.position_row()['hurdle'].startswith('FEE HURDLE')
    book_at(feed, at(0.083))
    await e.tick()
    assert e.pos['exiting']['reason'] == 'FLOW'
    lift(feed, e.pos['exiting']['px'])
    await e.sync()
    h = e.history[0]
    assert h['reason'] == 'FLOW' and h['pnl'] > 0 and h['hurdle_blocks'] == 1 and h['taker_fills'] == 0


async def test_red_side_waits_for_minus_008_then_exits(tmp_path):
    e, venue, feed = await btc_long_at(tmp_path, ENTRY, regime_flip_exit=False, **HURDLE)
    e.pos['opened'] -= 100
    book_at(feed, at(-0.05))
    dump(feed, at(-0.05), 4_000_000)
    await e.tick()
    assert not e.pos.get('exiting') and e.pos['hurdle'].startswith('FEE HURDLE -0.05')
    book_at(feed, at(-0.09))
    await e.tick()
    assert e.pos['exiting']['reason'] == 'FLOW' and e.pos['sl'] == e.pos['sl0']


async def test_green_trade_holds_90s_even_above_the_hurdle(tmp_path):
    e, venue, feed = await btc_long_at(tmp_path, ENTRY, regime_flip_exit=False, **HURDLE)
    e.pos['opened'] -= 40                                                      # breath over, < 90 s
    book_at(feed, at(0.10))
    dump(feed, at(0.10), 4_000_000)
    await e.tick()
    assert not e.pos.get('exiting') and e.pos['hurdle'].startswith('HOLD')
    e.pos['opened'] -= 60
    await e.tick()
    assert e.pos['exiting']['reason'] == 'FLOW'


async def test_red_trade_past_the_band_is_not_held_by_the_90s_rule(tmp_path):
    e, venue, feed = await btc_long_at(tmp_path, ENTRY, regime_flip_exit=False, **HURDLE)
    e.pos['opened'] -= 40
    book_at(feed, at(-0.10))
    dump(feed, at(-0.10), 4_000_000)
    await e.tick()
    assert e.pos['exiting']['reason'] == 'FLOW'


async def test_hard_sl_ignores_the_hurdle(tmp_path):
    e, venue, feed = await btc_long_at(tmp_path, ENTRY, **HURDLE)
    book_at(feed, round(e.pos['sl0'] - 5, 1))
    await e.sync()
    assert e.history[0]['reason'] == 'SL' and e.history[0]['hold_s'] < 90


async def test_regime_flip_exit_is_held_inside_the_band(tmp_path):
    e, venue, feed = await opened_short(tmp_path, flow_exit=False, **HURDLE)
    e.pos['opened'] -= 100
    cvd0 = feed.features()['cvd_60s']
    dump(feed, e.pos['entry'], 1_330_000 - cvd0, sell=False)
    await e.tick()
    e.pos['flip_since'] -= 21
    await e.tick()
    assert not e.pos.get('exiting') and e.pos['hurdle'].startswith('FEE HURDLE')
    assert any('REGIME exit HELD' in ev['message'] for ev in e.events)
    entry = e.pos['entry']
    book_at(feed, round(entry * 1.0009, 1))                                    # short is -0.09 %
    await e.tick()
    assert e.pos['exiting']['reason'] == 'REGIME'


async def test_marin_tiers_are_not_gated(tmp_path):
    """The hurdle only holds FLOW / REGIME. The +0.08 % micro-lock still exits on a pullback."""
    e, venue, feed = await btc_long_at(tmp_path, ENTRY, microbe_trigger_pct=0.08, microbe_lock_pct=0.03, **HURDLE)
    book_at(feed, at(0.09))
    await e.tick()
    assert e.pos['soft_kind'] == 'LOCK'
    book_at(feed, round(e.pos['soft_stop'] - 0.1, 1))
    await e.tick()
    assert e.pos['exiting']['reason'] == 'SOFT-LOCK'


# ----------------------------------------------------------------------------- entry quality
async def test_entry_needs_velocity_004_with_the_trade(tmp_path):
    e, venue, _ = mock_engine(tmp_path, feed=flow_feed(lead=30.0), min_velocity_pct=0.04)   # +0.030 %
    await e.sync()
    d = await e.tick()
    assert not d.allowed and d.reason.startswith('VELOCITY BUY') and not venue.fills
    e2, _, _ = mock_engine(tmp_path, feed=flow_feed(lead=60.0), min_velocity_pct=0.04)       # +0.060 %
    await e2.sync()
    e2.features = e2.feed.features()
    d2 = ms.evaluate(sig(FLOW=('BUY', 1.0, 3), TREND=('BUY', 1.0, 3)), ms.Gates())[0]
    assert e2.churn_gate(d2, {'consensus': 0.8, 'jev': 9.0, 'gates_ok': True, 'edge_bps': 40})[0]


def test_velocity_gate_is_directional_and_needs_data(tmp_path):
    e, _, _ = mock_engine(tmp_path, min_velocity_pct=0.04)
    buy = ms.evaluate(sig(FLOW=('BUY', 1.0, 3), TREND=('BUY', 1.0, 3)), ms.Gates())[0]
    sell = ms.evaluate(sig(FLOW=('SELL', 1.0, 3), TREND=('SELL', 1.0, 3)), ms.Gates())[0]
    diag = {'consensus': 0.8, 'jev': 9.0, 'gates_ok': True, 'edge_bps': 40}
    e.features = {'px_vel_15s': -0.05}
    assert e.churn_gate(sell, diag)[0] and not e.churn_gate(buy, diag)[0]
    e.features = {'px_vel_15s': None}
    assert e.churn_gate(sell, diag)[1].startswith('VELOCITY SELL 15s —')


def test_ker_band_038_042(tmp_path):                                       # 0.40/0.48 -> 0.38/0.42 (2026-10-07j)
    assert msig.SignalConfig().ker_unlock == 0.42 and msig.SignalConfig().ker_lock == 0.38
    assert msig.chop_locked(0.41, True) and not msig.chop_locked(0.41, False) and not msig.chop_locked(0.42, True)


# ----------------------------------------------------------------------------- telemetry / defaults
async def test_position_row_and_desk_show_the_hurdle(tmp_path):
    e, venue, feed = await btc_long_at(tmp_path, ENTRY, **HURDLE)
    book_at(feed, at(0.03))
    await e.tick()
    row = e.position_row()
    assert row['gross_pct'] == pytest.approx(0.03, abs=0.002) and row['hurdle_pct'] == 0.08 and row['fee_hurdle']
    json.dumps(e.snapshot(), default=str)
    html = (ROOT / 'web_ui' / 'index.html').read_text(encoding='utf-8')
    js = (ROOT / 'web_ui' / 'desk.js').read_text(encoding='utf-8')
    assert 'id="hurdle-chip"' in html and "'hurdle-chip'" in js
    assert 'HOLDING: FEE HURDLE' in js and 'PROFIT: UNLOCKED' in js


def test_defaults():
    p = eng.PROFILES['demo']
    assert p.fee_hurdle and (p.hurdle_pct, p.hurdle_min_hold_s, p.min_velocity_pct) == (0.08, 90.0, 0.025)
    assert eng.PROFILES['live'].fee_hurdle
