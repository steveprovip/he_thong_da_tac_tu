"""Phase 1 of docs/harness/plan_micro_adverse_selection.md: the harvest attempt log (core/harvest_markout.py) and its wiring in the engine.
Bookkeeping only: the engine tests assert that the orders are what they were without the log.  Mock venue, synthetic books."""
import asyncio
import json
import time
from dataclasses import replace

import pytest

from core import harvest_markout as hm
from tests.test_micro_rotation import BUY, SELL, doge_micro, rows, trade_through
CHOP = {'ker': 0.20, 'spread_bps': 0.5, 'ofi_10s': 0.6, 'atr_pct': 0.10, 'cvd_3m': 0.0, 'px_vel_60s': 0.0}


async def open_harvest(tmp_path, **prof):
    """An armed harvester whose feed reports a chop tape (KER 0.20, OFI +0.6), filled through the bid."""
    e, venue, feed = await doge_micro(tmp_path, micro_harvester='on', micro_harvest_ttl_s=3.0, scalp_min_edge_bps=0.0, scalp_align3=False, **prof)
    orig = feed.features
    feed.features = lambda *a, **k: {**orig(*a, **k), **CHOP}
    e.diag = {'consensus': 0.9, 'edge_bps': 40.0}
    task = asyncio.ensure_future(e.chop_scalp(BUY, feed.features()))
    await asyncio.sleep(0.5)
    await trade_through(feed)
    await task
    assert e.pos
    return e, venue, feed


def read(path):
    p = path
    return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines()] if p.exists() else []


# ============================================================================= the pure module
def test_signed_bps_follows_the_side_and_never_zero_fills_an_unknown():
    assert hm.signed_bps('BUY', 100.0, 100.1) == pytest.approx(10.0) and hm.signed_bps('SELL', 100.0, 100.1) == pytest.approx(-10.0)
    assert hm.signed_bps('SELL', 100.0, 99.9) == pytest.approx(10.0)
    for ref, px in ((None, 1.0), (1.0, None), (0.0, 1.0), (1.0, -1.0)):
        assert hm.signed_bps('BUY', ref, px) is None
    assert hm.rebase(100.0, 99.9, 100.0) == pytest.approx(99.9) and hm.rebase(100.0, None, 100.0) == 100.0


def make(tmp_path, **kw):
    t = [1000.0]
    return hm.MarkoutLog(tmp_path / 'hm.jsonl', clock=lambda: t[0], **kw), t


def test_an_unfilled_order_gets_its_quote_series_and_completes_once_it_is_closed(tmp_path):
    log, t = make(tmp_path)
    log.post('k1', 'XRPUSDT', 'SELL', 1.1000, 1.0990, 1.1000, {'ofi_10s': -0.5})                 # the Demo book sits 9 bps above mainnet
    assert log.open['k1']['basis_bps'] == pytest.approx(9.099, abs=1e-3) and log.open['k1']['quote_ref'] == pytest.approx(1.0990)
    for dt, sig, ven in ((1, 1.0990, 1.1000), (5, 1.0985, 1.0995), (15, 1.0980, 1.0990), (30, 1.0975, 1.0985), (60, 1.0970, 1.0980)):
        t[0] = 1000.0 + dt
        log.sample(sig, ven)
        if dt < 60:
            assert 'k1' in log.open
    assert 'k1' in log.open                                                                  # full quote series but not closed: it may still fill
    log.close('k1')
    log.sample(1.0970, 1.0980)
    [r] = read(log.path)
    assert r['complete'] is True and r['filled'] is False and r['features'] == {'ofi_10s': -0.5}
    assert r['q_sig'] == {'1s': 0.0, '5s': pytest.approx(4.55, abs=0.01), '15s': pytest.approx(9.1, abs=0.01),
                          '30s': pytest.approx(13.65, abs=0.01), '60s': pytest.approx(18.2, abs=0.01)}        # SELL: price falls = +
    assert r['q_ven']['60s'] == pytest.approx(18.18, abs=0.01) and r['f_sig'] == {}


def test_a_filled_order_gets_a_second_series_from_the_fill_and_waits_for_it(tmp_path):
    log, t = make(tmp_path)
    log.post('k2', 'DOGEUSDT', 'BUY', 0.0700, 0.0700, 0.0700)
    t[0] = 1010.0
    log.filled('k2', 0.0700, 0.0700, 0.0700, {'ker': 0.2})
    for dt in (11, 15, 25, 40, 69):
        t[0] = 1000.0 + dt
        log.sample(0.0700 * (1 + dt / 1e5), 0.0700)
    assert read(log.path) == []                                                              # the fill series needs fill + 60 s = 1070
    t[0] = 1070.0
    log.sample(0.07007, 0.0700)
    [r] = read(log.path)
    assert r['complete'] is True and r['filled'] is True and r['fill_features'] == {'ker': 0.2}
    assert set(r['f_sig']) == {'1s', '5s', '15s', '30s', '60s'} and r['f_sig']['60s'] == pytest.approx(10.0)
    assert r['f_ven']['60s'] == 0.0 and r['t_fill'] == 1010.0 and r['t_post'] == 1000.0


def test_an_old_record_is_written_incomplete_with_the_horizons_reached(tmp_path):
    log, t = make(tmp_path, max_age_s=100.0)
    log.post('k3', 'XRPUSDT', 'BUY', 1.0, 1.0, 1.0)
    t[0] = 1020.0
    log.sample(1.0001, 1.0)
    t[0] = 1101.0
    log.sample(1.0002, 1.0)
    [r] = read(log.path)
    assert r['complete'] is False and set(r['q_sig']) == {'1s', '5s', '15s', '30s', '60s'} and r['f_sig'] == {}      # never filled, never closed
    assert 'k3' not in log.open


def test_missing_prices_are_none_not_zero(tmp_path):
    log, t = make(tmp_path)
    log.post('k4', 'XRPUSDT', 'BUY', 1.0, None, None)
    log.close('k4')
    t[0] = 1061.0
    log.sample(None, None)
    [r] = read(log.path)
    assert r['q_sig']['60s'] is None and r['q_ven']['60s'] is None and r['sig0'] is None and r['basis_bps'] is None


def test_the_open_set_is_bounded_and_the_log_never_raises(tmp_path):
    log, t = make(tmp_path, max_open=2)
    for i in range(3):
        t[0] += 1
        log.post(f'k{i}', 'XRPUSDT', 'BUY', 1.0, 1.0, 1.0)
    assert set(log.open) == {'k1', 'k2'} and [r['key'] for r in read(log.path)] == ['k0']      # the oldest was flushed to make room
    blocker = tmp_path / 'file'
    blocker.write_text('x')
    bad = hm.MarkoutLog(blocker / 'sub' / 'hm.jsonl', clock=lambda: t[0])                       # a path that cannot be created
    bad.post('a', 'XRPUSDT', 'BUY', 1.0, 1.0, 1.0)
    assert bad.flush() == 0 and bad.errors == 1 and bad.written == 0


# ============================================================================= the engine wiring
async def test_a_harvest_fill_is_logged_with_both_series_and_joins_the_ledger_key(tmp_path):
    e, venue, feed = await open_harvest(tmp_path)
    assert e.pos and e.pos['harvest_key'] and list(e.harvest_log.open) == [e.pos['harvest_key']]
    r = e.harvest_log.open[e.pos['harvest_key']]
    assert r['filled'] is True and r['side'] == 'BUY' and r['symbol'] == 'DOGEUSDT' and r['qty_req'] and r['policy'] == 'legacy'
    assert r['features']['ofi_10s'] == pytest.approx(0.6) and r['features']['ker'] == pytest.approx(0.2)
    assert r['features']['queue_qty'] is not None and 'basis_bps' in r['features'] and r['features']['score'] > 0.76
    assert r['fill_features'] is not None and r['fill_ref'] is not None
    now = time.time()
    e.clock = lambda: now + 61.0                                                            # every horizon is due
    e.hlog_step({'price': feed.price()})
    [row] = read(e.harvest_log.path)
    assert row['complete'] is True and set(row['f_sig']) == {'1s', '5s', '15s', '30s', '60s'} == set(row['q_sig'])
    assert all(isinstance(v, float) for v in row['f_sig'].values())
    assert [o['harvest_key'] for o in rows(tmp_path) if o.get('outcome') == 'FILLED'] == [row['key']]     # the order row carries the same key


async def test_an_unfilled_harvest_order_is_logged_as_a_quote_series(tmp_path):
    e, venue, feed = await doge_micro(tmp_path, micro_harvester='on', micro_harvest_ttl_s=0.3, scalp_min_edge_bps=0.0, scalp_align3=False)
    e.diag = {'consensus': 0.9, 'edge_bps': 40.0}
    f = {**feed.features(), 'ker': 0.20, 'spread_bps': 0.5, 'ofi_10s': 0.6, 'atr_pct': 0.10, 'cvd_3m': 0.0, 'px_vel_60s': 0.0}
    await e.chop_scalp(BUY, f)
    assert e.pos is None and len(e.harvest_log.open) == 1
    key = next(iter(e.harvest_log.open))
    now = time.time()
    e.clock = lambda: now + 61.0
    e.hlog_step({'price': feed.price()})
    [row] = read(e.harvest_log.path)
    assert row['key'] == key and row['filled'] is False and row['complete'] is True and row['f_sig'] == {}
    assert set(row['q_sig']) == {'1s', '5s', '15s', '30s', '60s'}
    assert [o['harvest_key'] for o in rows(tmp_path) if o.get('outcome') == 'UNFILLED'] == [key]


async def test_the_log_changes_no_order_and_a_failing_log_cannot_stop_one(tmp_path):
    on, _, _ = await open_harvest(tmp_path / 'on')
    off, _, _ = await open_harvest(tmp_path / 'off', micro_harvest_log=False)
    assert off.harvest_log is None and not (tmp_path / 'off' / 'harvest_markouts.jsonl').exists()
    for k in ('side', 'qty', 'tp', 'sl', 'eject_s', 'path'):
        assert on.pos[k] == off.pos[k], k                                                   # same order, same bracket, same schedule
    assert 'harvest_key' in on.pos and off.pos['harvest_key'] is None
    broken = tmp_path / 'broken'
    broken.mkdir()
    e, venue, feed = await doge_micro(broken, micro_harvester='on', micro_harvest_ttl_s=3.0, scalp_min_edge_bps=0.0, scalp_align3=False)
    e.harvest_log.post = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('disk on fire'))
    e.diag = {'consensus': 0.9, 'edge_bps': 40.0}
    f = {**feed.features(), 'ker': 0.20, 'spread_bps': 0.5, 'ofi_10s': 0.6, 'atr_pct': 0.10, 'cvd_3m': 0.0, 'px_vel_60s': 0.0}
    task = asyncio.ensure_future(e.chop_scalp(BUY, f))
    await asyncio.sleep(0.5)
    await trade_through(feed)
    await task
    assert e.pos and e.harvest_log.errors == 1                                              # the order filled anyway
    assert any('HARVEST LOG error RuntimeError' in x['message'] for x in e.events)


async def test_only_harvest_orders_are_logged(tmp_path):
    e, venue, feed = await doge_micro(tmp_path, gtx_ttl_s=0.2)
    await e.execute('BUY', BUY, 'SWARM')
    await e.execute('SELL', SELL, 'SCALP')                                                  # a direct legacy scalp, not the harvester
    assert e.harvest_log.open == {} and not e.harvest_log.path.exists()
