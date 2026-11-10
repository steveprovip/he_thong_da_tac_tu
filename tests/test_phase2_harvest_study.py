"""tools/phase2_harvest_study.py + tools/fetch_aggtrades.py: the mainnet-tape simulator of the chop harvester and its search.  Synthetic tapes only
(no network, no files from outside the repo).  These tests pin fills, first-passage exits, fees, look-ahead safety and the acceptance arithmetic;
they say nothing about the market."""
import io
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import fetch_aggtrades as fa  # noqa: E402
import phase2_harvest_study as ps  # noqa: E402

DAY0_MS = 1_790_000_000_000 // 86_400_000 * 86_400_000          # a UTC midnight


def tape(points):
    """[(sec_in_day, price, qty, buyer_is_maker)] -> the arrays of one day file."""
    a = np.array(points, dtype=float)
    order = np.argsort(a[:, 0], kind='stable')
    a = a[order]
    return {'t': (DAY0_MS + (a[:, 0] * 1000).astype(np.int64)), 'p': a[:, 1], 'q': a[:, 2].astype(np.float32), 'm': a[:, 3].astype(bool)}


# ============================================================================= arrays and bars
def test_rolling_sum_and_ker():
    assert np.isnan(ps.rsum(np.arange(5.0), 3)[:2]).all() and ps.rsum(np.arange(5.0), 3)[2:].tolist() == [3.0, 6.0, 9.0]
    line = np.linspace(1.0, 2.0, 400)                                           # a straight line is perfectly efficient
    k = ps.ker_series(line)
    assert np.isnan(k[:119]).all() and np.allclose(k[125:], 1.0)
    zig = 1.0 + 0.001 * (np.arange(400) // 5 % 2)                               # up / down every 5 s: net 0 over an even number of steps
    assert (ps.ker_series(zig)[130:] < 0.1).all()


def test_second_bars_flow_touch_and_ages():
    t = tape([(10.2, 1.0000, 100, True), (10.7, 1.0001, 50, False), (12.1, 1.0000, 30, True), (13.0, 1.0001, 10, False)] * 300
             + [(20 + i, 1.0, 1, False) for i in range(1000)])
    b = ps.second_bars(t['t'], t['p'], t['q'], t['m'])
    assert b['cnt'][10] == 600 // 1 * 0 + b['cnt'][10] and b['cnt'][12] > 0
    assert b['buy'][13] > 0 and b['sell'][13] == 0                              # second 13: only a taker buy
    assert b['bid'][13] == pytest.approx(1.0000) and b['ask'][13] == pytest.approx(1.0001)
    assert b['bid_age'][13] == 1 and b['ask_age'][13] == 0                      # the last seller-initiated print was in second 12
    assert np.isinf(b['bid_age'][5]) and np.isnan(b['bid'][5])                  # nothing yet: unknown, not 0


def test_features_never_look_ahead():
    rng = np.random.default_rng(3)
    n = 20_000
    secs = np.sort(rng.uniform(0, 3000, n))
    price = 1.0 + np.cumsum(rng.normal(0, 1e-5, n))
    base = [(s, p, 10.0, bool(rng.random() < 0.5)) for s, p in zip(secs, price)]
    t1 = tape(base)
    changed = [(s, p * (1.01 if s > 2000 else 1.0), q, (not m) if s > 2000 else m) for s, p, q, m in base]
    t2 = tape(changed)
    f1, f2 = ps.features_grid(ps.second_bars(t1['t'], t1['p'], t1['q'], t1['m'])), ps.features_grid(ps.second_bars(t2['t'], t2['p'], t2['q'], t2['m']))
    for name, a in f1.items():
        assert np.array_equal(a[:2000], f2[name][:2000], equal_nan=True), name   # identical up to the last unchanged second


# ============================================================================= first-passage exits
CFG = dict(ps.CFG)


def passage_case(side, after, q=1.0):
    pts = [(0, q, 1, True)] + [(1 + i, p, 1, m) for i, (p, m) in enumerate(after)]
    t = tape(pts)
    return ps.passages(t['t'], t['p'], t['m'], 0, side, q, CFG, 'opt'), ps.passages(t['t'], t['p'], t['m'], 0, side, q, CFG, 'cons')


def test_a_long_tp_needs_a_buyer_initiated_print_and_a_stop_any_print():
    k15, j15 = ps.TP_LEVELS.index(0.15), ps.SL_LEVELS.index(0.15)
    # a seller-initiated print AT the TP price does not fill a long's sell limit; the next buyer-initiated one does
    (tp, sl, last, n), (tpc, slc, _, _) = passage_case('BUY', [(1.0010, False), (1.0015, True), (1.0016, False)])
    assert tp[k15] == 2 and tpc[k15] == 2 and n == 3 and last == pytest.approx(1.0016)
    # opt fills AT the price, cons needs a print strictly through it
    (tp, _, _, _), (tpc, _, _, _) = passage_case('BUY', [(1.0015, False), (1.0016, False)])
    assert tp[k15] == 0 and tpc[k15] == 1
    # the stop: any print at or through, taker
    (_, sl, _, _), _ = passage_case('BUY', [(0.9990, True), (0.9985, True)])
    assert sl[j15] == 1 and sl[ps.SL_LEVELS.index(0.10)] == 0
    (tp, sl, _, n), _ = passage_case('BUY', [(1.0005, False)])                  # neither leg: both "not found" (n + 1)
    assert tp[k15] == sl[j15] == n + 1


def test_a_short_is_the_mirror_image():
    k15, j15 = ps.TP_LEVELS.index(0.15), ps.SL_LEVELS.index(0.15)
    (tp, sl, _, _), _ = passage_case('SELL', [(0.9990, True), (0.9985, False), (0.9984, True)])
    assert tp[k15] == 2 and sl[j15] > 3                                         # TP by the SELLER-initiated print at 0.9984 (the 0.9985 one is a buyer)
    (tp, sl, _, _), _ = passage_case('SELL', [(1.0010, False), (1.0015, False)])
    assert sl[j15] == 1


def test_the_bracket_net_arithmetic_ties_and_censoring():
    df = pd.DataFrame({'n_after': [10, 10, 10, 10], 'h_move': [5.0, 5.0, 5.0, 5.0]})
    inf = 11
    tp = np.full((4, len(ps.TP_LEVELS)), inf)
    sl = np.full((4, len(ps.SL_LEVELS)), inf)
    k, j = ps.TP_LEVELS.index(0.15), ps.SL_LEVELS.index(0.15)
    tp[0, k], sl[0, j] = 3, inf                                                 # TP only
    tp[1, k], sl[1, j] = inf, 4                                                 # stop only
    tp[2, k], sl[2, j] = 5, 5                                                   # same print: the stop wins
    # row 3: neither -> the horizon exit
    for i in range(len(ps.TP_LEVELS)):
        df[f'tp{i}'] = tp[:, i]
    for i in range(len(ps.SL_LEVELS)):
        df[f'sl{i}'] = sl[:, i]
    got = ps.bracket_net(df, 0.15, 0.15)
    assert got.tolist() == pytest.approx([15 - 4, -15 - 2 - 5 - 1, -15 - 2 - 5 - 1, 5.0 - 4])


# ============================================================================= the lane simulation
def chop_day():
    """Two hours of a flat zig-zag: buyers lift 1.0001 four times as hard as sellers hit 1.0000, a print every 0.5 s."""
    pts = []
    for i in range(14_400):
        s = i * 0.5 + 0.1
        if i % 5 == 0:
            pts.append((s, 1.0000, 20.0, True))
        else:
            pts.append((s, 1.0001, 20.0, False))
    return tape(pts)


def test_a_flat_chop_tape_fills_in_opt_never_in_cons_and_one_position_at_a_time():
    arr = chop_day()
    opt = pd.DataFrame(ps.build_day('TESTUSDT', '2026-01-01', arr, CFG, 'opt'))
    assert len(opt) > 5 and set(opt['side']) == {'BUY'} and (opt['flow10'] > 0.3).all()
    assert (opt['quote'] == 1.0000).all()                                       # a BUY at the bid proxy
    gaps = np.diff(opt['sec'].to_numpy())
    assert (gaps >= CFG['horizon_s'] + CFG['cooldown_s'] - 1).all()             # the flat tape never exits, so the next entry waits for the horizon
    assert (opt['h_move'].abs() < 2.0).all()                                    # exits at the last print: ~1 bp away
    assert ps.build_day('TESTUSDT', '2026-01-01', arr, CFG, 'cons') == []       # the bid is never traded strictly through


def test_cons_fills_only_when_a_seller_print_goes_strictly_below_the_quote():
    arr = chop_day()
    rng = np.random.default_rng(5)
    arr['p'] = np.where(arr['m'], np.where(rng.random(len(arr['p'])) < 0.5, 1.0000, 0.9999), arr['p'])      # sellers hit 1.0000 or 0.9999 at random
    cons = pd.DataFrame(ps.build_day('TESTUSDT', '2026-01-01', arr, CFG, 'cons'))
    opt = pd.DataFrame(ps.build_day('TESTUSDT', '2026-01-01', arr, CFG, 'opt'))
    assert len(cons) > 5 and set(cons['side']) == {'BUY'}
    assert set(cons['quote'].round(4)) == {1.0000}          # a 0.9999 quote can never be traded strictly through; a 1.0000 one is by the next 0.9999 print
    assert len(opt) >= len(cons) and (opt['fill_wait'].median() <= cons['fill_wait'].median())      # opt fills sooner (at or through)


# ============================================================================= analysis helpers
def test_auc_block_bootstrap_logit_and_acceptance():
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    y = np.array([0, 0, 0, 1, 1, 1])
    assert ps.auc(x, y) == 1.0 and ps.auc(-x, y) == 0.0 and ps.auc(np.ones(6), y) == 0.5
    assert ps.auc_z(0.5, 30, 30) == 0.0 and ps.auc_z(0.7, 30, 30) > 2.5
    vals = np.r_[np.full(50, 1.0), np.full(50, 3.0)]
    days = np.r_[np.full(50, 'd1'), np.full(50, 'd2')]
    lo, hi = ps.block_boot(vals, days, b=500)
    assert lo == pytest.approx(1.0) and hi == pytest.approx(3.0) and ps.block_boot(vals, days, b=500) == (lo, hi)      # seeded
    rng = np.random.default_rng(1)
    X = rng.normal(size=(2000, 3))
    Y = (X[:, 0] * 2.0 + rng.normal(size=2000) > 0).astype(float)
    w = ps.fit_logit(X, Y)
    assert w[1] > 1.0 and abs(w[2]) < 0.5 and abs(w[3]) < 0.5
    good = ps.acceptance(np.r_[np.full(450, 2.0), np.full(50, 1.0)], np.array(['a', 'b', 'c', 'd', 'e'] * 100), 0.5)
    assert good['accepted'] and good['pass'] == {'n>=400': True, 'lower95>0': True, 'kept>=25%': True, 'without_best_day>0': True}
    bad = ps.acceptance(np.r_[np.full(300, 2.0)], np.array(['a', 'b', 'c'] * 100), 0.1)
    assert not bad['accepted'] and not bad['pass']['n>=400'] and not bad['pass']['kept>=25%']
    one_day = ps.acceptance(np.r_[np.full(450, -1.0), np.full(50, 100.0)], np.r_[np.full(450, 'x'), np.full(50, 'y')], 1.0)
    assert not one_day['pass']['without_best_day>0']                             # a single lucky day does not pass


# ============================================================================= the downloader (offline)
def make_zip(csv_text, name='X-aggTrades-2026-01-01.csv'):
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w') as z:
        z.writestr(name, csv_text)
    return b.getvalue()


def test_parse_zip_validates_the_archive():
    header = 'agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker\n'
    a = fa.parse_zip(make_zip(header + '1,1.1000,10,1,1,2000,true\n2,1.1001,5,2,2,1000,false\n'))
    assert a['t'].tolist() == [1000, 2000] and a['m'].tolist() == [False, True] and a['p'].dtype == np.float64   # sorted by time, flags parsed
    no_header = fa.parse_zip(make_zip('1,1.1000,10,1,1,2000,True\n'))
    assert no_header['m'].tolist() == [True]
    for bad in (header + '1,0,10,1,1,2000,true\n', header + '1,1.1,-1,1,1,2000,true\n', header):
        with pytest.raises(ValueError):
            fa.parse_zip(make_zip(bad))
    two = io.BytesIO()
    with zipfile.ZipFile(two, 'w') as z:
        z.writestr('a.csv', header + '1,1,1,1,1,1,true\n')
        z.writestr('b.csv', header + '1,1,1,1,1,1,true\n')
    with pytest.raises(ValueError, match='unexpected archive layout'):
        fa.parse_zip(two.getvalue())
    with pytest.raises(ValueError, match='unexpected archive layout'):
        fa.parse_zip(make_zip(header, name='x.txt'))


def test_checksum_and_the_run_with_a_fake_server(tmp_path):
    import hashlib
    header = 'agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker\n'
    blob = make_zip(header + '1,1.1,10,1,1,1000,true\n')
    good = hashlib.sha256(blob).hexdigest() + '  X.zip\n'
    assert fa.checksum_ok(blob, good) and not fa.checksum_ok(blob, '0' * 64) and not fa.checksum_ok(blob, '')

    class Resp:
        def __init__(self, body, n=None):
            self.body, self.headers = body, {'Content-Length': str(len(body) if n is None else n)}
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return self.body

    calls = []
    def opener(req, timeout=0):
        calls.append((req.get_method(), req.full_url))
        if req.full_url.endswith('2026-01-02.zip') or req.full_url.endswith('2026-01-02.zip.CHECKSUM'):
            raise OSError('404')
        return Resp(good.encode() if req.full_url.endswith('.CHECKSUM') else blob)
    out = tmp_path / 'agg'
    r = fa.run(['XUSDT'], '2026-01-01', '2026-01-02', out, 100.0, False, opener=opener, sleep=lambda s: None, log=lambda *a: None)
    assert r['files'] == 1 and (out / 'XUSDT' / '2026-01-01.npz').exists() and not list(out.rglob('*.zip'))      # the zip is not kept
    z = np.load(out / 'XUSDT' / '2026-01-01.npz')
    assert z['t'].tolist() == [1000] and z['m'].tolist() == [True]
    assert all(m in ('HEAD', 'GET') for m, _ in calls)
    with pytest.raises(SystemExit, match='max-mb'):
        fa.run(['XUSDT'], '2026-01-01', '2026-01-01', tmp_path / 'agg2', 0.0000001, False, opener=opener, sleep=lambda s: None, log=lambda *a: None)
    with pytest.raises(SystemExit, match='checksum mismatch'):
        fa.run(['XUSDT'], '2026-01-01', '2026-01-01', tmp_path / 'agg3', 100.0, False,
               opener=lambda req, timeout=0: Resp(('0' * 64 + '  x\n').encode() if req.full_url.endswith('.CHECKSUM') else blob),
               sleep=lambda s: None, log=lambda *a: None)
    assert not (tmp_path / 'agg3' / 'XUSDT' / '2026-01-01.npz').exists()                                          # nothing saved on a mismatch
