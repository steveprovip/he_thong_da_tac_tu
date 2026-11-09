"""2026-11-07 Pillar 3: tape maker markouts (1/5/30 s), the toxicity decision, and the engine's hold / skew / pull."""
import asyncio
import json

import pytest

from core import engine as eng
from core import exec_funnel as xf
from core import micro_swarm as ms
from core import toxicity_guard as tg
from core.micro_signals import MicroFeed
from tests.test_two_mode_engine import agg, depth, mock_engine, sig

BUY = ms.evaluate(sig(FLOW=('BUY', 1.0, 20), TREND=('BUY', 1.0, 20)), ms.Gates(cost_bps=4))[0]


# ------------------------------------------------------------------ TapeMarkouts
def test_passive_fills_are_marked_after_each_horizon_with_the_maker_sign():
    m = tg.TapeMarkouts()
    m.on_trade(100.2, 100.0, 1.0, True)            # seller hit the bid: passive BUY at 100.0
    m.on_trade(100.4, 100.1, 3.0, False)           # buyer lifted the ask: passive SELL at 100.1
    m.on_mid(100.9, 99.9)                          # same second still open: nothing matures
    assert m.view('BUY', 1.0) == (None, 0)
    m.on_mid(102.0, 99.9)                          # second ends at 101, +1 s -> the 1 s markouts mature
    assert m.view('BUY', 1.0)[0] == pytest.approx((99.9 - 100.0) / 100.0 * 1e4)    # -10 bps for the bid
    assert m.view('SELL', 1.0)[0] == pytest.approx((100.1 - 99.9) / 100.1 * 1e4)   # the ask did well
    assert m.view('BUY', 5.0) == (None, 0)
    m.on_mid(106.0, 100.0)
    assert m.view('BUY', 5.0) == (pytest.approx(0.0), 1)
    m.on_mid(131.0, 100.0)
    assert m.view('BUY', 30.0)[1] == 1
    m.on_mid(300.0, 100.0)                         # beyond the 120 s window: expired
    assert m.view('BUY', 1.0) == (None, 0) and m.view('BUY', 30.0) == (None, 0)


def test_same_second_fills_are_volume_weighted():
    m = tg.TapeMarkouts()
    m.on_trade(10.0, 100.0, 1.0, True)
    m.on_trade(10.5, 101.0, 3.0, True)
    m.on_mid(12.0, 100.75)
    vwap = (100.0 + 3 * 101.0) / 4
    assert m.view('BUY', 1.0) == (pytest.approx((100.75 - vwap) / vwap * 1e4), 1)
    snap = m.snapshot()
    assert set(snap) == {'1s', '5s', '30s'} and snap['1s']['n_buy'] == 1 and snap['1s']['SELL'] is None


def test_micro_feed_reports_maker_markouts():
    f = MicroFeed('BTCUSDT')
    f.on_message(depth(99.9, 100.1), now=50.0)
    f.on_message(agg(100.0, 2.0, maker=True), now=50.2)
    f.on_message(depth(99.5, 99.7), now=52.0)
    mk = f.features(now=52.0)['maker_markout']
    assert mk['1s']['BUY'] == pytest.approx((99.6 - 100.0) / 100.0 * 1e4) and mk['1s']['n_buy'] == 1


# ------------------------------------------------------------------ decide
def test_decide_pulls_on_vpin_or_deep_markouts_and_skews_on_moderate_ones():
    p = tg.ToxPolicy()
    assert tg.decide('BUY', 0.70, None)[0] == tg.PULL and tg.decide('SELL', 0.70, None)[0] == tg.PULL
    assert tg.decide('BUY', 0.65, None) == (tg.QUOTE, 0, 'no toxicity evidence')
    assert tg.decide('BUY', None, (-3.5, 40))[0] == tg.PULL
    assert tg.decide('BUY', None, (-2.0, 40)) [:2] == (tg.SKEW, 1)
    assert tg.decide('BUY', None, (-9.0, 29))[0] == tg.QUOTE              # too few samples: no evidence
    assert tg.decide('BUY', None, (-1.0, 99))[0] == tg.QUOTE              # inside the adverse-selection budget
    own = [-4.0, -3.5, -4.5, -3.8, -4.2, -3.9, -4.1, -4.0, -3.7, -4.3]
    assert tg.decide('SELL', None, None, own)[0] == tg.PULL
    assert tg.decide('SELL', None, None, own[:9])[0] == tg.QUOTE          # own_min_n 10
    assert tg.decide('SELL', None, None, [x + 2.0 for x in own])[:2] == (tg.SKEW, 1)
    assert tg.decide('SELL', None, None, [-4.0, 4.0] * 5)[0] == tg.QUOTE   # mean 0: not systematic
    with pytest.raises(ValueError):
        tg.decide('HOLD', None, None)
    with pytest.raises(ValueError):
        tg.ToxPolicy(skew_bps=4.0, pull_bps=3.0)
    assert p.vpin_max == 0.65 and p.pull_bps == 3.0


# ------------------------------------------------------------------ engine wiring
class PostRecorder(eng.MockVenue):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.posts, self.cancels = [], []

    async def limit_gtx(self, symbol, side, qty, price, cid):
        self.posts.append((cid, float(price)))
        await super().limit_gtx(symbol, side, qty, price, cid)

    async def cancel(self, symbol, cid):
        self.cancels.append(cid)
        await super().cancel(symbol, cid)


def guarded(tmp_path, mode='on', **kw):
    e, _, feed = mock_engine(tmp_path, tox_guard=mode, **kw)
    v = PostRecorder(e.p, quote=lambda s: (feed.bid or feed.price(), feed.ask or feed.price()))
    e.venue = v
    return e, v, feed


def order_rows(tmp_path):
    p = tmp_path / 'engine_orders.jsonl'
    return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines()] if p.exists() else []


async def test_toxic_vpin_withholds_the_quote_before_any_token(tmp_path):
    e, venue, feed = guarded(tmp_path)
    await e.sync()
    feed.vpin_now = lambda: 0.70
    issued = e.authorized
    res = await e.execute('BUY', BUY, 'SWARM')
    assert not res['ok'] and res['error'].startswith('TOX HOLD') and venue.posts == []
    assert e.authorized == issued                                          # no VESKA token spent on a held quote
    assert order_rows(tmp_path)[-1]['outcome'] == 'TOX_HOLD' and e.cooldown_until > e.clock()
    assert e.funnel.counters()['tox_hold'] == 1 and e.funnel.counters()['intents'] == 0


async def test_moderate_adverse_markouts_quote_one_tick_behind_the_touch(tmp_path):
    e, venue, feed = guarded(tmp_path)
    await e.sync()
    feed.markouts.view = lambda side, h=5.0: (-2.0, 60)
    touch = e.gtx_price('BUY', {**feed.features(e.clock()), **{k: v for k, v in e.book().items() if v is not None}})
    res = await e.execute('BUY', BUY, 'SWARM')
    assert venue.posts and venue.posts[0][1] == pytest.approx(touch - e.spec['tick'])
    assert not res['ok'] and order_rows(tmp_path)[-1]['tox'].startswith('SKEW')


async def test_toxicity_while_resting_pulls_the_quote(tmp_path):
    e, venue, feed = guarded(tmp_path, gtx_ttl_s=3.0)
    await e.sync()
    task = asyncio.ensure_future(e.execute('BUY', BUY, 'SWARM'))
    await asyncio.sleep(0.2)
    assert len(venue.posts) == 1
    feed.vpin_now = lambda: 0.72                                            # under the 0.80 pause, over 0.65
    res = await task
    assert not res['ok'] and res['error'].startswith('TOX PULL') and venue.cancels
    assert order_rows(tmp_path)[-1]['outcome'] == 'TOX_PULL'
    c = e.funnel.counters()
    assert c['tox_pull'] == 1 and c['cancels'] == 1 and c['intents'] == 1


async def test_shadow_and_off_never_change_the_quote(tmp_path):
    for mode in ('shadow', 'off'):
        d = tmp_path / mode
        d.mkdir()
        e, venue, feed = guarded(d, mode=mode)
        await e.sync()
        feed.vpin_now = lambda: 0.70
        await e.execute('BUY', BUY, 'SWARM')
        assert len(venue.posts) == 1                                        # quoted at the touch as before
        assert order_rows(d)[-1]['tox'] == ('PULL: VPIN 0.70 > 0.65' if mode == 'shadow' else None)


async def test_own_fill_markouts_feed_the_guard(tmp_path):
    e, venue, feed = guarded(tmp_path)
    e.own_mk5.extend([-4.0, -3.5, -4.5, -3.8, -4.2, -3.9, -4.1, -4.0, -3.7, -4.3])
    assert e.tox_check('BUY')[0] == tg.PULL
    assert e.quant_view()['own_mk5_n'] == 10 and e.quant_view()['tox_guard'] == 'on'


@pytest.mark.production_profile
def test_demo_turns_the_guard_on_and_live_stays_off():
    assert eng.PROFILES['demo'].tox_guard == 'on' and eng.PROFILES['live'].tox_guard == 'off'
    assert eng.PROFILES['demo'].tox_vpin_max == 0.65 and eng.PROFILES['demo'].vpin_gate
    assert 'TOX_PULL' in xf.SENT and 'TOX_PULL' in xf.CANCELS and 'TOX_HOLD' not in xf.SENT
