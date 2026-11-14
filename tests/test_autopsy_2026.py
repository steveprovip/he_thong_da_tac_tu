"""tools/autopsy_2026.py: the additive loss decomposition, the Demo fee split, tape access and path helpers."""
import datetime as dt

import numpy as np
import pytest

from tools import autopsy_2026 as ap


def row(**kw):
    r = {'tag': 'AUTO', 'side': 'BUY', 'qty': 100.0, 'entry': 1.0, 'exit': 1.001, 'gross': 0.1,
         'fee': 100 * 1.0 * 2e-4 + 100 * 1.001 * 4e-4, 'markout_bps': {'1s': [0.5, 1.0], '30s': [3.0, 30.1]}}
    r.update(kw)
    r['pnl'] = r['gross'] - r['fee']
    return r


def test_decomposition_is_additive_and_exact():
    d = ap.decompose(row())
    assert d['notional'] == pytest.approx(100.0)
    assert d['fill_mk'] == pytest.approx(100.0 * 3.0 / 1e4) and d['mk_h'] == '30s'      # 30 s preferred
    assert d['fill_mk'] + d['body'] == pytest.approx(d['gross'])
    assert d['gross'] - d['fee'] == pytest.approx(d['net'])


def test_demo_fee_split_recovers_maker_entry_and_taker_exit():
    d = ap.decompose(row())
    assert d['fee_taker_est'] == pytest.approx(100 * 1.001 * 4e-4)       # the taker leg at 4 bps (Demo)
    assert d['fee_maker_est'] == pytest.approx(100 * 1.0 * 2e-4)
    all_maker = ap.decompose(row(fee=(100 * 1.0 + 100 * 1.001) * 2e-4))
    assert all_maker['fee_taker_est'] == pytest.approx(0.0, abs=1e-12)


def test_unknown_markout_stays_unknown():
    d = ap.decompose(row(markout_bps={}))
    assert d['fill_mk'] is None and d['body'] is None
    assert ap.markout_pick({'5s': [1.5, 5.0]}) == (1.5, '5s') and ap.markout_pick(None) == (None, None)


def test_first_touch_and_reaches_after():
    t = np.arange(6, dtype=np.int64) * 1000
    p = np.array([100.0, 100.1, 100.3, 99.6, 100.6, 101.0])
    assert ap.first_touch(t, p, 1, 0.005, 0.003, 10_000)[0] == 'SL'      # -0.4 % at t=3 s before +0.6 % at t=4 s
    assert ap.first_touch(t, p, 1, 0.002, 0.005, 10_000)[0] == 'TP'      # +0.3 % at t=2 s
    assert ap.first_touch(t, p, 1, 0.02, 0.02, 10_000)[0] == 'TIME'
    assert ap.first_touch(t, p, -1, 0.003, 0.005, 10_000)[0] == 'TP'     # short: -0.4 % is a +0.4 % gain
    assert ap.reaches_after(t, p, 100.0, 1, 0.009, 3_000, 5_000) is True
    assert ap.reaches_after(t, p, 100.0, 1, 0.02, 3_000, 5_000) is False


def test_bracket_net_uses_the_mainnet_fee_model():
    assert ap.bracket_net_bps('TP', 0.0046, True) == pytest.approx(46 - 4)
    assert ap.bracket_net_bps('SL', -0.0022, True) == pytest.approx(-22 - 2 - 6)


def test_tape_window_with_a_hole_is_unknown(tmp_path):
    d = tmp_path / 'data' / 'aggtrades_20261110' / 'DOGEUSDT'
    d.mkdir(parents=True)
    day0 = dt.date(2026, 11, 1)
    t0 = int(dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    t = t0 + np.arange(0, 86_400_000, 60_000, dtype=np.int64)
    np.savez(d / f'{day0.isoformat()}.npz', t=t, p=np.full(len(t), 0.1), q=np.ones(len(t), np.float32),
             m=np.zeros(len(t), bool))
    tape = ap.Tape(tmp_path)
    w = tape.window('DOGEUSDT', t0 + 3_600_000, t0 + 7_200_000)
    assert w is not None and w[0][0] <= t0 + 3_600_000 and w[0][-1] <= t0 + 7_200_000
    assert tape.window('DOGEUSDT', t0 + 80_000_000, t0 + 90_000_000) is None   # runs into the missing next day
    assert tape.window('XRPUSDT', t0, t0 + 1000) is None


def test_refusal_reasons_are_normalised():
    assert ap.norm_reason('MICRO ALIGN SELL: OFI -0.39 not <= -0.65') == 'MICRO ALIGN'
    assert ap.norm_reason('') == 'UNSPECIFIED'
