"""11-09d noise elimination after the 21:33 / 21:34 Demo losses (docs/harness/plan_micro_noise.md): micro SL noise floor, TP sized
for net RR 1.40, KER >= 0.30 + triple alignment, and the micro halt counting only micro trades of the last 24 h.
Mock venue, synthetic books.  A wider stop and a stricter filter remove one specific way to lose; they add no edge."""
import json
import time
from dataclasses import replace

import pytest

from core import engine as eng
from core import exec_funnel as xf
from core import micro_signals as msig
from core import regime_classifier as rc
from core.position_sizer import MicroCapitalSizer
from tests.test_micro_rotation import BUY, DOGE, PX, SELL, SPEC, book, burst_engine, doge_micro, rows
from tests.test_two_mode_engine import mock_engine


def align_engine(tmp_path, **kw):
    """Micro engine WITH the KER / alignment filter (the shared test fixtures switch it off)."""
    return burst_engine(tmp_path, micro_align=True, **kw)

FEES = 0.08


# ============================================================================= what the 21:33 / 21:34 ledger rows show
def test_the_two_logged_losses_were_075_stops_in_a_mr_short_against_buying_flow():
    pepe_move = (0.00517140758097166 / 0.0051662 - 1) * 100
    xrp_move = (1.0943 / 1.0934 - 1) * 100
    assert pepe_move == pytest.approx(0.1008, abs=1e-3) and xrp_move == pytest.approx(0.0823, abs=1e-3)   # 0.075 % + slip
    assert 988 * 0.0051662 == pytest.approx(5.104, abs=1e-3) and 4.7 * 1.0934 == pytest.approx(5.139, abs=1e-3)


# ============================================================================= task 1 / 2: bracket
def test_the_noise_floor_brackets_of_the_micro_mode(tmp_path):
    e = burst_engine(tmp_path)
    sl, tp = e.micro_bracket(0.10, 0.29, {})                              # the MR fixed bracket, unscaled
    assert (sl, tp) == (pytest.approx(0.22), pytest.approx(0.46))
    assert (tp - 0.04) / (sl + FEES) == pytest.approx(1.40)               # net RR 1.40 with maker TP and taker stop
    assert e.micro_bracket(0.075, 0.2175, {}) == (pytest.approx(0.22), pytest.approx(0.46))   # after SIDEWAYS x0.75
    sl, tp = e.micro_bracket(0.10, 0.29, {'atr_pct': 0.30})               # ATR 1 m 0.30 %: 1.2 x ATR beats the 0.22 floor
    assert sl == pytest.approx(0.36) and tp == pytest.approx(max(1.4 * 0.44 + 0.04, 1.8 * 0.30))
    assert e.micro_bracket(0.10, 0.29, {'atr_pct': 0.05})[0] == pytest.approx(0.22)    # a quiet tape keeps the absolute floor
    assert e.micro_bracket(0.50, 1.25, {}) == (0.50, 1.25)                # a wider bracket (DOGE asset floor) is left alone
    sl, tp = e.micro_bracket(0.15, 0.32, {}, scalp=True)                  # scalp lane: noise floor yes, RR 1.10 not 1.40
    assert sl == pytest.approx(0.22) and tp == pytest.approx((0.22 + FEES) * 1.10 + 0.04)


def test_the_directive_numbers_do_not_reach_rr_1_40_but_the_sized_ones_do():
    """TP 0.38 % at SL 0.22 % is RR 1.13 (break-even win rate 47 %), not 1.40; RR 1.40 at SL 0.22 needs TP 0.46 %, at SL 0.20 0.432 %."""
    assert (0.38 - 0.04) / (0.22 + FEES) == pytest.approx(1.133, abs=1e-3)
    assert 1.40 * (0.22 + FEES) + 0.04 == pytest.approx(0.46) and 1.40 * (0.20 + FEES) + 0.04 == pytest.approx(0.432)
    assert 1 / (1 + 1.40) == pytest.approx(0.4167, abs=1e-3)
    # a driftless tape: P(TP first) = SL / (SL + TP) = 0.22 / 0.68 = 32 % < 41.7 % needed -> the bracket cannot create edge
    assert 0.22 / (0.22 + 0.46) < 1 / (1 + 1.40)


def test_loss_budget_of_the_floor_bracket_is_far_inside_the_0_05_cap():
    s = MicroCapitalSizer()
    for n in (5.10, 5.25, 6.0):
        assert s.loss_at_stop(n, 0.22, FEES) == pytest.approx(n * 0.0030) and s.loss_at_stop(n, 0.22, FEES) < 0.02
    assert 5.1 * 0.0025 + 0.0031 == pytest.approx(0.0159, abs=1e-4)       # the directive's own arithmetic for SL 0.25 %
    assert s.risk_notional(0.22, FEES) == pytest.approx(0.05 / 0.0030)    # compounding ceiling at this stop: 16.7 U


def test_bracket_plan_applies_the_floor_in_micro_mode_only(tmp_path):
    e = burst_engine(tmp_path, mr_bracket='fixed')
    bp = e.bracket_plan('MR', {})
    assert (bp['sl_pct'], bp['tp_pct']) == (pytest.approx(0.22), pytest.approx(0.46))
    assert bp['rr_net'] == pytest.approx(1.40) and bp['be_wr'] == pytest.approx(0.4167, abs=1e-3)
    big = burst_engine(tmp_path / 'b', wallet=5000.0, mr_bracket='fixed')          # the 5 000 U demo account
    assert (big.bracket_plan('MR', {})['sl_pct'], big.bracket_plan('MR', {})['tp_pct']) == (0.10, 0.29)
    off = burst_engine(tmp_path / 'c', mr_bracket='fixed', micro_bracket=False)
    assert off.bracket_plan('MR', {})['sl_pct'] == 0.10
    live = burst_engine(tmp_path / 'd', mr_bracket='fixed', max_loss_usdt=0.05)    # loss-budget SL (live): derived by plan()
    assert live.bracket_plan('SWARM', {})['sl_pct'] != 0.22


async def test_the_regime_multipliers_cannot_push_the_funnel_stop_back_under_the_floor(tmp_path):
    e, venue, feed = await doge_micro(tmp_path, regime_gate='on', mr_bracket='fixed', gtx_ttl_s=0.2)
    e.regime_state = rc.RegimeState(rc.SIDEWAYS, 'test', 0.0, rc.POLICIES[rc.SIDEWAYS], None, None, None, 1)
    await e.execute('BUY', BUY, 'MR')
    r = rows(tmp_path)[-1]
    assert r['bracket'] == [pytest.approx(0.22), pytest.approx(0.46)]      # SIDEWAYS x0.75 would have given 0.165 / 0.345 ...
    assert r['micro_bracket'] == '0.165/0.345->0.220/0.460'                 # x0.75 of the floor bracket went back to the floor
    assert r['qty_req'] * PX * (0.22 + FEES) / 100 <= 0.05


# ============================================================================= task 2 / 3: KER + triple alignment
def feats(ker=0.40, ofi=-0.80, cvd3m=-200_000.0):
    return {'ker': ker, 'ofi_10s': ofi, 'cvd_3m': cvd3m}


def test_both_logged_losses_would_have_been_blocked(tmp_path):
    e = align_engine(tmp_path)
    pepe = dict(feats(ker=0.1116, ofi=0.1497))                           # ledger: ker 0.11, ofi +0.15 for a SELL
    xrp = dict(feats(ker=0.3263, ofi=0.1820))                             # ledger: ker 0.33, ofi +0.18 for a SELL
    assert [g for g, _ in e.micro_entry_block('SELL', pepe)] == ['MICRO_KER', 'MICRO_ALIGN']
    assert [g for g, _ in e.micro_entry_block('SELL', xrp)] == ['MICRO_ALIGN']       # KER 0.33 was fine; the flow was not
    assert 'OFI +0.18 not <= -0.65' in e.micro_entry_block('SELL', xrp)[0][1]


@pytest.mark.parametrize('side, ker, ofi, cvd, ok', [
    ('SELL', 0.30, -0.65, -1.0, True), ('SELL', 0.299, -0.65, -1.0, False), ('SELL', 0.30, -0.64, -1.0, False),
    ('BUY', 0.30, +0.65, +1.0, True), ('BUY', 0.30, +0.64, +1.0, False), ('BUY', 0.50, +0.90, -1.0, False),
    ('SELL', 0.50, -0.90, 0.0, True)])                                    # CVD exactly 0 does not oppose
def test_boundaries(tmp_path, side, ker, ofi, cvd, ok):
    e = align_engine(tmp_path)
    assert (e.micro_entry_block(side, feats(ker, ofi, cvd)) == []) is ok


def test_macro_and_cvd_opposition_and_unknowns_block(tmp_path):
    e = align_engine(tmp_path)
    e.macro = {'name': 'UP', 'allow': ('BUY',)}
    assert 'macro 5m UP forbids SELL' in e.micro_entry_block('SELL', feats())[0][1]
    e.macro = {'name': 'MIXED', 'allow': ('BUY', 'SELL')}
    assert 'CVD 3m +50k opposes SELL' in e.micro_entry_block('SELL', feats(cvd3m=50_000.0))[0][1]
    assert e.micro_entry_block('SELL', feats(cvd3m=None))[0][1].endswith('CVD 3m unknown')
    assert e.micro_entry_block('SELL', {'ker': None, 'ofi_10s': None, 'cvd_3m': None})[0][1].startswith('MICRO KER —')
    tol = align_engine(tmp_path / 't', micro_cvd3m_usd=50_000.0)
    lim = 50_000.0 * tol.feed.cfg.usd_scale                                # the tolerance is scaled like every CVD threshold
    assert tol.micro_entry_block('SELL', feats(cvd3m=0.8 * lim)) == [] and tol.micro_entry_block('SELL', feats(cvd3m=1.2 * lim))


def test_the_filter_is_micro_only_and_switchable(tmp_path):
    assert align_engine(tmp_path / 'a', wallet=5000.0).micro_entry_block('SELL', feats(ker=0.0, ofi=0.9)) == []
    assert burst_engine(tmp_path / 'b', micro_align=False).micro_entry_block('SELL', feats(ker=0.0, ofi=0.9)) == []
    assert align_engine(tmp_path / 'c').micro_entry_block('HOLD', feats(ker=0.0)) == []
    p = eng.PROFILES['demo']
    assert (p.micro_ker_min, p.micro_ofi_min, p.micro_cvd3m_usd, p.micro_align) == (0.30, 0.65, 0.0, True)


def test_the_blockers_reach_the_why_no_trade_funnel(tmp_path):
    assert xf.GATE_CLASS['MICRO_KER'] == xf.GATE_CLASS['MICRO_ALIGN'] == xf.STAT
    e = align_engine(tmp_path)
    e.features = feats(ker=0.1, ofi=0.2)
    names = [g for g, _ in e.churn_checks(SELL, {'consensus': 0.9, 'edge_bps': 50.0})[0]]
    assert 'MICRO_KER' in names and 'MICRO_ALIGN' in names


async def test_every_auto_path_is_refused_in_the_funnel_and_a_qualified_setup_goes_through(tmp_path):
    e, venue, feed = await doge_micro(tmp_path, micro_align=True, gtx_ttl_s=0.2)
    orig = feed.features
    feed.features = lambda *a, **k: {**orig(*a, **k), 'ker': 0.11, 'ofi_10s': 0.15, 'cvd_3m': 1.0e5}   # the PEPE tape
    for path in ('MR', 'SWARM', 'SCALP'):
        res = await e.execute('SELL', SELL, path)
        assert not res['ok'] and res['error'].startswith('MICRO KER 0.11 < 0.3')
    assert e.funnel.outcomes['MICRO_KER'] == 3 and e.funnel.outcomes['MICRO_ALIGN'] == 3
    assert not [r for r in rows(tmp_path) if r.get('qty_req')] and not venue.fills
    feed.features = lambda *a, **k: {**orig(*a, **k), 'ker': 0.45, 'ofi_10s': -0.80, 'cvd_3m': -2.0e5}   # a real burst
    await e.execute('SELL', SELL, 'SWARM')
    assert [r for r in rows(tmp_path) if r.get('qty_req')]


# ============================================================================= task 4: the halt
def loss(t, micro=True, tag='AUTO', pnl=-0.008):
    return {'t': t, 'tag': tag, 'pnl': pnl, 'symbol': DOGE, 'mode': 'demo', **({'micro': True} if micro else {})}


def test_the_halt_at_21_34_counted_a_31_hour_old_loss_from_another_run(tmp_path):
    """Ledger tail: 11-08 14:36 BUY SCALP loss (code 2398e92, no micro rules) + the PEPE and XRP losses = 3 AUTO losses in a row."""
    now = time.time()
    e = burst_engine(tmp_path)
    stale = loss(now - 31 * 3600, micro=False)
    e.all_rows.extend([stale, loss(now - 120, micro=False), loss(now - 60, micro=False)])      # rows written by the old code
    assert e.micro_sizer().halt_until(e.all_rows, now) is not None        # the old rule: 3 in a row -> 24 h halt after 2 new losses
    assert e.micro_halt_until() is None                                   # now: only micro trades count
    e.all_rows[:] = [stale, loss(now - 120), loss(now - 60)]              # two real micro losses (+ the stale one)
    assert e.micro_halt_until() is None
    e.all_rows.append(loss(now - 10))                                     # the third micro loss in a row
    assert e.micro_halt_until() == pytest.approx(now - 10 + 86_400, abs=2)
    ok, why = e.gates({}).rune
    assert not ok and why.startswith('MICRO HALT: 3 losses in a row')


def test_losses_older_than_the_pause_window_do_not_join_the_streak(tmp_path):
    now = time.time()
    e = burst_engine(tmp_path)
    e.all_rows.extend([loss(now - 90_000), loss(now - 100), loss(now - 50)])        # 25 h, 100 s, 50 s ago
    assert e.micro_halt_until() is None
    e.all_rows.insert(1, loss(now - 80_000))                                        # a third loss inside 24 h
    assert e.micro_halt_until() == pytest.approx(now - 50 + 86_400, abs=2)
    s = MicroCapitalSizer()
    rows_ = [loss(now - 90_000), loss(now - 100), loss(now - 50)]
    assert s.halt_until(rows_, now) is not None and s.halt_until(rows_, now, lookback_s=86_400) is None   # default unchanged


async def test_trades_taken_under_the_micro_rules_carry_the_flag_into_the_ledger(tmp_path):
    feed = msig.MicroFeed(DOGE, msig.config_for(DOGE))
    book(feed, PX)
    orig = feed.features
    feed.features = lambda *a, **k: {**orig(*a, **k), 'ofi_10s': -0.94}
    e, venue, _ = mock_engine(tmp_path, feed=feed, mock_balance=5.0, micro_ioc='on', micro_align=False, gtx_ttl_s=0.3)
    e.p = replace(e.p, symbol=DOGE, qty=0.0)
    e.spec = dict(SPEC[DOGE])
    await e.sync()
    e.diag = {'consensus': 0.802}
    res = await e.execute('SELL', SELL, 'SWARM')
    assert res['ok'] and e.pos['micro'] is True
    await e.close_now()
    last = json.loads((tmp_path / 'trades.jsonl').read_text(encoding='utf-8').splitlines()[-1])
    assert last['micro'] is True and e.all_rows[-1]['micro'] is True
