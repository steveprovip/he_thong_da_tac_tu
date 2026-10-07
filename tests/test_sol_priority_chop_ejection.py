"""SOL priority / BTC chop isolation (2026-10-07o): per-asset KER gates (BTC >= 0.55, SOL/DOGE >= 0.42),
180 s time-decay ejection at fee-true breakeven, SOL/DOGE size boost to 0.70 SOL at swarm vote >= 90 %,
BTC risk reserve for the alts, per-coin realised PnL on the radar."""
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from core import engine as eng
from core import micro_signals as msig
from core import micro_swarm as ms
from tests.test_chop_filter import PX
from tests.test_multi_asset_scanner import SOL, fill_sol, scanner, sol_feed
from tests.test_two_mode_engine import (agg, book_at, btc_long_at, depth, lift, mock_engine, opened_short, sig)

ROOT = Path(__file__).resolve().parents[1]
ENTRY = 84_000.0
EJ = {'eject': True}
BUY_DEC = ms.evaluate(sig(FLOW=('BUY', 1.0, 20), TREND=('BUY', 1.0, 20)), ms.Gates(cost_bps=4))[0]
DIAG = {'consensus': 0.8, 'jev': 9.5, 'gates_ok': True, 'edge_bps': 40.0}


def at(pct, entry=ENTRY):
    return round(entry * (1 + pct / 100), 1)


def ker_tape(cfg, zig, now=None, sym='BTCUSDT'):
    """2 min climb of 0.36 % with a +zig zig-zag every 10 s (zig 47 -> KER ~0.48), then a $750k buy burst."""
    now = time.time() if now is None else now
    f = msig.MicroFeed(sym, cfg)
    for i in range(110):
        f.on_message(agg(PX - 300 + i * 300 / 110 + (zig if (i // 10) % 2 else 0), 0.02, maker=i % 2 == 0),
                     now - 120 + i)
    for i in range(30):
        f.on_message(agg(PX + 30.0, 0.3, maker=False), now - 10 + i * 0.3)
    for _ in range(10):
        f.on_message(depth(PX + 30.0, PX + 30.1, bq=6.0, aq=1.0), now)
    return f


def engine_for(tmp_path, sym, **prof):
    prof.setdefault('alt_boost', True)
    e, venue, feed = mock_engine(tmp_path, **prof)
    e.p = replace(e.p, symbol=sym, qty=eng.SYMBOL_QTY.get(sym, 0.0))
    e.spec = dict(eng.FALLBACK_SPECS[sym])
    e.sig_cfg = msig.config_for(sym)
    return e


def ker_gate(e, k):
    e.features = {**(e.features or {}), 'ker': k, 'px_vel_15s': 0.1}
    e.chop_locked = msig.chop_locked(k, True, e.sig_cfg)
    return e.churn_gate(BUY_DEC, DIAG)


# ============================================================================= 1. asset-specific KER
def test_ker_gates_per_asset():
    b, s, d = (msig.config_for(x) for x in ('BTCUSDT', SOL, 'DOGEUSDT'))
    assert (b.ker_lock, b.ker_unlock, b.mr_ker_max) == (0.55, 0.55, 0.0)            # BTC: clean tape or nothing
    assert (s.ker_lock, s.ker_unlock, s.mr_ker_max) == (0.38, 0.42, 0.38) == (d.ker_lock, d.ker_unlock, d.mr_ker_max)
    assert b.usd_scale == 1.0 and s.usd_scale == 0.1 and s.vpin_bucket_usd == pytest.approx(2.5e6)  # untouched


def test_btc_048_locked_sol_045_open(tmp_path):
    """Verification: BTC sideway KER 0.48 -> CHOP LOCKED; SOL KER 0.45 -> allowed."""
    btc, sol = engine_for(tmp_path, 'BTCUSDT', ker_gate=True), engine_for(tmp_path, SOL, ker_gate=True)
    ok, why = ker_gate(btc, 0.48)
    assert not ok and why == 'CHOP LOCKED KER 0.48 < 0.55'
    assert ker_gate(sol, 0.45) == (True, '')
    assert ker_gate(btc, 0.55) == (True, '') and not ker_gate(btc, 0.549)[0]
    assert not ker_gate(sol, 0.37)[0] and ker_gate(sol, 0.42)[0]


async def test_btc_048_tape_refused_on_a_real_tick(tmp_path):
    """Same tape, same swarm GO: the old 0.42 gate trades it, the BTC 0.55 gate does not."""
    feed = ker_tape(msig.config_for('BTCUSDT'), 47.0)
    k = feed.features()['ker']
    assert 0.45 < k < 0.55
    e, venue, _ = mock_engine(tmp_path, feed=feed, ker_gate=True)
    await e.sync()
    d = await e.tick()
    assert not d.allowed and d.reason.startswith('CHOP LOCKED KER 0.') and '< 0.55' in d.reason
    assert e.pos is None and not venue.fills and e.diag['side'] == 'BUY'
    assert e.radar()['label'] == 'CHOP'
    old = ker_tape(msig.SignalConfig(), 47.0)                                       # pre-2026-10-07o BTC gate
    assert not msig.chop_locked(old.features()['ker'], True, msig.SignalConfig())   # 0.48 >= 0.42 would trade


def test_btc_mr_lane_is_off():
    """BTC MR needs KER < mr_ker_max = 0: never. SOL keeps MR below 0.38 on the same features."""
    base = {'ker': 0.20, 'vwap_dev_pct': -0.15, 'range_pos': 0.05, 'range_lo': 100.0, 'range_hi': 100.30,
            'depth10_imb': 0.40, 'obi': 0.0, 'cvd_60s': 0.0, 'cvd_3m': 0.0, 'last': 99.9}
    assert msig.signals(base, msig.config_for(SOL))['MR']['side'] == 'BUY'
    assert msig.signals(base, msig.config_for('DOGEUSDT'))['MR']['side'] == 'BUY'
    assert msig.signals(base, msig.config_for('BTCUSDT'))['MR'] is None


# ============================================================================= 2. time-decay ejection 180 s
async def test_soak_180s_mfe_003_ejects_at_breakeven(tmp_path):
    """Verification: open 181 s, MFE +0.03 % -> post-only reduce at entry + 4 bps; fill -> net ~0."""
    e, venue, feed = await btc_long_at(tmp_path, ENTRY, **EJ)
    book_at(feed, at(0.03))
    await e.tick()
    assert not e.pos.get('exiting') and e.pos['mfe_pct'] == pytest.approx(0.03, abs=0.002)
    assert e.position_row()['eject_in_s'] > 170
    e.pos['opened'] -= 181
    await e.tick()
    x = e.pos['exiting']
    floor = eng.round_tick(ENTRY * (1 + 4e-4), e.spec['tick'], 'up')
    assert x['reason'] == 'EJECT-BE' and x['floor'] == pytest.approx(floor) and x['px'] == pytest.approx(floor)
    assert x['px'] > feed.ask                                                       # rests above the touch
    assert e.pos['sl'] == e.pos['sl0'] and any('TIME-DECAY EJECT' in ev['message'] for ev in e.events)
    assert e.snapshot()['position']['exiting']['floor'] == pytest.approx(floor)
    lift(feed, floor)
    await e.sync()
    h = e.history[0]
    assert h['reason'] == 'EJECT-BE' and h['taker_fills'] == 0 and h['eject_floor'] == pytest.approx(floor)
    assert abs(h['pnl']) < 0.002 and h['gross'] == pytest.approx(h['fee'], abs=0.002)   # fee-true breakeven


async def test_short_ejection_floor_is_mirrored(tmp_path):
    e, venue, feed = await opened_short(tmp_path, **EJ)
    entry = e.pos['entry']
    e.pos['opened'] -= 181
    await e.tick()
    x = e.pos['exiting']
    assert x['reason'] == 'EJECT-BE' and x['floor'] == pytest.approx(eng.round_tick(entry * (1 - 4e-4), 0.1, 'down'))
    assert x['px'] <= x['floor'] + 1e-9 and x['px'] < entry


async def test_be_order_never_chases_down_then_escalates_at_360s(tmp_path):
    e, venue, feed = await btc_long_at(tmp_path, ENTRY, **EJ)
    e.pos['opened'] -= 181
    await e.tick()
    floor = e.pos['exiting']['floor']
    book_at(feed, at(-0.05))
    e.pos['exiting']['posted'] -= 5
    await e.tick()
    assert e.pos['exiting']['px'] == pytest.approx(floor) and e.pos['exiting']['reprices'] == 0
    e.pos['opened'] -= 180                                                          # 361 s (10-07p: 360 s)
    e.pos['exiting']['posted'] -= 5
    await e.tick()
    x = e.pos['exiting']
    assert x['reason'] == 'EJECT' and x['floor'] is None and x['px'] < floor and x['reprices'] == 1
    assert any('EJECT escalate' in ev['message'] for ev in e.events)
    lift(feed, x['px'])
    await e.sync()
    h = e.history[0]
    assert h['reason'] == 'EJECT' and h['pnl'] < 0 and h['taker_fills'] == 0       # small maker loss, not SL


@pytest.mark.parametrize('age,pct,on', [(179, 0.03, True), (181, 0.09, True), (181, 0.03, False)])
async def test_no_ejection_young_or_worked_or_disabled(tmp_path, age, pct, on):
    e, venue, feed = await btc_long_at(tmp_path, ENTRY, eject=on)
    book_at(feed, at(pct))
    await e.tick()
    book_at(feed, at(0.0) + 3)
    e.pos['opened'] -= age
    await e.tick()
    assert not (e.pos or {}).get('exiting') or not e.pos['exiting']['reason'].startswith('EJECT')


def test_eject_due_rules(tmp_path):
    e, _, _ = mock_engine(tmp_path, eject=True)
    now = 1000.0
    p = {'opened': now - 180, 'mfe_pct': 0.059, 'tp1_filled': False}             # 10-07p: MFE < 0.06 %
    assert e.eject_due(p, now)
    assert not e.eject_due({**p, 'mfe_pct': 0.06}, now) and not e.eject_due({**p, 'opened': now - 179.9}, now)
    assert not e.eject_due({**p, 'tp1_filled': True}, now)
    e.p = replace(e.p, soft_exits=False)
    assert not e.eject_due(p, now)


# ============================================================================= 3. SOL priority scaling
def test_alt_cap_qty_and_notional(tmp_path):
    sol, doge, btc = (engine_for(tmp_path, s) for s in (SOL, 'DOGEUSDT', 'BTCUSDT'))
    assert sol.alt_cap(115.0) == pytest.approx(0.70)                                # base (no boost)
    assert sol.alt_cap(150.06) == pytest.approx(0.70)                               # $105 < $140 notional cap
    assert sol.alt_cap(250.0) == pytest.approx(0.56)                                # $140 notional binds
    assert doge.alt_cap(0.20) == pytest.approx(300.0) and btc.alt_cap(84_000.0) is None
    assert sol.alt_cap(115.0, boosted=True) == pytest.approx(0.70)                  # empty ledger: Kelly scale 0


def test_boost_needs_raw_vote_090(tmp_path):
    sol, btc = engine_for(tmp_path, SOL), engine_for(tmp_path, 'BTCUSDT')
    full = ms.evaluate(sig(FLOW=('BUY', 1.0, 20), TREND=('BUY', 1.0, 20), SURGE=('BUY', 1.0, 20)),
                       ms.Gates(cost_bps=4))[0]
    assert sol.alt_boosted(BUY_DEC, {'consensus': 1.0}) and not sol.alt_boosted(BUY_DEC, {'consensus': 0.8,
                                                                                         'jev': 9.9})
    assert not btc.alt_boosted(full, {'consensus': 1.0})
    sol.p = replace(sol.p, alt_boost=False)
    assert not sol.alt_boosted(BUY_DEC, {'consensus': 1.0})


async def test_sol_100pct_vote_sizes_up_to_the_cap(tmp_path):
    feeds = {'BTCUSDT': ker_tape(msig.config_for('BTCUSDT'), 47.0), SOL: sol_feed()}
    es, venue, _ = await scanner(tmp_path, feeds, alt_boost=True, ker_gate=False, min_edge_bps=0.0,
                                 concordance_gate=False, min_velocity_pct=0.0, fee_hurdle=False)
    sol = es[SOL]
    d = await fill_sol(sol, feeds[SOL], sol.tick())
    await sol.sync()
    p = sol.pos
    assert d.allowed and p['alt_boost'] and p['qty'] == pytest.approx(sol.alt_cap(p['entry'], True), abs=0.011)
    assert p['qty'] >= 0.70 - 1e-9 and p['qty'] * p['entry'] <= 140.0 + 1e-6
    assert any('ALPHA PRIORITY SOL' in ev['message'] for ev in sol.events)
    assert sol.risk_at_stop() < 0.85 and sol.position_row()['alt_boost']


async def test_every_sol_size_path_stops_at_the_cap(tmp_path):
    """Trade #34: MR parity at SL 0.08 % gave 0.81 SOL. The cap clips parity/Kelly/convex sizes too."""
    feeds = {SOL: sol_feed()}
    es, venue, _ = await scanner(tmp_path, {'BTCUSDT': ker_tape(msig.config_for('BTCUSDT'), 47.0), **feeds},
                                 risk_target_usdt=1.0, ker_gate=False, min_edge_bps=0.0, concordance_gate=False,
                                 min_velocity_pct=0.0, fee_hurdle=False)
    sol = es[SOL]
    await fill_sol(sol, feeds[SOL], sol.tick())
    await sol.sync()
    assert sol.pos and not sol.pos['alt_boost'] and sol.pos['qty'] == pytest.approx(sol.alt_cap(sol.pos['entry']))


def test_btc_keeps_a_reserve_for_the_alts(tmp_path):
    btc, sol = engine_for(tmp_path, 'BTCUSDT'), engine_for(tmp_path, SOL)
    assert btc.risk_cap() == pytest.approx(0.15) and sol.risk_cap() == pytest.approx(1.0)   # 10-07p: 85 % alts
    peer = engine_for(tmp_path, 'DOGEUSDT')
    peer.pos = {'qty': 500.0, 'entry': 0.2, 'sl': 0.1996, 'side': 'BUY', 'entry_fee_bps': 2.0}   # 0.28 U at stop
    for e in (btc, sol):
        e.risk.engines = [e, peer]
        e.account = {'wallet': 5000.0}
    pl = {'loss_at_sl': 0.45, 'margin': 20.0}
    assert peer.risk_at_stop() == pytest.approx(0.28)
    assert btc.portfolio_check(pl).startswith('PORTFOLIO RISK') and '> 0.15 U' in btc.portfolio_check(pl)
    assert sol.portfolio_check(pl) is None
    btc.p = replace(btc.p, alt_boost=False)
    assert btc.risk_cap() == 1.0 and btc.portfolio_check(pl) is None


# ============================================================================= 4. radar per-coin PnL
def test_radar_shows_per_coin_realised_pnl(tmp_path):
    sol, btc = engine_for(tmp_path, SOL), engine_for(tmp_path, 'BTCUSDT')
    rows = [{'symbol': 'BTCUSDT', 'pnl': -0.2556}, {'symbol': 'BTCUSDT', 'pnl': -0.3096},
            {'symbol': 'BTCUSDT', 'pnl': -0.2268}, {'symbol': SOL, 'pnl': 0.1024}, {'symbol': SOL, 'pnl': 0.2085},
            {'symbol': SOL, 'pnl': -0.0053}]
    for e in (sol, btc):
        e.all_rows = rows
    rs, rb = sol.radar(), btc.radar()
    assert rs['pnl'] == pytest.approx(0.3056) and rs['tag'] == 'PROFIT MAKER' and rs['n'] == 3 and rs['wins'] == 2
    assert rb['pnl'] == pytest.approx(-0.792) and rb['tag'] == 'BLEEDER' and rb['risk_cap'] == pytest.approx(0.15)
    assert rs['gross_win'] == pytest.approx(0.3109)
    json.dumps(rs)


def test_desk_renders_the_pnl_strip():
    js = (ROOT / 'web_ui' / 'desk.js').read_text(encoding='utf-8')
    css = (ROOT / 'web_ui' / 'style.css').read_text(encoding='utf-8')
    assert 'pnl-strip' in js and "(PROFIT MAKER)" in js and 'coin-pnl' in js and '.coin-pnl.win' in css
    assert 'EJECT ${' in js and 'sàn BE' in js and 'PRIORITY · ' in js


# ============================================================================= 5. defaults / ledger evidence
def test_defaults_demo_on_live_off():
    d, l = eng.PROFILES['demo'], eng.PROFILES['live']
    assert d.eject and (d.eject_s, d.eject_mfe_pct, d.eject_hard_s) == (180.0, 0.06, 360.0)   # 10-07p values
    assert d.alt_boost and d.alt_boost_score == 0.90 and d.alt_risk_share == 0.85
    assert not l.eject and not l.alt_boost and l.alt_risk_share == 0.0 and l.equity_cap == 3.85
    assert eng.ALT_SIZE['SOLUSDT'] == (0.70, 1.20, 140.0)


def test_ledger_34_trades_evidence():
    path = ROOT / 'data' / 'engine_trades.jsonl'
    rows = [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()] if path.exists() else []
    if len(rows) < 34:
        pytest.skip('needs the 34-trade ledger')
    rows = rows[:34]
    by = lambda s: [r for r in rows if r.get('symbol', 'BTCUSDT') == s]
    sol, btc = by(SOL), by('BTCUSDT')
    assert sum(r['pnl'] for r in sol) == pytest.approx(0.2275, abs=1e-3)          # net (all 7 SOL trades)
    assert sum(r['pnl'] for r in sol if r['pnl'] > 0) == pytest.approx(0.3298, abs=1e-3)   # the "+0.33 U" = wins
    assert sum(r['pnl'] for r in btc) < -1.4
    t28, t30, t31, t33 = rows[27], rows[29], rows[30], rows[32]
    assert sum(r['pnl'] for r in (t28, t30, t31)) == pytest.approx(-0.792, abs=1e-3)       # the "BTC -0.79 U"
    assert all(r['ker'] >= 0.55 for r in (t28, t30, t31))       # KER 0.55 would NOT have stopped them ...
    assert all(r['hold_s'] >= 180 and r['mfe_pct'] < 0.08 for r in (t28, t30, t31))   # ... the 180 s eject targets them
    assert t33['ker'] < 0.55 and rows[33]['qty'] > 0.70 and rows[33]['path'] == 'MR'   # #33 blocked, #34 capped
