"""tools/replay_overnight_exits.py: the exit-rule replay of the logged chop-harvester entries.  Synthetic prints only, no network:
fetch_window is driven by a fake opener.  These tests pin the fill / fee / event-order mechanics; they say nothing about the market."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import replay_overnight_exits as ro  # noqa: E402

XRP = {'symbol': 'XRPUSDT', 'side': 'SELL', 'entry': 1.1000, 'qty': 4.6, 't0': 1000.0, 't_exit': 1100.0, 'net': 0.0, 'reason': 'x', 'hold_s': 100.0}
LONG = {**XRP, 'side': 'BUY'}


def path(points, trades_every=1.0):
    """[(sec_after_entry, price)] -> a print stream; each leg alternates a buyer- and a seller-initiated print at the price."""
    out = []
    for s, px in points:
        out += [(1000.0 + s, px, False), (1000.0 + s + 0.1, px, True)]
    return out


def maker_fee(tr, exit_px):
    return tr['qty'] * (tr['entry'] + exit_px) * 2.0 / 1e4


# ============================================================================= barriers and fees
def test_the_tp_fills_every_policy_with_a_maker_exit_and_the_fees_of_the_ledger():
    ticks = path([(5, 1.1000), (20, 1.0990), (30, 1.0983)])                     # SELL: price falls through the TP 1.0983500
    for pol in ro.POLICIES:
        r = ro.simulate(ticks, XRP, pol)
        assert r['reason'] == 'TP' and r['exit_px'] == pytest.approx(1.1 * (1 - 0.0015)) and r['maker']
        gross = 4.6 * (1.1 - r['exit_px'])
        assert r['net'] == pytest.approx(gross - maker_fee(XRP, r['exit_px']))     # +0.00759 gross, -0.00203 fees
    assert ro.simulate(ticks, XRP, 'barriers')['net'] == pytest.approx(0.00759 - 0.00203, abs=2e-5)


def test_the_stop_is_taker_with_one_bp_of_slippage_and_wins_the_tie_with_the_tp():
    ticks = path([(5, 1.1000), (10, 1.1017)])                                   # SELL stopped at 1.10165
    r = ro.simulate(ticks, XRP, 'barriers')
    stop = 1.1 * 1.0015
    assert r['reason'] == 'SL' and not r['maker'] and r['exit_px'] == pytest.approx(stop * (1 + 1e-4))
    assert r['net'] == pytest.approx(4.6 * (1.1 - r['exit_px']) - 4.6 * (1.1 * 2.0 + r['exit_px'] * 5.0) / 1e4)
    assert r['net'] == pytest.approx(-0.0117, abs=3e-4)                         # the -0.23 % of a 5.06 U lot


def test_a_long_is_the_mirror_image():
    up = ro.simulate(path([(5, 1.1000), (30, 1.1017)]), LONG, 'barriers')
    assert up['reason'] == 'TP' and up['exit_px'] == pytest.approx(1.1 * 1.0015)
    dn = ro.simulate(path([(5, 1.1000), (10, 1.0983)]), LONG, 'barriers')
    assert dn['reason'] == 'SL' and dn['exit_px'] < 1.1 * (1 - 0.0015)


def test_the_conservative_model_needs_a_print_through_the_price():
    tp = 1.1 * (1 + -1 * 0.15 / 100.0)                                          # the simulator's own TP float
    ticks = [(1001.0, 1.1000, False), (1002.0, tp, True)]                       # a seller-initiated print AT the TP, no further
    assert ro.simulate(ticks, XRP, 'barriers', 'opt')['reason'] == 'TP'
    assert ro.simulate(ticks, XRP, 'barriers', 'cons')['reason'] == 'HORIZON'


def test_nothing_before_the_entry_counts_and_the_horizon_exits_at_the_last_print():
    ticks = [(900.0, 1.0, True)] + path([(5, 1.0999), (400, 1.0995), (2000, 1.0)])   # the last print is past the horizon
    r = ro.simulate(ticks, XRP, 'barriers')
    assert r['reason'] == 'HORIZON' and r['exit_px'] == pytest.approx(1.0995) and r['maker']


# ============================================================================= the three exit machines
def test_legacy_scratches_a_small_winner_at_45s_where_barriers_wait_for_the_tp():
    ticks = path([(5, 1.0999), (30, 1.0995), (50, 1.0994), (60, 1.0994), (400, 1.0983)])      # +0.055 % at 50 s, TP only at 400 s
    leg, bar = ro.simulate(ticks, XRP, 'legacy'), ro.simulate(ticks, XRP, 'barriers')
    assert leg['reason'] == 'BE' and 45.0 <= leg['held_s'] <= 62.0 and leg['exit_px'] == pytest.approx(1.0994)
    assert 0 < leg['net'] < 0.001                                                # a scratch: well under one TP (+0.0056 U), like the ledger's
    assert bar['reason'] == 'TP' and bar['net'] > 5 * leg['net']


def test_legacy_rests_at_the_floor_then_leaves_at_the_touch_after_the_escalation_time():
    ticks = path([(5, 1.1000), (40, 1.1006), (100, 1.1008), (190, 1.1009), (230, 1.1011), (260, 1.1011)])   # SELL going the wrong way
    r = ro.simulate(ticks, XRP, 'legacy')
    assert r['reason'] == 'EJECT' and r['held_s'] >= 215.0 and r['exit_px'] == pytest.approx(1.1011)
    assert r['net'] == pytest.approx(4.6 * (1.1 - 1.1011) - maker_fee(XRP, 1.1011)) and r['net'] < -0.005
    barr = ro.simulate(ticks, XRP, 'barriers')
    assert barr['reason'] == 'HORIZON'                                           # the stop 1.10165 was never reached: it just sits
    assert ro.simulate(ticks, XRP, 'legacy', cfg={**ro.CFG, 'escalate_s': 400.0})['reason'] == 'HORIZON'


def test_legacy_floor_is_the_fee_true_break_even_a_scratch_never_books_a_loss_on_a_floor_fill():
    ticks = path([(5, 1.1000), (60, 1.1003), (80, 1.1001), (100, 1.0995)])      # the SELL comes back through the floor 1.09956
    r = ro.simulate(ticks, XRP, 'legacy')
    assert r['reason'] == 'BE' and r['exit_px'] <= 1.1 * (1 - 4e-4) + 1e-9      # filled at the touch, which is on the floor or beyond it
    assert 0 <= r['net'] < 0.001                                                 # the scratch never books a loss


def test_patient_needs_both_the_age_and_a_trade_that_ran_60_percent_of_the_tp():
    quiet = path([(5, 1.0999), (200, 1.0996), (300, 1.0996), (400, 1.0996)])    # MFE 0.036 % only
    assert ro.simulate(quiet, XRP, 'patient')['reason'] == 'HORIZON'            # no scratch: the trade runs on
    ran = path([(5, 1.0999), (100, 1.0988), (170, 1.0989), (190, 1.0991), (200, 1.0991), (240, 1.0991)])   # MFE 0.10 %, back to +0.08 %
    r = ro.simulate(ran, XRP, 'patient')
    assert r['reason'] == 'BE' and r['held_s'] >= 180.0 and r['exit_px'] == pytest.approx(1.0991)
    tick = ro.TICK['XRPUSDT']
    assert r['exit_px'] <= 1.1 * (1 - 4e-4) - tick + 1e-9                        # at or better than floor + 1 tick


def test_a_stop_before_the_timers_ends_every_policy_alike():
    ticks = path([(5, 1.1000), (20, 1.1018)])
    assert {p: ro.simulate(ticks, XRP, p)['reason'] for p in ro.POLICIES} == {'legacy': 'SL', 'patient': 'SL', 'barriers': 'SL'}


# ============================================================================= ledger, summary, fetch
def test_load_trades_keeps_real_harvest_rows_only(tmp_path):
    rows = [
        {'t': 2000.0, 'bracket_src': 'HARVEST', 'tag': 'AUTO', 'symbol': 'XRPUSDT', 'side': 'SELL', 'entry': 1.1, 'qty': 4.6, 'hold_s': 100, 'pnl': 0.0003, 'reason': 'EJECT-BE'},
        {'t': 2001.0, 'bracket_src': 'HARVEST', 'tag': 'AUTO', 'symbol': '1000PEPEUSDT', 'side': 'BUY', 'entry': 0.005, 'qty': 1.0, 'hold_s': 54, 'pnl': 1e-5, 'reason': 'EJECT-BE'},
        {'t': 2002.0, 'bracket_src': 'SCALP', 'tag': 'AUTO', 'symbol': 'XRPUSDT', 'side': 'SELL', 'entry': 1.1, 'qty': 4.6, 'hold_s': 77, 'pnl': 0.003, 'reason': 'EJECT-BE'},
        {'t': 10.0, 'bracket_src': 'HARVEST', 'tag': 'AUTO', 'symbol': 'XRPUSDT', 'side': 'SELL', 'entry': 1.1, 'qty': 4.6, 'hold_s': 5, 'pnl': 0.1, 'reason': 'TP'},
    ]
    p = tmp_path / 'l.jsonl'
    p.write_text('\n'.join(json.dumps(r) for r in rows), encoding='utf-8')
    got = ro.load_trades(p, since=1000.0)
    assert [(t['symbol'], t['t0'], t['net']) for t in got] == [('XRPUSDT', 1900.0, 0.0003)]      # dust, non-harvest and old rows dropped


def test_summary_and_the_legacy_vs_ledger_honesty_check():
    trades = [XRP, LONG]
    ticks = [path([(5, 1.0999), (30, 1.0995), (50, 1.0994)]), path([(5, 1.1000), (30, 1.1017)])]
    res = ro.run(trades, ticks)
    s = ro.summarize(trades, res)
    assert s['ledger'] == {'n': 2, 'net_usdt': 0.0}
    assert s['opt']['legacy']['n'] == 2 and s['opt']['barriers']['reasons'].get('TP') == 1
    assert set(s) == {'cons', 'opt', 'ledger'}


class FakeResp:
    def __init__(self, payload, used=10):
        self.body, self.headers = json.dumps(payload).encode(), {'X-MBX-USED-WEIGHT-1M': str(used)}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


def test_fetch_pages_by_from_id_stops_at_the_window_end_and_never_sends_anything_but_a_get():
    pages = [[{'a': i, 'T': 1_000_000 + i, 'p': '1.1', 'q': '10', 'm': bool(i % 2)} for i in range(1000)],
             [{'a': 1000 + i, 'T': 1_001_000 + i, 'p': '1.2', 'q': '5', 'm': False} for i in range(10)]]
    seen, sleeps = [], []

    def opener(req, timeout=0):
        seen.append(req)
        return FakeResp(pages[len(seen) - 1], used=1600)

    rows = ro.fetch_window('XRPUSDT', 1_000_000, 1_001_004, opener=opener, sleep=sleeps.append, log=lambda *a: None)
    assert len(seen) == 2 and all(r.get_method() == 'GET' and r.data is None for r in seen)
    assert 'startTime=1000000' in seen[0].full_url and 'fromId=1000' in seen[1].full_url and 'startTime' not in seen[1].full_url
    assert len(rows) == 1000 + 5 and rows[-1][0] <= 1_001_004                      # truncated at the window end
    assert sleeps == [12.0]                                                       # the weight header (1 600 > 1 500) slows the pager
    assert dict(seen[0].header_items())['User-agent'] == 'replay-overnight-exits/1'   # a generic client name, nothing personal


def test_fetch_rejects_a_non_list_answer():
    def opener(req, timeout=0):
        return FakeResp({'code': -1003, 'msg': 'Too many requests'})
    with pytest.raises(RuntimeError, match='unexpected aggTrades answer'):
        ro.fetch_window('XRPUSDT', 0, 10, opener=opener, sleep=lambda s: None)
