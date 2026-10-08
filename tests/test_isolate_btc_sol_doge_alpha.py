"""BTC observe-only, SOL/DOGE size floor + larger super size, low-velocity 120 s eject (2026-10-08u).

The 41-row ledger is DEMO, mixed code versions; removing BTC in hindsight is an accounting identity on the
same sample, not a forecast. Behaviour tests only; not evidence of profitability."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from core import engine as eng
from core import micro_signals as msig
from core import micro_swarm as ms
from tests.test_chop_filter import chop_feed
from tests.test_multi_asset_scanner import SOL, fill_sol, mr_feed, scanner, sol_feed
from tests.test_two_mode_engine import btc_long_at, bullish_feed, mock_engine, sig

ROOT = Path(__file__).resolve().parents[1]
BTC = 'BTCUSDT'
OBS = {'observe_only': (BTC,)}
RELAX = dict(ker_gate=False, min_edge_bps=0.0, concordance_gate=False, min_velocity_pct=0.0, fee_hurdle=False)
BUY = ms.evaluate(sig(FLOW=('BUY', 1.0, 20), TREND=('BUY', 1.0, 20)), ms.Gates(cost_bps=4))[0]


def ledger():
    p = ROOT / 'data' / 'engine_trades.jsonl'
    rows = [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines() if x.strip()] if p.exists() else []
    if len(rows) < 41:
        pytest.skip('needs the 41-trade ledger')
    return rows[:41]


def orders(tmp_path):
    p = tmp_path / 'engine_orders.jsonl'
    return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines()] if p.exists() else []


# ============================================================================= ledger reconciliation
def test_ledger_41_by_symbol():
    rows = ledger()
    by = lambda s: [r for r in rows if r.get('symbol') == s]
    btc, sol, doge = by(BTC), by(SOL), by('DOGEUSDT')
    assert (len(btc), sum(r['pnl'] > 0 for r in btc)) == (28, 6)            # form: "28 losses of 29" -> 22 of 28
    assert sum(r['pnl'] for r in btc) == pytest.approx(-1.4999, abs=1e-4)
    assert sum(r['pnl'] for r in btc if r.get('tag') == 'AUTO') == pytest.approx(-1.4303, abs=1e-4)
    assert (len(sol), sum(r['pnl'] > 0 for r in sol)) == (10, 6)            # form: 9 trades
    assert sum(r['pnl'] for r in sol) == pytest.approx(0.2522, abs=1e-4)
    assert len(doge) == 3 and sum(r['pnl'] for r in doge) == pytest.approx(-0.0813, abs=1e-4)
    assert sum(r['pnl'] for r in sol + doge) == pytest.approx(0.1709, abs=1e-4)   # hindsight, same sample


def test_trade_41_was_the_btc_minimum_size_probe():
    r = ledger()[40]
    assert r['symbol'] == BTC and r['qty'] == pytest.approx(0.0007) and r['probe'].startswith('risk')
    assert r['reason'] == 'EJECT' and r['pnl'] == pytest.approx(-0.04679, abs=1e-5) and r['mfe_pct'] < 0.03


# ============================================================================= 1. BTC observe-only
async def test_btc_observer_computes_but_never_orders(tmp_path):
    e, venue, feed = mock_engine(tmp_path, **OBS)
    await e.sync()
    d = await e.tick()
    assert not d.allowed and d.reason.startswith('OBSERVER (no entries) · would be BUY')
    assert e.features and e.diag and e.radar()['label'] == 'OBSERVER'
    assert not venue.orders and not venue.fills and orders(tmp_path) == []


async def test_observer_refuses_direct_and_manual_entries(tmp_path):
    e, venue, feed = mock_engine(tmp_path, **OBS)
    await e.sync()
    for tag in ('AUTO', 'MANUAL'):
        res = await e.execute('BUY', BUY, 'SWARM', tag=tag)
        assert not res['ok'] and res['error'].startswith('OBSERVER')
    assert not venue.fills


async def test_observer_still_manages_and_closes_an_existing_position(tmp_path):
    e, venue, feed = mock_engine(tmp_path, **OBS)
    venue._fill(BTC, 'BUY', 0.002, feed.bid, venue.cfg.maker_bps)
    await e.sync()
    assert e.pos and e.pos['tag'] == 'ADOPTED'
    await e.close_now()
    assert e.pos is None and not venue.amt.get(BTC)


async def test_observer_runs_no_shadow_scalper(tmp_path):
    feed = chop_feed()
    e, venue, _ = mock_engine(tmp_path, feed=feed, ker_gate=True, chop_scalper='shadow', **OBS)
    await e.sync()
    f = feed.features()
    e.feed.features = lambda now=None, _f=f: {**_f, 'ofi_10s': 0.7}
    await e.tick()
    assert orders(tmp_path) == []


def test_only_btc_is_observer_by_default(tmp_path):
    p = eng.PROFILES['demo']
    assert p.observe_only == (BTC,)
    for sym, obs in ((BTC, True), (SOL, False), ('DOGEUSDT', False)):
        e, _, _ = mock_engine(tmp_path, observe_only=p.observe_only)
        e.p = replace(e.p, symbol=sym)
        assert e.observer is obs


async def test_scanner_trades_sol_while_btc_observes(tmp_path):
    feeds = {BTC: bullish_feed(), SOL: sol_feed()}
    es, venue, _ = await scanner(tmp_path, feeds, observe_only=(BTC,), **RELAX)
    await es[BTC].tick()
    d = await fill_sol(es[SOL], feeds[SOL], es[SOL].tick())
    assert d.allowed and es[SOL].pos and es[BTC].pos is None
    assert all(f['symbol'] == SOL for f in venue.fills)


# ============================================================================= 2. SOL / DOGE sizing
def test_size_tables():
    assert eng.ALT_SIZE_FLOOR == {SOL: 0.40, 'DOGEUSDT': 300.0}
    assert eng.SUPER_SIZE == {SOL: 0.80, 'DOGEUSDT': 500.0}
    sol_risk = 0.80 * 115.0 * (0.35 + 0.08) / 100                           # SL floor 0.35 % + fees
    doge_risk = 500 * 0.0885 * (0.50 + 0.08) / 100
    assert sol_risk + doge_risk < 1.0                                         # both at max fit the portfolio cap


async def test_normal_sol_entry_uses_the_040_floor(tmp_path):
    feeds = {BTC: mr_feed(), SOL: sol_feed()}
    es, venue, _ = await scanner(tmp_path, feeds, alt_size_floor=True, **RELAX)
    sol = es[SOL]
    d = await fill_sol(sol, feeds[SOL], sol.tick())
    await sol.sync()
    assert d.allowed and sol.pos['qty'] == pytest.approx(0.40) and sol.risk_at_stop() < 0.40


async def test_floor_off_keeps_the_parity_size(tmp_path):
    feeds = {BTC: mr_feed(), SOL: sol_feed()}
    es, venue, _ = await scanner(tmp_path, feeds, alt_size_floor=False, **RELAX)
    sol = es[SOL]
    await fill_sol(sol, feeds[SOL], sol.tick())
    await sol.sync()
    assert sol.pos and sol.pos['qty'] < 0.40


async def test_probe_halves_the_floored_size(tmp_path):
    feeds = {BTC: mr_feed(), SOL: sol_feed()}
    es, venue, _ = await scanner(tmp_path, feeds, alt_size_floor=True, **RELAX)
    sol = es[SOL]
    sol.drift_probe = 'DRIFT ACCEL BUY: test'
    await fill_sol(sol, feeds[SOL], sol.execute('BUY', BUY, 'SWARM'))
    assert sol.pos and sol.pos['qty'] == pytest.approx(0.20)


# ============================================================================= 3. 120 s low-velocity eject
@pytest.mark.parametrize('age,mfe,vel,extra,due', [
    (121, 0.05, 0.01, {}, True), (121, 0.05, -0.02, {}, True), (121, 0.05, 0.03, {}, False),
    (121, 0.05, None, {}, False), (121, 0.08, 0.01, {}, False), (119, 0.05, 0.01, {}, False),
    (121, 0.05, 0.01, {'eject_s': 90.0}, False), (121, 0.05, 0.01, {'tp1_filled': True}, False)])
def test_eject_fast_rule(tmp_path, age, mfe, vel, extra, due):
    e, _, _ = mock_engine(tmp_path, eject_fast=True)
    now = 1000.0
    p = {'opened': now - age, 'mfe_pct': mfe, 'tp1_filled': False, **extra}
    assert e.eject_fast_due(p, now, {'px_vel_15s': vel}) is due


async def test_eject_fast_exits_at_the_touch(tmp_path):
    e, venue, feed = await btc_long_at(tmp_path, 84_000.0, eject_fast=True)
    e.features = {**(e.features or {}), 'px_vel_15s': 0.01}
    e.pos['opened'] -= 121
    await e.manage()
    x = e.pos['exiting']
    assert x['reason'] == 'EJECT-FAST' and x['floor'] is None and x['px'] == pytest.approx(feed.ask)
    assert any('FAST EJECT' in ev['message'] for ev in e.events)


async def test_strong_velocity_keeps_the_180s_schedule(tmp_path):
    e, venue, feed = await btc_long_at(tmp_path, 84_000.0, eject_fast=True, eject=True)
    e.features = {**(e.features or {}), 'px_vel_15s': 0.05}
    e.pos['opened'] -= 121
    await e.manage()
    assert not e.pos.get('exiting')


def test_eject_replay_shows_no_mechanical_cost():
    p = ROOT / 'docs' / 'research_upgrade' / 'replay_eject_insample.json'
    if not p.exists():
        pytest.skip('replay artefact not present')
    d = json.loads(p.read_text(encoding='utf-8'))['all']
    for m in ('cons', 'opt'):
        lo, hi = d[m]['paired_B_minus_A']['ci95']
        assert lo < 0 < hi                                                     # neither better nor worse


def test_defaults():
    d, l = eng.PROFILES['demo'], eng.PROFILES['live']
    assert d.alt_size_floor and d.eject_fast and (d.eject_fast_s, d.eject_fast_mfe_pct, d.eject_fast_vel_pct) == (120.0, 0.08, 0.025)
    assert not l.alt_size_floor and not l.eject_fast and l.symbol == 'DOGEUSDT'
