"""Exit lifecycle (2026-10-07r): DOGE #37 SOFT-BE lost money with be_locked=True; RATCHET failed 3x on the
adopted SOL short with the exchange code thrown away.

Reproduced here on the mock venue, then fixed: (1) a protective maker exit may chase only 0.03 % past its
reference / 5 s, then it becomes a reduce-only MARKET exit; (2) 'BE locked' is reported only when the EXCHANGE
stop covers the entry - a soft target is labelled as such; (3) stop-API failures keep code + context + the
unprotected window, retries back off and stop; (4) ADOPT finds the entry fills so the entry fee is in the
ledger; (5) KILL is idempotent and reconciles. Behaviour tests, not evidence of profitability."""
import json
import time
from dataclasses import replace

import pytest

from core import engine as eng
from core import micro_signals as msig
from live.binance_futures_client import BinanceError
from tests.test_two_mode_engine import depth, mock_engine

DOGE = 'DOGEUSDT'
ENTRY = 0.08880


def book(feed, bid, ask=None):
    ask = ask if ask is not None else round(bid + 0.00001, 5)
    for _ in range(3):
        feed.on_message(depth(bid, ask, bq=50_000.0, aq=50_000.0, sym=DOGE))


async def doge_long(tmp_path, history=True, **prof):
    """291 DOGE long @0.08880 with its hard stop 0.08835, adopted by MARIN (the #37 position)."""
    feed = msig.MicroFeed(DOGE, msig.config_for(DOGE))
    book(feed, 0.08880, 0.08881)
    prof.setdefault('regime_flip_exit', False)
    prof.setdefault('flow_exit', False)
    e, venue, _ = mock_engine(tmp_path, feed=feed, **prof)
    e.p = replace(e.p, symbol=DOGE, qty=0.0)
    e.spec = dict(eng.FALLBACK_SPECS[DOGE])
    venue._fill(DOGE, 'BUY', 291.0, ENTRY, venue.cfg.maker_bps)
    await venue.stop(DOGE, 'SELL', '0.08835', 'gs-hard')
    if not history:
        venue.fills.clear()                                  # fills older than the visible history window
    await e.sync()
    assert e.pos and e.pos['entry'] == pytest.approx(ENTRY) and e.pos['sl'] == pytest.approx(0.08835)
    return e, venue, feed


async def step(e, feed, bid):
    book(feed, bid)
    if e.pos and e.pos.get('exiting'):
        e.pos['exiting']['posted'] -= 1.0                   # let the 0.5 s re-peg happen
    await e.sync()


def last_row(tmp_path):
    return json.loads((tmp_path / 'trades.jsonl').read_text(encoding='utf-8').splitlines()[-1])


async def arm_be(e, feed):
    await step(e, feed, 0.08892)                            # mid 0.088925 = MFE +0.1408 % (#37): T1 + T2, no ratchet
    p = e.pos
    assert p['mfe_pct'] == pytest.approx(0.1408, abs=1e-3) and not p.get('ratchet')
    assert p['soft_kind'] == 'BE' and p['soft_stop'] == pytest.approx(0.08886) and p['be_locked']
    return p


# ============================================================================= 1. the DOGE #37 loss
async def test_doge_37_reproduced_without_the_bound(tmp_path):
    e, venue, feed = await doge_long(tmp_path)                       # default: bound in SHADOW (replay 10-08)
    await arm_be(e, feed)
    await step(e, feed, 0.08885)                            # mark <= 0.08886 -> SOFT-BE maker at the touch
    assert e.pos['exiting']['reason'] == 'SOFT-BE' and e.pos['exiting']['ref'] == pytest.approx(0.08886)
    for bid in (0.08883, 0.08880, 0.08877, 0.08874, 0.08871, 0.08867):
        await step(e, feed, bid)                            # the ask walks down, the sell is never lifted
    assert e.pos['exiting']['reprices'] >= 5
    book(feed, 0.08868, 0.08869)                            # a buyer finally lifts 0.08868
    await e.sync()
    r = last_row(tmp_path)
    assert r['reason'] == 'SOFT-BE' and r['be_locked'] and r['taker_fills'] == 0
    assert r['gross'] == pytest.approx(-0.03492, abs=1e-6)
    assert r['fee'] == pytest.approx(0.01032931, abs=2e-6) and r['pnl'] == pytest.approx(-0.04524931, abs=2e-6)
    assert r['stop_covered_entry'] is False                 # the exchange stop never moved off 0.08835
    sh = r['shadow_escalate']                               # where the bound would have exited (taker at the bid)
    assert sh['touch'] == pytest.approx(0.08880) and sh['adverse_pct'] >= 0.03       # first step past 0.03 %


async def test_doge_37_with_the_bound_escalates_and_caps_the_loss(tmp_path):
    e, venue, feed = await doge_long(tmp_path, soft_exit_escalate=True)
    await arm_be(e, feed)
    await step(e, feed, 0.08885)
    assert e.pos['exiting']['reason'] == 'SOFT-BE'
    await step(e, feed, 0.08883)                            # mark 0.088835: 0.028 % past 0.08886 -> still maker
    assert e.pos and e.pos['exiting']
    await step(e, feed, 0.08882)                            # mark 0.088825: 0.039 % past -> MARKET reduce-only
    assert e.pos is None
    r = last_row(tmp_path)
    assert r['reason'] == 'SOFT-BE>TAKER' and r['exit_mode'] == 'maker+taker' and r['taker_fills'] == 1
    assert r['exit_ref'] == pytest.approx(0.08886)
    assert r['pnl'] > -0.02 and r['pnl'] > -0.04524931 + 0.025          # same path: -0.045 -> about -0.012
    assert any('EXIT ESCALATED SOFT-BE' in ev['message'] for ev in e.events)


async def test_deadline_escalates_when_price_sits_on_the_reference(tmp_path):
    e, venue, feed = await doge_long(tmp_path, soft_exit_escalate=True)
    await arm_be(e, feed)
    await step(e, feed, 0.08885)
    e.pos['exiting']['started'] -= 5.1                      # 5 s, mark still at/below 0.08886
    await step(e, feed, 0.08885)
    assert e.pos is None and last_row(tmp_path)['reason'] == 'SOFT-BE>TAKER'


async def test_no_taker_when_price_recovers_above_the_reference(tmp_path):
    e, venue, feed = await doge_long(tmp_path, soft_exit_escalate=True)
    await arm_be(e, feed)
    await step(e, feed, 0.08885)
    book(feed, 0.08885, 0.08888)                            # mid back above 0.08886, nobody lifts our sell
    e.pos['exiting']['started'] -= 30
    await e.sync()
    assert e.pos and e.pos['exiting'] and not e.pos.get('exit_escalated')
    assert not any(not f['maker'] for f in venue.fills)


async def test_eject_keeps_its_own_schedule(tmp_path):
    e, venue, feed = await doge_long(tmp_path, eject=True, soft_exit_escalate=True)
    e.pos['opened'] -= 181
    await step(e, feed, 0.08880)
    x = e.pos['exiting']
    assert x['reason'] == 'EJECT-BE'
    x['started'] -= 60
    await step(e, feed, 0.08860)                            # far below: EJECT is not escalated by this rule
    assert e.pos and e.pos['exiting']['reason'].startswith('EJECT')


async def test_partial_maker_fill_then_escalation_settles_once(tmp_path):
    e, venue, feed = await doge_long(tmp_path, soft_exit_escalate=True)
    await arm_be(e, feed)
    await step(e, feed, 0.08885)
    venue._fill(DOGE, 'SELL', 100.0, 0.08886, venue.cfg.maker_bps)     # race: part of the maker exit filled
    await step(e, feed, 0.08882)
    assert e.pos is None and not venue.amt.get(DOGE)
    rows = (tmp_path / 'trades.jsonl').read_text(encoding='utf-8').splitlines()
    r = json.loads(rows[-1])
    assert len(rows) == 1 and r['maker_fills'] >= 2 and r['taker_fills'] == 1


# ============================================================================= 2. what 'BE locked' means
async def test_soft_target_is_not_reported_as_protection(tmp_path):
    e, venue, feed = await doge_long(tmp_path)
    await arm_be(e, feed)
    row = e.position_row()
    assert row['be_locked'] and row['stop_covers_entry'] is False and row['protection'] == 'SOFT TARGET ONLY'
    labels = [str(r) for r in e.agent_rows()]
    assert any('BE TARGET (soft)' in x for x in labels) and not any('BREAKEVEN LOCKED' in x for x in labels)
    e.pos['sl'] = 0.08884                                   # an exchange stop above the entry
    assert e.position_row()['protection'] == 'EXCHANGE STOP >= ENTRY'
    assert any('BREAKEVEN LOCKED' in str(r) for r in e.agent_rows())
    js = open(eng.ROOT / 'web_ui' / 'desk.js', encoding='utf-8').read()
    assert 'CHƯA BẢO VỆ' in js and 'stop_covers_entry' in js


# ============================================================================= 3. RATCHET failures
class StopScript:
    """venue.stop replacement: raises the scripted errors in order, then succeeds."""

    def __init__(self, venue, script):
        self.venue, self.script, self.calls = venue, list(script), []
        self.orig = venue.stop

    async def __call__(self, sym, side, price, cid):
        self.calls.append(float(price))
        err = self.script.pop(0) if self.script else None
        if err is not None:
            raise err
        await self.orig(sym, side, price, cid)


async def test_ratchet_failure_keeps_code_context_and_naked_window(tmp_path):
    e, venue, feed = await doge_long(tmp_path)
    s = StopScript(venue, [BinanceError(-4130, 400), BinanceError(-2021, 400), None])
    venue.stop = s
    await step(e, feed, 0.08895)                            # MFE +0.17 % -> ratchet to entry +0.08 %
    p = e.pos
    assert s.calls == pytest.approx([0.08888, 0.08888, 0.08835])          # new, new after cancel, restore old
    assert p['sl'] == pytest.approx(0.08835) and p.get('ratchet_fails') == 1 and p.get('ratchet') is not True
    ev = p['stop_events'][-1]
    assert ev['code'] == -2021 and ev['meaning'] == 'order would immediately trigger' and ev['step'] == 'swap'
    assert ev['naked_ms'] >= 0 and ev['working_type'] == 'CONTRACT_PRICE' and ev['venue_bid'] == pytest.approx(0.08895)
    msg = next(x['message'] for x in e.events if 'failed after cancel' in x['message'])
    assert 'code=-2021 (order would immediately trigger)' in msg and 'unprotected' in msg
    assert p['ratchet_retry_at'] - e.clock() == pytest.approx(e.p.ratchet_retry_s, abs=0.5)


async def test_ratchet_backs_off_and_gives_up_after_3(tmp_path):
    e, venue, feed = await doge_long(tmp_path)
    venue.stop = StopScript(venue, [BinanceError(-4130, 400), BinanceError(-2021, 400), None] * 3)
    await step(e, feed, 0.08895)
    for n in (2, 3):
        e.pos['ratchet_retry_at'] = 0.0
        await step(e, feed, 0.08895)
        assert e.pos['ratchet_fails'] == n
    assert e.pos['ratchet'] == 'FAILED' and any('RATCHET gave up' in x['message'] for x in e.events)
    calls = len(venue.stop.calls)
    e.pos['ratchet_retry_at'] = 0.0
    await step(e, feed, 0.08895)
    assert len(venue.stop.calls) == calls                   # no 4th attempt


async def test_restore_failure_closes_instead_of_staying_naked(tmp_path):
    e, venue, feed = await doge_long(tmp_path)
    venue.stop = StopScript(venue, [BinanceError(-4130, 400), BinanceError(-2021, 400),
                                    BinanceError('TRANSPORT', 0, True)])
    await step(e, feed, 0.08895)
    assert e.pos is None and not venue.amt.get(DOGE)
    r = last_row(tmp_path)
    assert r['reason'] == 'RATCHET' and [x['step'] for x in r['stop_events']] == ['swap', 'restore']
    assert r['stop_events'][-1]['meaning'].startswith('network/timeout')


async def test_cancel_timeout_that_succeeded_is_recorded_not_hidden(tmp_path):
    e, venue, feed = await doge_long(tmp_path)
    orig_cancel = venue.cancel_stop

    async def cancel_then_timeout(cid):
        await orig_cancel(cid)
        raise BinanceError('TRANSPORT', 0, True)
    venue.cancel_stop = cancel_then_timeout
    venue.stop = StopScript(venue, [BinanceError(-4130, 400), None])
    await step(e, feed, 0.08895)
    p = e.pos
    assert p['sl'] == pytest.approx(0.08888) and p['ratchet'] is True and e.stop_covers_entry(p)
    assert p['stop_events'][-1]['step'] == 'cancel' and p['stop_events'][-1]['uncertain'] is True


def test_err_txt_never_carries_raw_exchange_text():
    assert eng.err_txt(BinanceError(-2021, 400)) == 'BinanceError code=-2021 (order would immediately trigger)'
    assert eng.err_txt(BinanceError(-9999, 400)) == 'BinanceError code=-9999'
    assert 'uncertain' in eng.err_txt(BinanceError('TRANSPORT', 0, True))


# ============================================================================= 4. ADOPT accounting
def test_entry_start_from_fills():
    f = eng.entry_start_from_fills
    fills = [(1000, 0.5), (2000, -0.5), (3000, -0.3), (3100, -0.31)]        # old trade closed, then a short
    assert f(fills, -0.61, 0.01) == 3000
    assert f([(1, 100.0), (2, 191.0)], 291.0, 1.0) == 1
    assert f([(1, 0.2)], -0.61, 0.01) is None and f([], 0.5, 0.01) is None


async def test_adopted_trade_carries_its_entry_fee(tmp_path):
    e, venue, feed = await doge_long(tmp_path)
    assert e.pos['cost_complete'] is True
    await e.close_now()
    r = last_row(tmp_path)
    one_side = 291 * ENTRY * 2e-4
    assert r['cost_complete'] is True and r['fee'] > 1.5 * one_side          # entry maker fee + exit taker fee


async def test_adopt_without_matching_history_is_flagged(tmp_path):
    e, venue, feed = await doge_long(tmp_path, history=False)
    assert e.pos['cost_complete'] is False
    await e.close_now()
    r = last_row(tmp_path)
    assert r['cost_complete'] is False and r['fee'] < 1.5 * 291 * ENTRY * 5e-4      # exit fee only: entry UNKNOWN


# ============================================================================= 5. KILL
async def test_kill_is_idempotent_and_reconciles(tmp_path):
    e, venue, feed = await doge_long(tmp_path)
    rep = await eng.kill_switch([e])
    assert rep == [{'symbol': DOGE, 'flat': True, 'amt': 0.0}]
    n = len((tmp_path / 'trades.jsonl').read_text(encoding='utf-8').splitlines())
    rep2 = await eng.kill_switch([e])
    assert rep2[0]['flat'] and len((tmp_path / 'trades.jsonl').read_text(encoding='utf-8').splitlines()) == n
    assert last_row(tmp_path)['reason'] == 'KILL'


async def test_kill_reports_a_position_it_could_not_flatten(tmp_path):
    e, venue, feed = await doge_long(tmp_path)
    e.pos = None                                             # untracked leftover on the venue

    async def broken(sym):
        return None
    venue.flatten = broken
    rep = await eng.kill_switch([e])
    assert rep[0]['flat'] is False and rep[0]['amt'] == pytest.approx(291.0)
    assert any('NOT FLAT' in x['message'] for x in e.events)


def test_defaults_and_rollback_switch():
    """The bound is SHADOW by default: in the 10-08 mainnet replay every (slip, deadline) setting cost
    0.1-1.3 bps per exit on average versus chasing (docs/research_upgrade/experiment_register.md R2)."""
    p = eng.PROFILES['demo']
    assert not p.soft_exit_escalate and (p.soft_exit_deadline_s, p.soft_exit_max_slip_pct, p.ratchet_max_fail) == (5.0, 0.03, 3)
    assert not eng.PROFILES['live'].soft_exit_escalate
