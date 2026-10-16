"""10-16 (b) RFC-Q4.3: burst-lane CHOP hibernation + VPIN veto, funding carry quotes -> shadow book -> telemetry.
Synthetic prints / quotes, temp files; no network, no real ledger."""
from types import SimpleNamespace

import pytest

from core import engine as eng
from core import funding_executor as fx
from tests.test_burst_lane import setup, spike_up, warm


def _burst(tmp_path, **kw):
    lane, e, feed, venue, events, clk = setup(tmp_path, **kw)
    warm(feed, clk)
    spike_up(feed, clk)                                                # a burst second: MR -> SELL at the ask
    return lane, e, events, clk


# ============================================================================ CHOP hibernation
async def test_router_chop_hibernates_entry_and_logs_once(tmp_path):
    lane, e, events, clk = _burst(tmp_path, burst_chop_gate=True)
    e.autonomy = SimpleNamespace(router={'regime': 'CHOP'})
    await lane.step()
    await lane.step()
    assert lane.last['burst'] and not lane.orders and lane.hibernating
    obs = [m for m in events if m.startswith('BURST OBSERVE')]
    assert obs == ['BURST OBSERVE SOLUSDT: CHOP regime active (KER < 0.25) -> entry hibernated']


async def test_symbol_ker_below_chop_hibernates_unknown_ker_does_not(tmp_path):
    lane, e, events, clk = _burst(tmp_path, burst_chop_gate=True)
    e.features = {'ker': 0.20}
    await lane.step()
    assert not lane.orders and 'CHOP' in lane.last['skip']
    lane2, e2, _, _ = _burst(tmp_path / 'b', burst_chop_gate=True)
    e2.features = {'ker': None}                                        # UNKNOWN is not CHOP (never a silent 0)
    await lane2.step()
    assert lane2.orders and lane2.orders[0]['side'] < 0


async def test_entries_resume_when_ker_recovers(tmp_path):
    lane, e, events, clk = _burst(tmp_path, burst_chop_gate=True)
    e.autonomy = SimpleNamespace(router={'regime': 'CHOP'})
    e.features = {'ker': 0.10}
    await lane.step()
    assert not lane.orders
    e.autonomy.router = {'regime': 'NORMAL'}
    e.features = {'ker': 0.45}
    await lane.step()
    assert lane.orders and lane.orders[0]['kind'] == 'E' and not lane.hibernating


async def test_gate_off_keeps_trading_in_chop(tmp_path):
    lane, e, events, clk = _burst(tmp_path, burst_chop_gate=False, burst_vpin_veto_s=0.0)
    e.autonomy = SimpleNamespace(router={'regime': 'CHOP'})
    e.features = {'ker': 0.10}
    await lane.step()
    assert lane.orders


# ============================================================================ VPIN veto
async def test_vpin_toxic_mark_vetoes_both_sides_for_veto_window(tmp_path):
    lane, e, events, clk = _burst(tmp_path, burst_chop_gate=False, burst_vpin_veto_s=60.0)
    e.toxic_vpin, e.toxic_side = 0.84, 'BUY'                          # BUY flow: the engine itself would allow SELL
    e.toxic_until = clk.t - 10.0 + e.p.vpin_pause_s                    # marked 10 s ago
    await lane.step()
    assert not lane.orders and 'VPIN TOXIC 0.84 BUY flow 10s ago' in lane.last['skip']
    e.toxic_until = clk.t - 61.0 + e.p.vpin_pause_s                    # marked 61 s ago -> veto over
    await lane.step()
    assert lane.orders


async def test_vpin_veto_does_not_touch_an_open_position(tmp_path):
    from tests.test_burst_lane import T0, trade
    lane, e, events, clk = _burst(tmp_path, burst_chop_gate=True, burst_vpin_veto_s=60.0)
    await lane.step()
    trade(lane.e.feed, 100.32, T0 + 1.5, False)
    clk.t = T0 + 2.0
    await lane.step()
    assert lane.pos is not None
    e.toxic_vpin, e.toxic_side, e.toxic_until = 0.9, 'SELL', clk.t + e.p.vpin_pause_s
    e.autonomy = SimpleNamespace(router={'regime': 'CHOP'})
    await lane.step()
    assert lane.pos is not None and any(o['kind'] == 'TP' for o in lane.orders)   # TP stays resting


def test_demo_profile_gates_on_live_lane_and_carry_off():
    d, live = eng.PROFILES['demo'], eng.PROFILES['live']
    assert d.burst_chop_gate and d.burst_vpin_veto_s == 60.0 and d.funding_shadow
    assert live.burst_lane == 'off' and not live.funding_shadow
    base = eng.Profile('x', 'BTCUSDT')
    assert not base.burst_chop_gate and base.burst_vpin_veto_s == 0.0 and not base.funding_shadow


# ============================================================================ funding carry telemetry
BN = [{'symbol': 'LINKUSDT', 'markPrice': '20.000', 'lastFundingRate': '0.0001'},          # 1.0 bps / 8 h
      {'symbol': 'ETHUSDT', 'markPrice': '4000.0', 'lastFundingRate': '0.00002'}]
HL = [{'universe': [{'name': 'LINK'}, {'name': 'ETH'}, {'name': 'kPEPE'}]},
      [{'funding': '0.0000500', 'markPx': '20.010'},                                         # 0.5 bps / h = 4 / 8 h
       {'funding': '0.0000025', 'markPx': '4000.0'},
       {'funding': '0.0000125', 'markPx': '0.01'}]]


def test_quotes_from_parses_both_venues_and_keeps_missing_unknown():
    q = {x.coin: x for x in fx.quotes_from(BN, HL, ('LINK', 'ETH', '1000PEPE', 'SOL'))}
    assert q['LINK'].bn_rate_8h == pytest.approx(1e-4) and q['LINK'].hl_rate_1h == pytest.approx(5e-5)
    assert q['1000PEPE'].hl_mark == pytest.approx(0.01) and q['1000PEPE'].bn_rate_8h is None
    assert q['SOL'] == fx.FundingQuote('SOL', None, None, None, None)
    assert fx.evaluate(q['1000PEPE'])[1].startswith('UNKNOWN')


def test_carry_view_reports_apr_eligibility_and_unknowns():
    quotes = fx.quotes_from(BN, HL, ('LINK', 'ETH', 'SOL'))
    book = fx.ShadowCarryBook(4919.1996)
    intents = book.step(quotes)
    v = fx.carry_view(book, quotes, intents, now=123.0)
    link = v['top'][0]
    assert link['coin'] == 'LINK' and link['short'] == 'HL' and link['spread_8h_bps'] == pytest.approx(3.0)
    assert link['apr_pct'] == pytest.approx(3.0 * 3 * 365 / 100, abs=0.01) and link['eligible']   # basis +5 bps
    assert v['held'] == ['LINK'] and intents[0]['kind'] == 'OPEN' and v['unknown'] == ['SOL']
    assert v['notional_per_leg'] == pytest.approx(4919.1996 * 0.7 / 6, abs=0.01) and v['mode'] == 'shadow'


async def test_runner_funding_loop_publishes_state_and_survives_errors():
    events = []
    e0 = SimpleNamespace(account={'wallet': 4919.1996}, p=eng.PROFILES['demo'], event=events.append)
    r = SimpleNamespace(engines={'SOLUSDT': e0}, carry_state=None)
    st = await eng.Runner.funding_loop(r, once=True, fetch=lambda: fx.quotes_from(BN, HL, ('LINK', 'ETH')))
    assert st['top'][0]['coin'] == 'LINK' and st['held'] == ['LINK'] and 'error' not in st
    assert any(m.startswith('CARRY SHADOW OPEN LINK') for m in events)

    def boom():
        raise OSError('proxy 403')
    st2 = await eng.Runner.funding_loop(r, once=True, fetch=boom)
    assert st2['error'] == 'OSError: proxy 403' and st2['t'] == st['t']   # stale quotes keep their own timestamp
