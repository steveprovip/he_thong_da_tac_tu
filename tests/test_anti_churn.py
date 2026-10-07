"""Anti-churn (2026-10-07f): $2M flow exit + 30 s breathing room, churn governor, regime-flip exit, SOL/DOGE."""
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from core import engine as eng
from core import micro_signals as msig
from core import micro_swarm as ms
from tests.test_two_mode_engine import (book_at, dump, lift, mock_engine, opened, opened_short, past_breath, sig,
                                        super_feed)

LEDGER = Path(__file__).resolve().parents[1] / 'data' / 'engine_trades.jsonl'


# ----------------------------------------------------------------------------- defaults
def test_anti_churn_defaults():
    p, cfg = eng.PROFILES['demo'], msig.SignalConfig()
    assert cfg.flow_exit_usd == 2_000_000.0 and cfg.flow_exit_s == 15.0
    assert (p.flow_breath_s, p.flow_exit_trigger_mult, p.flow_exit_cap_mult) == (30.0, 2.5, 3.0)
    assert (p.max_trades_hour, p.cooldown_s, p.min_score, p.min_edge_bps) == (8, 30.0, 0.82, 30.0)
    assert p.regime_flip_exit and p.regime_flip_usd == 1_000_000.0
    live = eng.PROFILES['live']
    assert live.max_trades_hour <= 8 and live.cooldown_s >= 30.0 and live.max_loss_usdt == 0.05


def test_real_ledger_flow_exits_were_noise_under_2m():
    """The 17 demo trades of 2026-10-07: 15 FLOW exits, only 2 had >= $2M against them."""
    if not LEDGER.exists():
        pytest.skip('no ledger')
    rows = [json.loads(x) for x in LEDGER.read_text(encoding='utf-8').splitlines() if x.strip()][:17]
    if len(rows) < 17:
        pytest.skip('ledger shorter than the 17-trade sample')
    flow = [r for r in rows if 'FLOW' in (r.get('reason') or '')]
    small = [r for r in flow if (r.get('adverse_usd') or 0) < 2_000_000]
    assert len(flow) == 15 and len(small) == 13
    assert all((r.get('adverse_usd') or 0) >= 500_000 for r in flow)        # every one tripped the old $500k
    ts = sorted(r['t'] for r in rows)
    busiest = max(sum(1 for u in ts if t <= u < t + 3600) for t in ts)
    assert busiest == 14                                                      # 14 in one hour > the new cap of 8


# ----------------------------------------------------------------------------- pillar 1: $2M + 30 s
def test_flow_limit_floor_trigger_multiple_and_cap(tmp_path):
    e, _, _ = mock_engine(tmp_path)
    assert e.flow_limit({'cvd_exit_win': 500_000.0}) == 2_000_000.0            # floor
    assert e.flow_limit({'cvd_exit_win': -1_200_000.0}) == pytest.approx(3_000_000.0)   # 2.5 x trigger
    assert e.flow_limit({'cvd_exit_win': 9_000_000.0}) == 6_000_000.0          # cap 3 x floor
    sol = eng.Engine(replace(eng.PROFILES['demo'], symbol='SOLUSDT', qty=0.0), e.venue,
                     msig.MicroFeed('SOLUSDT', msig.config_for('SOLUSDT')), ledger=tmp_path / 's.jsonl')
    assert sol.flow_limit({}) == pytest.approx(200_000.0)                      # x usd_scale 0.1


async def test_500k_noise_does_not_cut_and_trade_reaches_tp1(tmp_path):
    """Simulation of the ledger pattern ($0.50-0.56M against us within seconds) - new rules keep the trade."""
    e, venue, feed = await opened(tmp_path)
    entry = e.pos['entry']
    assert e.pos['flow_limit'] >= 2_000_000.0
    dump(feed, entry - 1, 520_000)                                  # noise inside the breathing room
    await e.tick()
    assert not e.pos.get('exiting') and e.pos['adverse_usd'] >= 500_000 and e.pos['breathing']
    past_breath(e)
    dump(feed, entry - 1, 520_000)                                  # more noise after 30 s, still < $2M
    await e.tick()
    assert not e.pos.get('exiting') and not e.pos['breathing'] and e.pos['adverse_usd'] < e.pos['flow_limit']
    book_at(feed, round(entry * 1.004, 1))                          # +0.40 %: TP1 (+0.35 %) rests and fills
    await e.sync()
    await e.tick()
    assert e.pos and e.pos['tp1_filled'] and abs(e.view['amt']) == pytest.approx(0.001) and not e.history
    tp_fill = [f for f in venue.fills if f['price'] > entry][0]
    assert tp_fill['maker']


async def test_same_noise_under_the_old_500k_rule_cut_the_trade(tmp_path):
    e, venue, feed = await opened(tmp_path, flow_breath_s=0.0)
    e.pos['flow_limit'] = 500_000.0                                 # the pre-2026-10-07f setting
    entry = e.pos['entry']
    dump(feed, entry - 1, 520_000)
    await e.tick()
    assert e.pos['exiting']['reason'] == 'FLOW'                     # the 13 premature exits, reproduced


async def test_breathing_room_holds_big_flow_for_30s_then_exits(tmp_path):
    e, venue, feed = await opened(tmp_path, regime_flip_exit=False)
    entry, sl0 = e.pos['entry'], e.pos['sl0']
    dump(feed, entry - 1, 4_000_000)
    await e.tick()
    assert not e.pos.get('exiting') and e.pos['adverse_usd'] >= e.pos['flow_limit']   # breathing: held
    assert e.pos['sl'] == sl0                                       # the exchange hard SL is still live
    past_breath(e)
    await e.tick()
    assert e.pos['exiting']['reason'] == 'FLOW'
    assert e.position_row()['breath_left_s'] == 0.0


async def test_hard_sl_still_fires_inside_the_breathing_room(tmp_path):
    e, venue, feed = await opened(tmp_path)
    book_at(feed, round(e.pos['sl0'] - 5, 1))
    await e.sync()
    h = e.history[0]
    assert h['reason'] == 'SL' and h['hold_s'] < 30


# ----------------------------------------------------------------------------- pillar 2: churn governor
async def test_churn_gate_blocks_edge_below_30bps(tmp_path):
    e, venue, feed = mock_engine(tmp_path, min_edge_bps=30.0)      # trend feed: edge ~27 bps
    await e.sync()
    d = await e.tick()
    assert not d.allowed and d.reason.startswith('CHURN edge') and e.pos is None and not venue.fills
    assert e.snapshot()['governor']['min_edge_bps'] == 30.0


async def test_churn_gate_lets_super_momentum_through(tmp_path):
    e, venue, _ = mock_engine(tmp_path, feed=super_feed(), min_edge_bps=30.0)
    await e.sync()
    d = await e.tick()
    assert d.allowed and e.pos and e.diag['edge_bps'] >= 30 and e.pos['edge_bps'] >= 30


def test_churn_gate_score_floor(tmp_path):
    e, _, _ = mock_engine(tmp_path, min_edge_bps=30.0)
    d = ms.evaluate(sig(FLOW=('BUY', 1.0, 20), TREND=('BUY', 1.0, 20)), ms.Gates(cost_bps=12))[0]
    ok, _ = e.churn_gate(d, {'consensus': 0.80, 'jev': 9.0, 'gates_ok': True, 'edge_bps': 40.0})
    assert ok                                                        # JEV 9.0 lifts the score to 90 %
    ok, why = e.churn_gate(d, {'consensus': 0.80, 'jev': 7.5, 'gates_ok': True, 'edge_bps': 40.0})
    assert not ok and why.startswith('CHURN score 80.0%')
    ok, why = e.churn_gate(d, {'consensus': 0.80, 'jev': 9.9, 'gates_ok': False, 'edge_bps': 40.0})
    assert not ok                                                    # a vetoed gate never lifts the score


async def test_rune_caps_8_trades_per_hour_and_30s_cooldown(tmp_path):
    e, venue, feed = await opened(tmp_path)
    e.p = replace(e.p, cooldown_s=30.0)
    book_at(feed, round(e.pos['sl0'] - 5, 1))
    await e.sync()
    assert 29.0 < e.cooldown_until - time.time() <= 30.5
    assert 'cooldown' in e.gates(feed.features()).rune[1]
    e.cooldown_until = 0.0
    now = time.time()
    e.trade_times.clear()
    e.trade_times.extend([now - 60] * 8)
    rune = e.gates(feed.features()).rune
    assert not rune[0] and '8 trades/h cap' in rune[1]


# ----------------------------------------------------------------------------- pillar 3: regime-flip exit
async def test_short_vs_strong_uptrend_cvd_133m_exits_by_maker(tmp_path):
    """The SHORT that sat through STRONG UPTREND (CVD +1.33M): MARIN now closes it, even inside 30 s."""
    e, venue, feed = await opened_short(tmp_path)
    entry, sl0 = e.pos['entry'], e.pos['sl0']
    cvd0 = feed.features()['cvd_60s']
    dump(feed, entry, 1_330_000 - cvd0, sell=False)                 # buyers push CVD 60 s to +1.33M
    assert feed.features()['cvd_60s'] == pytest.approx(1_330_000, rel=0.01)
    await e.tick()
    x = e.pos['exiting']
    assert x['reason'] == 'REGIME' and e.pos['breathing'] and e.pos['sl'] == sl0
    assert x['px'] < feed.ask                                         # post-only BUY rests on the bid side
    assert any('REGIME-FLIP' in ev['message'] for ev in e.events)
    lift(feed, x['px'], sell_side=False)
    await e.sync()
    h = e.history[0]
    assert h['reason'] == 'REGIME' and h['taker_fills'] == 0 and h['exit_mode'] == 'maker'


async def test_long_vs_cvd_minus_1m_exits(tmp_path):
    e, venue, feed = await opened(tmp_path)
    cvd0 = feed.features()['cvd_60s']
    dump(feed, e.pos['entry'] - 1, cvd0 + 1_050_000)                # CVD 60 s -> -1.05M
    await e.tick()
    assert e.pos['exiting']['reason'] == 'REGIME'


def test_regime_flip_thresholds_and_symbol_scale(tmp_path):
    e, _, _ = mock_engine(tmp_path)
    short, long_ = {'side': 'SELL'}, {'side': 'BUY'}
    assert e.regime_flipped(short, {'cvd_60s': 1_330_000.0}) and not e.regime_flipped(short, {'cvd_60s': 999_000.0})
    assert e.regime_flipped(long_, {'cvd_60s': -1_000_000.0}) and not e.regime_flipped(long_, {'cvd_60s': 1e7})
    e.feed.cfg = msig.config_for('SOLUSDT')
    assert e.regime_flipped(short, {'cvd_60s': 100_000.0})          # SOL/DOGE: $1M x 0.1
    e.p = replace(e.p, regime_flip_exit=False)
    assert not e.regime_flipped(short, {'cvd_60s': 9e9})


async def test_regime_flip_hard_mode_closes_reduce_only(tmp_path):
    e, venue, feed = await opened(tmp_path, soft_exits=False)
    cvd0 = feed.features()['cvd_60s']
    dump(feed, e.pos['entry'] - 1, cvd0 + 1_200_000)
    await e.tick()
    await e.sync()
    assert e.pos is None and e.history[0]['reason'] == 'REGIME'


# ----------------------------------------------------------------------------- pillar 4: SOL / DOGE
@pytest.mark.parametrize('sym,px', [('SOLUSDT', 150.0), ('DOGEUSDT', 0.17)])
def test_alt_symbols_size_and_fee_math(sym, px):
    p = replace(eng.PROFILES['demo'], symbol=sym, qty=0.0)
    pl = eng.plan('BUY', px, eng.FALLBACK_SPECS[sym], p)
    assert pl['ok'] and 5.0 <= pl['notional'] < 8.0
    # fee % is identical on every coin: same move % -> same net %; smaller notional only shrinks BOTH sides
    btc_n, move, fee_bps = 0.002 * 85_000, 0.0010, 4.0
    for n in (btc_n, pl['notional']):
        net_pct = (n * move - n * fee_bps / 1e4) / n * 100
        assert net_pct == pytest.approx(0.06)
