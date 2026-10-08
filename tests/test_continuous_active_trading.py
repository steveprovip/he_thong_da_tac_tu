"""Opportunity starvation (2026-10-08t): what actually kept the desk idle, and the two proposed engines.

Fixed (OBSERVED in the 03:26-04:12 UTC run): VFR used the demo venue's own testnet spread (13 bps on SOL ->
VFR 1.9, frozen; mainnet spread 0.86 bps), and the BTC probe was a fixed 0.0001 BTC below the venue's $50
minimum, so BTC could never trade. Added: DRIFT acceleration probe (half size) and the chop micro-scalper,
which ships in SHADOW because the bookTicker replay loses 6-7.5 bps per scalp after fees (holdout CI < 0).
Behaviour tests only; not evidence of profitability."""
import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from core import engine as eng
from core import micro_signals as msig
from core import micro_swarm as ms
from tests.test_chop_filter import chop_feed
from tests.test_two_mode_engine import bullish_feed, fill_through, mock_engine, sig
from tests.test_world_quant_economic_engine import seed

ROOT = Path(__file__).resolve().parents[1]
BUY = ms.evaluate(sig(FLOW=('BUY', 1.0, 20), TREND=('BUY', 1.0, 20)), ms.Gates(cost_bps=4))[0]
SELL = ms.evaluate(sig(FLOW=('SELL', 1.0, 20), TREND=('SELL', 1.0, 20)), ms.Gates(cost_bps=4))[0]
DIAG = {'consensus': 0.9, 'jev': 9.5, 'gates_ok': True, 'edge_bps': 40.0}
SCALP_F = {'ker': 0.20, 'spread_bps': 0.5, 'ofi_10s': 0.6}


def orders(tmp_path):
    p = tmp_path / 'engine_orders.jsonl'
    return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines()] if p.exists() else []


def engine_for(tmp_path, sym, **prof):
    e, venue, feed = mock_engine(tmp_path, **prof)
    e.p = replace(e.p, symbol=sym, qty=eng.SYMBOL_QTY.get(sym, 0.0))
    e.spec = dict(eng.FALLBACK_SPECS[sym])
    e.sig_cfg = e.feed.cfg = msig.config_for(sym)
    return e


# ============================================================================= starvation causes (fixed)
def test_vfr_uses_the_signal_market_spread_not_the_demo_book(tmp_path):
    """04:12 UTC: SOL ATR15m 0.320 %, demo book spread 13 bps -> VFR 1.88 frozen; mainnet spread 0.86 bps."""
    e = engine_for(tmp_path, 'SOLUSDT', vfr_gate=True)
    e.venue.own_book = True
    e.vbook, e.vbook_at = {'bid': 115.23, 'ask': 115.38}, time.monotonic()        # ~13 bps testnet book
    e.features = {'atr15_pct': 0.320, 'atr15_src': '15m', 'spread_bps': 0.86}
    v = e.vfr_view()
    assert v['spread_bps'] == pytest.approx(0.86) and v['vfr'] == pytest.approx(32.0 / 4.86, abs=0.01)
    assert not v['frozen'] and 32.0 / (4 + 13.0) < 3.0                             # the old reading froze it


def test_vfr_falls_back_to_the_book_without_a_feed_spread(tmp_path):
    e = engine_for(tmp_path, 'SOLUSDT', vfr_gate=True)
    e.features = {'atr15_pct': 0.32, 'atr15_src': '15m'}
    assert e.vfr_view()['vfr'] is not None


def demo_btc_spec():
    return {'step': 0.0001, 'min_qty': 0.0001, 'min_notional': 50.0, 'tick': 0.1}     # Binance demo BTCUSDT


async def test_btc_probe_trades_the_smallest_size_the_venue_accepts(tmp_path):
    """04:06 UTC: 'BTC MICRO-PROBE 0.0001 ... min qty 0.0001 / $50 -> bỏ lệnh' on every BTC signal."""
    feed = bullish_feed()
    seed(feed, 0.05)                                                                   # ATR -> SL floor 0.15 %
    e, venue, _ = mock_engine(tmp_path, feed=feed, alt_boost=True)
    e.spec = demo_btc_spec()
    e.vfr_view = lambda f=None: {'vfr': 4.0, 'frozen': False, 'why': ''}
    await e.sync()
    d = await fill_through(e, feed, e.tick())
    p = e.pos
    assert d.allowed and p and p['qty'] == pytest.approx(0.0006) and p['qty'] * p['entry'] >= 50.0
    assert p['probe'].startswith('VFR 4.00') and e.risk_at_stop() <= 0.15 + 1e-9


async def test_btc_probe_still_refused_when_the_minimum_overruns_the_budget(tmp_path):
    e, venue, feed = mock_engine(tmp_path, alt_boost=True)                         # FALLBACK: $100 min, SL 0.20
    e.vfr_view = lambda f=None: {'vfr': 4.0, 'frozen': False, 'why': ''}
    await e.sync()
    await e.tick()
    assert e.pos is None and not venue.fills
    msg = next(x['message'] for x in e.events if 'MICRO-PROBE' in x['message'])
    assert 'dưới sàn' in msg and 'size nhỏ nhất 0.002' in msg and '> 0.15 U' in msg


# ============================================================================= drift acceleration probe
@pytest.mark.parametrize('sym,c60,c3,side,ok', [
    ('BTCUSDT', 4.51e6, -0.52e6, 'BUY', True), ('BTCUSDT', 4.51e6, -0.52e6, 'SELL', False),
    ('BTCUSDT', 1.5e6, -0.1e6, 'BUY', False), ('BTCUSDT', 2.5e6, 1.0e6, 'BUY', False),
    ('DOGEUSDT', 0.451e6, -0.052e6, 'BUY', True), ('BTCUSDT', -3e6, 0.5e6, 'SELL', True)])
def test_drift_acceleration_rule(tmp_path, sym, c60, c3, side, ok):
    e = engine_for(tmp_path, sym, drift_accel=True)
    e.features = {'cvd_60s': c60, 'cvd_3m': c3}
    r = e.drift_acceleration(side)
    assert bool(r) is ok and (not r or r.startswith(f'DRIFT ACCEL {side}'))


def test_drift_lock_is_lifted_only_for_the_accelerating_side(tmp_path):
    e = engine_for(tmp_path, 'BTCUSDT', drift_accel=True, drift_lock=True)
    e.reg = {'name': 'DRIFT', 'label': 'DRIFT (60s/3m CONFLICT · WAIT)', 'why': 'test'}
    e.features = {'cvd_60s': 4.51e6, 'cvd_3m': -0.52e6, 'px_vel_15s': 0.1, 'ker': 0.6}
    e.chop_locked = False
    e.drift_probe = None
    ok, why = e.churn_gate(BUY, DIAG)
    assert ok and e.drift_probe.startswith('DRIFT ACCEL BUY')
    e.drift_probe = None
    ok, why = e.churn_gate(SELL, DIAG)
    assert not ok and why.startswith('DRIFT LOCK') and e.drift_probe is None


def test_drift_lock_unchanged_when_disabled(tmp_path):
    e = engine_for(tmp_path, 'BTCUSDT', drift_accel=False, drift_lock=True)
    e.reg = {'name': 'DRIFT', 'label': 'DRIFT', 'why': ''}
    e.features = {'cvd_60s': 9e6, 'cvd_3m': 0.0}
    assert e.churn_gate(BUY, DIAG)[1].startswith('DRIFT LOCK')


async def test_drift_probe_opens_half_size_and_is_ledgered(tmp_path):
    e, venue, feed = mock_engine(tmp_path, qty=0.004)
    await e.sync()
    e.drift_probe = 'DRIFT ACCEL BUY: test'
    await fill_through(e, feed, e.execute('BUY', BUY, 'SWARM'))
    assert e.pos and e.pos['qty'] == pytest.approx(0.002) and e.pos['drift_probe'].startswith('DRIFT ACCEL')
    await e.close_now()
    row = json.loads((tmp_path / 'trades.jsonl').read_text(encoding='utf-8').splitlines()[-1])
    assert row['drift_probe'].startswith('DRIFT ACCEL BUY')


async def test_probe_never_goes_below_the_venue_minimum(tmp_path):
    e, venue, feed = mock_engine(tmp_path, qty=0.002, drift_probe_mult=0.1)
    await e.sync()
    e.drift_probe = 'DRIFT ACCEL BUY: test'
    await fill_through(e, feed, e.execute('BUY', BUY, 'SWARM'))
    assert e.pos and e.pos['qty'] >= e.spec['min_qty'] and e.pos['qty'] * e.pos['entry'] >= 100.0


# ============================================================================= chop micro-scalper
@pytest.mark.parametrize('f,side', [
    (SCALP_F, 'BUY'), ({**SCALP_F, 'ofi_10s': -0.6}, 'SELL'), ({**SCALP_F, 'ker': 0.45}, None),
    ({**SCALP_F, 'spread_bps': 2.0}, None), ({**SCALP_F, 'ofi_10s': 0.1}, None), ({**SCALP_F, 'ofi_10s': None}, None),
    ({**SCALP_F, 'ker': None}, None)])
def test_scalp_candidate(tmp_path, f, side):
    e, _, _ = mock_engine(tmp_path)
    assert e.scalp_candidate(f)[0] == side


def test_scalp_bracket_and_its_honest_rr(tmp_path):
    """TP 0.08 % / SL 0.12 %: fee-true RR (0.08 - 0.04) / (0.12 + 0.08) = 0.20 -> needs ~83 % winners."""
    e, _, _ = mock_engine(tmp_path)
    bp = e.bracket_plan('SCALP', {})
    assert (bp['tp_pct'], bp['sl_pct']) == (0.08, 0.12) and bp['profile'].tp1_frac == 1.0
    assert bp['rr_net'] == pytest.approx(0.20, abs=1e-3) and bp['be_wr'] == pytest.approx(0.833, abs=1e-3)


async def test_shadow_scalper_logs_without_trading(tmp_path):
    e, venue, feed = mock_engine(tmp_path, chop_scalper='shadow')
    await e.sync()
    f = {**feed.features(), **SCALP_F}
    await e.chop_scalp(BUY, f)
    await e.chop_scalp(BUY, f)                                                       # throttled (20 s)
    rows = [o for o in orders(tmp_path) if o['outcome'] == 'SHADOW_SCALP']
    assert len(rows) == 1 and rows[0]['side'] == 'BUY' and rows[0]['touch'] == pytest.approx(feed.bid)
    assert not venue.fills and not [o for o in venue.orders.values() if o['kind'] == 'ENTRY']


async def test_scalper_off_does_nothing(tmp_path):
    e, venue, feed = mock_engine(tmp_path, chop_scalper='off')
    await e.sync()
    await e.chop_scalp(BUY, {**feed.features(), **SCALP_F})
    assert orders(tmp_path) == [] and not venue.fills


async def test_scalper_respects_rune(tmp_path):
    e, venue, feed = mock_engine(tmp_path, chop_scalper='shadow')
    await e.sync()
    e.halted = 'KILL SWITCH'
    await e.chop_scalp(BUY, {**feed.features(), **SCALP_F})
    assert orders(tmp_path) == []


async def test_scalper_on_opens_a_half_size_maker_scalp(tmp_path):
    e, venue, feed = mock_engine(tmp_path, chop_scalper='on', net_rr_min=1.30, qty=0.004)
    await e.sync()
    await fill_through(e, feed, e.chop_scalp(BUY, {**feed.features(), **SCALP_F}))
    p = e.pos
    assert p and p['path'] == 'SCALP' and p['qty'] == pytest.approx(0.002) and not p['split']
    assert p['tp'] == pytest.approx(p['entry'] * 1.0008, abs=0.2) and p['sl'] == pytest.approx(p['entry'] * 0.9988, abs=0.2)
    assert all(f['maker'] for f in venue.fills) and p['eject_s'] == 90.0


async def test_scalp_is_ejected_at_90s(tmp_path):
    e, venue, feed = mock_engine(tmp_path, chop_scalper='on', qty=0.004)
    await e.sync()
    await fill_through(e, feed, e.chop_scalp(BUY, {**feed.features(), **SCALP_F}))
    e.pos['opened'] -= 91
    await e.sync()
    assert e.pos['exiting']['reason'] == 'EJECT-BE'


async def test_no_scalp_while_a_position_is_open(tmp_path):
    e, venue, feed = mock_engine(tmp_path, chop_scalper='shadow')
    await e.sync()
    e.pos = {'side': 'BUY'}
    await e.chop_scalp(BUY, {**feed.features(), **SCALP_F})
    assert orders(tmp_path) == []


async def test_tick_routes_a_locked_chop_book_to_the_shadow_scalper(tmp_path):
    feed = chop_feed()                                                               # KER < 0.40, flow burst
    e, venue, _ = mock_engine(tmp_path, feed=feed, ker_gate=True, chop_scalper='shadow')
    await e.sync()
    f = feed.features()
    assert f['ker'] < 0.40
    e.feed.features = lambda now=None, _f=f: {**_f, 'ofi_10s': 0.7}               # OFI frames not in this tape
    d = await e.tick()
    assert not d.allowed and d.reason.startswith('CHOP LOCKED')
    assert [o['outcome'] for o in orders(tmp_path)] == ['SHADOW_SCALP'] and not venue.fills


def test_replay_evidence_behind_the_shadow_default():
    p = ROOT / 'docs' / 'research_upgrade' / 'replay_chop_scalper_holdout_20260908.json'
    if not p.exists():
        pytest.skip('replay artefact not present')
    d = json.loads(p.read_text(encoding='utf-8'))['all']
    for m in ('cons', 'opt'):
        assert d[m]['mean_net_bps'] < 0 and d[m]['net_ci95_block']['ci95'][1] < 0


# ============================================================================= multiplexing / defaults
async def test_a_locked_symbol_returns_fast_and_holds_no_slot(tmp_path):
    e, venue, _ = mock_engine(tmp_path, feed=chop_feed(), ker_gate=True)
    await e.sync()
    t0 = time.perf_counter()
    d = await e.tick()
    assert not d.allowed and (time.perf_counter() - t0) < 0.1 and not e.busy and e.pos is None
    assert e.slot_block() is None


def test_defaults_demo_and_live():
    d, l = eng.PROFILES['demo'], eng.PROFILES['live']
    assert d.chop_scalper == 'shadow' and d.drift_accel and d.btc_probe_min_tradable
    assert (d.scalp_tp_pct, d.scalp_sl_pct, d.scalp_be_s, d.scalp_ker_max, d.scalp_spread_max_bps) == (0.08, 0.12, 90.0, 0.40, 1.5)
    assert (d.drift_accel_ratio, d.drift_accel_min_usd, d.drift_probe_mult) == (3.0, 2_000_000.0, 0.5)
    assert l.chop_scalper == 'off' and not l.drift_accel
