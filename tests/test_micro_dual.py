"""11-09e dual-mode micro engine (docs/harness/plan_micro_dual.md): CHOP harvester vs TREND sniper split at KER 0.28, the lagging
CVD 3 m turned into a size penalty, chop rotation by ATR x |OFI|, and equity-linked compounding on a Demo wallet.
Mock venue, synthetic books.  These tests prove wiring and arithmetic; they say nothing about whether any of it makes money."""
import asyncio
import re
import time
from dataclasses import replace

import pytest

from config.settings import micro_capital_env
from core import engine as eng
from core import invariants as inv
from core import micro_signals as msig
from tests.test_micro_rotation import ADA, BUY, DOGE, PEPE, PX, SELL, XRP, book, burst_engine, doge_micro, rows, scanner, trade_through

FEES = 0.08
# a chop tape the harvester accepts: KER 0.20 < 0.28, spread 0.5 bps, OFI +0.6, ATR 1 m 0.10 %
CHOP_F = {'ker': 0.20, 'spread_bps': 0.5, 'ofi_10s': 0.6, 'atr_pct': 0.10, 'cvd_3m': 0.0, 'px_vel_60s': 0.0}


def align_engine(tmp_path, **kw):
    return burst_engine(tmp_path, micro_align=True, **kw)


# ============================================================================= pillar 1: the lane boundary
@pytest.mark.parametrize('ker, lane', [(None, None), (0.0, 'CHOP'), (0.279, 'CHOP'), (0.28, 'TREND'), (0.9, 'TREND')])
def test_the_lane_splits_at_ker_028_and_unknown_is_no_lane(tmp_path, ker, lane):
    assert burst_engine(tmp_path).micro_lane({'ker': ker}) == lane
    assert burst_engine(tmp_path / 'b').micro_lane(None) is None


def test_the_harvest_lane_needs_a_micro_account_a_mode_and_a_known_chop_ker(tmp_path):
    e = burst_engine(tmp_path)
    assert e.harvest_lane({'ker': 0.2}) and not e.harvest_lane({'ker': 0.3}) and not e.harvest_lane({'ker': None})
    assert not burst_engine(tmp_path / 'b', micro_harvester='off').harvest_lane({'ker': 0.2})
    assert not burst_engine(tmp_path / 'c', wallet=5000.0).harvest_lane({'ker': 0.2})            # the 5 000 U demo wallet
    assert not burst_engine(tmp_path / 'd', micro_capital='off').harvest_lane({'ker': 0.2})
    assert eng.PROFILES['demo'].micro_harvester == 'shadow' and eng.PROFILES['demo'].micro_ker_min == 0.28


def test_the_harvester_bracket_is_fixed_small_and_honest_about_its_win_rate(tmp_path):
    e = burst_engine(tmp_path)
    bp = e.bracket_plan('SCALP', CHOP_F, harvest=True)
    assert (bp['tp_pct'], bp['sl_pct'], bp['src']) == (0.15, 0.15, 'HARVEST')
    assert bp['rr_net'] == pytest.approx((0.15 - 0.04) / (0.15 + FEES)) and bp['rr_net'] < 1.0
    assert bp['be_wr'] == pytest.approx(0.23 / 0.34) and bp['be_wr'] == pytest.approx(0.676, abs=1e-3)   # needs ~68 % winners
    # the ordinary micro scalp keeps the noise floor and the RR lift: SL 0.22 %, TP 0.37 % (never 0.15 %)
    old = e.bracket_plan('SCALP', CHOP_F)
    assert old['sl_pct'] == pytest.approx(0.22) and old['tp_pct'] == pytest.approx((0.22 + FEES) * 1.10 + 0.04)
    # a mistyped TP cannot leave the 0.12-0.18 % band
    assert burst_engine(tmp_path / 'b', micro_harvest_tp_pct=0.9).bracket_plan('SCALP', CHOP_F, harvest=True)['tp_pct'] == 0.18
    assert burst_engine(tmp_path / 'c', micro_harvest_tp_pct=0.01).bracket_plan('SCALP', CHOP_F, harvest=True)['tp_pct'] == 0.12


def test_tp_012_locks_008_net_of_the_maker_round_trip():
    assert 0.12 - 0.04 == pytest.approx(0.08)
    # why no RR floor: even at SL 0.10 the stop costs 0.18 % against a 0.11 % win -> break-even win rate 62 %
    assert (0.10 + FEES) / ((0.15 - 0.04) + (0.10 + FEES)) == pytest.approx(0.621, abs=1e-3)


# ============================================================================= pillar 1: the trend sniper
def test_the_sniper_tp_slides_from_the_rr_140_tp_to_070_with_ker(tmp_path):
    e = burst_engine(tmp_path)
    tp = lambda k: e.micro_bracket(0.10, 0.29, {'ker': k})[1]
    assert e.micro_bracket(0.10, 0.29, {'ker': 0.28})[0] == pytest.approx(0.22)
    assert tp(0.28) == pytest.approx(0.46)                                  # entry of the trend lane: the RR-1.40 TP
    assert tp(0.44) == pytest.approx(0.46 + 0.5 * 0.24)                      # halfway between 0.28 and 0.60
    assert tp(0.60) == pytest.approx(0.70) and tp(0.95) == pytest.approx(0.70)
    assert tp(0.20) == pytest.approx(0.46)                                   # CHOP keeps the 11-09d bracket
    assert e.micro_bracket(0.10, 0.29, {})[1] == pytest.approx(0.46)         # KER unknown: no sniper stretch
    assert e.micro_bracket(0.15, 0.32, {'ker': 0.9}, scalp=True)[1] == pytest.approx((0.22 + FEES) * 1.10 + 0.04)
    sl, t1 = e.micro_bracket(0.10, 0.29, {'ker': 0.5})
    assert e.micro_bracket(sl, t1, {'ker': 0.5}) == (pytest.approx(sl), pytest.approx(t1))   # idempotent (called twice in _execute)


def test_the_sniper_off_switch_and_the_loss_budget(tmp_path):
    off = burst_engine(tmp_path, micro_sniper_tp_max_pct=0.0)
    assert off.micro_bracket(0.10, 0.29, {'ker': 0.9})[1] == pytest.approx(0.46)
    # a 0.70 % TP does not touch the stop, so the 0.05 U budget is exactly what it was
    assert 5.1 * (0.22 + FEES) / 100 == pytest.approx(0.0153, abs=1e-4)


# ============================================================================= pillar 2: CVD 3 m is a size penalty
def f_trend(side, **kw):
    s = 1 if side == 'BUY' else -1
    return {'ker': 0.40, 'ofi_10s': s * 0.80, 'cvd_3m': -s * 1.0e5, 'px_vel_60s': s * 0.10, 'atr_pct': 0.10, **kw}


def test_px1m_favours_needs_half_an_atr_and_a_known_move(tmp_path):
    e = burst_engine(tmp_path)
    assert e.px1m_favors('SELL', {'px_vel_60s': -0.05, 'atr_pct': 0.10}) and not e.px1m_favors('SELL', {'px_vel_60s': -0.049, 'atr_pct': 0.10})
    assert not e.px1m_favors('SELL', {'px_vel_60s': +0.20, 'atr_pct': 0.10}) and not e.px1m_favors('BUY', {'px_vel_60s': None})
    assert e.px1m_favors('BUY', {'px_vel_60s': 0.03}) and not e.px1m_favors('BUY', {'px_vel_60s': 0.029})   # ATR unknown: the 0.03 % floor
    assert e.px1m_favors('BUY', {'px_vel_60s': 0.03, 'atr_pct': 0.01})                                        # quiet tape: floor wins


@pytest.mark.parametrize('side', ['BUY', 'SELL'])
def test_a_lagging_cvd3m_opposing_the_trade_only_sizes_it_down_when_fast_evidence_agrees(tmp_path, side):
    e = align_engine(tmp_path)
    assert e.micro_entry_block(side, f_trend(side)) == []                                    # relaxed, not blocked
    assert 'size x0.75' in e.cvd3m_soft(side, f_trend(side))
    for kw in ({'px_vel_60s': None}, {'px_vel_60s': 0.0}, {'cvd_3m': None}, {'ofi_10s': None}):    # unknown never relaxes
        blk = e.micro_entry_block(side, f_trend(side, **kw))
        assert blk and blk[0][0] == 'MICRO_ALIGN', kw
    s = 1 if side == 'BUY' else -1
    assert e.cvd3m_soft(side, f_trend(side, cvd_3m=s * 1.0e5)) is None                       # CVD with the trade: nothing to relax
    assert e.cvd3m_soft(side, f_trend(side, ofi_10s=s * 0.49)) is None                       # OFI under the 0.50 soft floor
    assert burst_engine(tmp_path / 'b', micro_cvd_soft=False, micro_align=True).micro_entry_block(side, f_trend(side))


def test_the_ofi_align_floor_and_the_macro_lock_still_bind(tmp_path):
    e = align_engine(tmp_path)
    blk = e.micro_entry_block('SELL', f_trend('SELL', ofi_10s=-0.55))                        # soft OFI 0.50 < 0.55 < align 0.65
    assert blk and 'OFI -0.55' in blk[0][1]
    e.macro = {'name': 'LONG ONLY', 'allow': ('BUY',)}
    assert any('macro 5m LONG ONLY forbids SELL' in w for _, w in e.micro_entry_block('SELL', f_trend('SELL')))   # macro stays a block


def test_align3_skips_only_the_cvd3m_layer_when_the_soft_rule_applies(tmp_path):
    e = align_engine(tmp_path)
    f = {**f_trend('SELL'), 'cvd_60s': -1e5, 'cvd_15s': -1e5, 'obi': -0.3}
    assert e.align3_check('SELL', force=True, f=f) is None
    assert 'cvd_60s' in e.align3_check('SELL', force=True, f={**f, 'cvd_60s': +1e5})
    assert 'cvd_3m' in e.align3_check('SELL', force=True, f={**f, 'px_vel_60s': 0.0})


def test_the_60s_price_move_feature(tmp_path):
    feed = msig.MicroFeed(DOGE, msig.config_for(DOGE))
    for t, px in ((0.0, 0.0700), (30.0, 0.0705), (59.0, 0.0710), (61.0, 0.0714), (70.0, 0.0721)):
        feed.on_message({'stream': 'dogeusdt@aggTrade', 'data': {'p': str(px), 'q': '1000', 'm': False}}, now=t)
    v = feed.features(now=70.0)
    assert v['px_vel_60s'] == pytest.approx((0.0721 / 0.0700 - 1) * 100, abs=1e-6)        # last print at/before t-60 s = 10 s -> t = 0
    assert msig.MicroFeed(DOGE).features(now=5.0)['px_vel_60s'] is None                   # no history: unknown, not 0


async def sized(tmp_path, wallet, side='SELL'):
    e, venue, feed = await doge_micro(tmp_path, wallet=wallet, micro_align=True, micro_ttl=False, gtx_ttl_s=0.2)
    orig = feed.features
    feed.features = lambda *a, **k: {**orig(*a, **k), **f_trend(side)}
    await e.execute(side, SELL if side == 'SELL' else BUY, 'SWARM')
    return [r for r in rows(tmp_path) if r.get('qty_req')][-1]


async def test_the_size_penalty_cuts_a_lot_above_the_venue_floor(tmp_path):
    r = await sized(tmp_path, 10.0)                                           # E = 10 U: compounding lot ~10 U notional
    assert r['cvd_soft'].startswith('CVD 3m +100k opposes SELL, relaxed')
    lot, cut = [int(x) for x in re.search(r'lot (\d+) -> (\d+)', r['cvd_soft']).groups()]
    assert cut == int(lot * 0.75) and cut < lot and r['qty_req'] == cut
    assert cut * 0.0689 >= 5.05                                               # still inside the venue window
    assert 'no size left to cut' not in r['cvd_soft']


async def test_the_size_penalty_is_a_noop_at_the_5u_venue_lot(tmp_path):
    r = await sized(tmp_path, 5.0)
    lot, cut = [int(x) for x in re.search(r'lot (\d+) -> (\d+)', r['cvd_soft']).groups()]
    assert cut == lot and 'at the venue lot: no size left to cut' in r['cvd_soft']      # 0.75 x 5.05 U < the 5 U venue minimum
    assert r['qty_req'] * 0.0689 >= 5.05 - 1e-9


# ============================================================================= pillar 3: chop rotation
def chop_feats(e, ker, ofi, atr):
    e.features = {'ker': ker, 'ofi_10s': ofi, 'atr_pct': atr}


def test_a_dead_tape_coin_yields_at_once_to_the_most_volatile_live_chop_coin(tmp_path):
    es, _ = scanner(tmp_path)
    chop_feats(es[DOGE], 0.10, 0.9, 0.30), chop_feats(es[XRP], 0.20, 0.6, 0.12), chop_feats(es[ADA], 0.25, 0.5, 0.20)
    chop_feats(es[PEPE], 0.22, 0.4, 0.40)
    sc = {s: es[s].harvest_score(es[s].features) for s in (DOGE, XRP, ADA, PEPE)}
    assert sc[DOGE] is None                                                  # KER 0.10 < 0.15: dead tape, whatever its ATR / OFI
    assert sc[XRP] == pytest.approx(0.072) and sc[ADA] == pytest.approx(0.10) and sc[PEPE] == pytest.approx(0.16)
    assert es[DOGE].harvest_pick(es[DOGE].features)[0] is False
    assert es[XRP].harvest_pick(es[XRP].features)[1].startswith(f'yield to {PEPE}')
    assert es[ADA].harvest_pick(es[ADA].features)[1].startswith(f'yield to {PEPE}')
    assert es[PEPE].harvest_pick(es[PEPE].features) == (True, '')


def test_peers_outside_the_chop_lane_or_unknown_are_not_rivals(tmp_path):
    es, _ = scanner(tmp_path)
    chop_feats(es[DOGE], 0.20, 0.5, 0.10)
    chop_feats(es[XRP], 0.50, 0.9, 0.90)                                      # TREND lane coin: the sniper's, not a harvest rival
    chop_feats(es[ADA], None, 0.9, 0.90)                                      # KER unknown
    es[PEPE].features = {'ker': 0.20, 'ofi_10s': None, 'atr_pct': 0.9}        # OFI unknown
    assert es[DOGE].harvest_pick(es[DOGE].features) == (True, '')
    chop_feats(es['BTCUSDT'], 0.20, 0.99, 5.0)                                # an observer outside the micro universe
    assert es[DOGE].harvest_pick(es[DOGE].features) == (True, '')


# ============================================================================= pillar 1: harvester end to end (mock venue)
async def test_shadow_harvest_logs_a_row_with_its_bracket_and_never_sends(tmp_path):
    e, venue, feed = await doge_micro(tmp_path)
    assert e.p.chop_scalper == 'off' and e.p.micro_harvester == 'shadow'      # the lane has its own switch
    await e.chop_scalp(BUY, {**feed.features(), **CHOP_F})
    [r] = [x for x in rows(tmp_path) if x['outcome'] == 'SHADOW_SCALP']
    assert r['harvest'] is True and (r['tp_pct'], r['sl_pct']) == (0.15, 0.15) and r['be_wr'] == pytest.approx(0.676, abs=1e-3)
    assert r['side'] == 'BUY' and not venue.fills and not [o for o in venue.orders.values() if o['kind'] == 'ENTRY']
    await e.chop_scalp(BUY, {**feed.features(), **CHOP_F})                    # 20 s throttle
    assert len([x for x in rows(tmp_path) if x['outcome'] == 'SHADOW_SCALP']) == 1


async def test_harvester_off_and_a_trend_tape_leave_the_old_scalper_rules(tmp_path):
    e, venue, feed = await doge_micro(tmp_path, micro_harvester='off', chop_scalper='shadow')
    await e.chop_scalp(BUY, {**feed.features(), **CHOP_F})
    [r] = rows(tmp_path)
    assert r['outcome'] == 'SHADOW_SCALP' and 'harvest' not in r              # legacy shadow row, mode from chop_scalper
    e2, _, feed2 = await doge_micro(tmp_path / 'b', chop_scalper='off')
    await e2.chop_scalp(BUY, {**feed2.features(), **CHOP_F, 'ker': 0.35})     # TREND lane + scalper off: nothing
    assert rows(tmp_path / 'b') == []


async def test_dead_tape_coin_does_not_harvest(tmp_path):
    e, venue, feed = await doge_micro(tmp_path)
    await e.chop_scalp(BUY, {**feed.features(), **CHOP_F, 'ker': 0.10})
    assert rows(tmp_path) == [] and e.funnel.outcomes['HARVEST_YIELD'] == 1          # KER 0.10 < 0.15: counted, nothing sent


async def test_on_rests_a_maker_harvest_with_the_fixed_bracket_and_fills(tmp_path):
    e, venue, feed = await doge_micro(tmp_path, micro_harvester='on', micro_harvest_ttl_s=3.0, scalp_min_edge_bps=0.0,
                                      scalp_align3=False)
    e.diag = {'consensus': 0.9, 'edge_bps': 40.0}
    f = {**feed.features(), **CHOP_F}
    task = asyncio.ensure_future(e.chop_scalp(BUY, f))
    await asyncio.sleep(0.8)                                                  # rests past the 0.3 s base TTL: the 45 s TTL applies
    assert not task.done() and any(o['status'] == 'NEW' for o in venue.orders.values())
    await trade_through(feed)
    await task
    r = [x for x in rows(tmp_path) if x['outcome'] in ('FILLED', 'UNFILLED')][-1]
    assert r['outcome'] == 'FILLED' and r['harvest'] is True and r['route'] == 'gtx' and r['path'] == 'SCALP'
    assert r['bracket'] == [pytest.approx(0.15, abs=0.01), pytest.approx(0.15, abs=0.01)]
    assert r['micro_bracket'] is None and r['tp_lift'] is None                # no noise-floor / RR lift on the harvester
    p = e.pos
    assert p and p['path'] == 'SCALP' and p['eject_s'] == 45.0                # maker TP rests, EJECT-BE after 45 s
    assert (p['tp'] / p['entry'] - 1) * 100 == pytest.approx(0.15, abs=0.02) and (1 - p['sl'] / p['entry']) * 100 == pytest.approx(0.15, abs=0.02)
    assert p['qty'] * p['entry'] <= 6.0 + 1e-6                                # micro window


async def test_on_cuts_a_resting_harvest_on_an_adverse_move_inside_the_ttl(tmp_path):
    q = [None]
    e, venue, feed = await doge_micro(tmp_path, frozen=q, micro_harvester='on', micro_harvest_ttl_s=5.0, scalp_min_edge_bps=0.0,
                                      scalp_align3=False)
    e.diag = {'consensus': 0.9, 'edge_bps': 40.0}
    task = asyncio.ensure_future(e.chop_scalp(BUY, {**feed.features(), **CHOP_F}))
    await asyncio.sleep(0.6)                                                  # resting well inside the 5 s TTL
    assert not task.done()
    book(feed, round(PX * 0.9992, 5))                                         # the signal market drops ~8 bps
    t0 = time.monotonic()
    await task
    r = [x for x in rows(tmp_path) if x['outcome'] != 'SHADOW_SCALP'][-1]
    assert time.monotonic() - t0 < 1.5                                        # pulled at once, not at the 5 s deadline
    assert r['harvest'] is True and r['outcome'] == 'UNFILLED' and r['ttl_cut'].startswith('ADVERSE')


async def test_on_never_bypasses_the_consensus_token(tmp_path):
    """VESKA: TokenAuthority refuses a decision under 76.8 %, so a harvest needs the swarm behind the OFI side."""
    e, venue, feed = await doge_micro(tmp_path, micro_harvester='on', scalp_min_edge_bps=0.0, scalp_align3=False)
    weak = replace(BUY, score=0.70)
    await e.chop_scalp(weak, {**feed.features(), **CHOP_F})
    await e.chop_scalp(SELL, {**feed.features(), **CHOP_F})                   # swarm on the other side
    assert rows(tmp_path) == [] and not venue.orders


async def test_a_hostile_be_wr_config_refuses_the_harvest(tmp_path):
    e, venue, feed = await doge_micro(tmp_path, micro_harvester='on', micro_harvest_sl_pct=0.30, scalp_min_edge_bps=0.0,
                                      scalp_align3=False)
    e.diag = {'consensus': 0.9, 'edge_bps': 40.0}
    await e.chop_scalp(BUY, {**feed.features(), **CHOP_F})                    # TP 0.15 / SL 0.30: break-even win rate 77 %
    assert rows(tmp_path) == [] and not venue.orders
    assert e.bracket_plan('SCALP', CHOP_F, harvest=True)['be_wr'] == pytest.approx(0.38 / 0.49)


def test_the_harvester_is_a_demo_rehearsal_live_refuses_it_on():
    assert inv.check_profile(eng.PROFILES['demo']) == [] and inv.check_profile(eng.PROFILES['live']) == []
    assert inv.check_profile(replace(eng.PROFILES['demo'], micro_harvester='on')) == []                 # maker only: Demo may rehearse
    errs = inv.check_profile(replace(eng.PROFILES['live'], micro_harvester='on'))
    assert len(errs) == 1 and 'micro_harvester' in errs[0]
    assert inv.check_profile(replace(eng.PROFILES['live'], micro_harvester='shadow')) == []


def test_env_flag_parses_and_a_typo_raises():
    assert micro_capital_env({'MICRO_HARVESTER': ' ON '}) == {'micro_harvester': 'on'}
    assert micro_capital_env({}) == {}
    with pytest.raises(ValueError, match='MICRO_HARVESTER'):
        micro_capital_env({'MICRO_HARVESTER': 'yes'})


# ============================================================================= pillar 4: equity-linked compounding
def ledger_rows(*pnls, micro=True):
    return [{'pnl': p, 'micro': micro, 'tag': 'AUTO', 't': 1.0 + i} for i, p in enumerate(pnls)]


def test_a_forced_micro_run_on_a_demo_wallet_compounds_realized_micro_pnl(tmp_path):
    e = burst_engine(tmp_path, wallet=5000.0, micro_capital='on')             # the Demo account: 5 000 U wallet, micro forced
    assert e.micro_equity() == 5.0                                            # no trades yet
    e.all_rows = ledger_rows(+0.4, +0.6, -0.1) + ledger_rows(+9.0, micro=False) + [{'pnl': None, 'micro': True}]
    assert e.micro_equity() == pytest.approx(5.9)                             # +0.9 net; non-micro rows and unknown pnl skipped
    e.all_rows = ledger_rows(*[+1.0] * 30)
    assert e.micro_equity() == 15.0                                           # capped at micro_equity_max
    e.all_rows = ledger_rows(-9.0)
    assert e.micro_equity() == 0.0                                            # never negative
    off = burst_engine(tmp_path / 'b', wallet=5000.0, micro_capital='on', micro_equity_track=False)
    off.all_rows = ledger_rows(+3.0)
    assert off.micro_equity() == 5.0                                          # old behaviour behind the flag


def test_every_won_usdt_raises_the_lot_until_the_loss_ceiling_binds(tmp_path):
    s = burst_engine(tmp_path).micro_sizer()
    lots = {e: s.qty(0.0711, 1.0, 1.0, 5.0, equity=e, leverage=5.0, risk_notional=s.risk_notional(0.22, FEES))[0]
            for e in (5.0, 6.0, 7.0, 8.0, 10.0, 12.0, 15.0)}
    assert lots[5.0] == 72 and lots[6.0] == 84 and lots[7.0] == 98 and lots[10.0] == 140       # E x 1.0 U notional at 0.0711
    assert all(lots[a] < lots[b] for a, b in ((5.0, 6.0), (6.0, 7.0), (7.0, 8.0), (8.0, 10.0), (10.0, 12.0), (12.0, 15.0)))
    assert lots[15.0] * 0.0711 <= 0.05 / 0.0030 + 0.0711                       # ... and 16.7 U is the 0.05 U ceiling at SL 0.22 %
    # the directive's 20 U -> 300 DOGE = 21.3 U: loss at the stop 0.064 U, over the 0.05 U invariant
    assert 300 * 0.0711 * 0.0030 == pytest.approx(0.0640, abs=1e-4) and 300 * 0.0711 * 0.0030 > 0.05
    unlimited = s.qty(0.0711, 1.0, 1.0, 5.0, equity=20.0, leverage=5.0, risk_notional=s.risk_notional(0.22, FEES))[0]
    assert unlimited * 0.0711 <= 0.05 / 0.0030 + 1e-9                          # the sizer refuses to grow past the invariant
