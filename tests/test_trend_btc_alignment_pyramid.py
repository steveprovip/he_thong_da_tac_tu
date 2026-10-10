"""10-09 (ff): BTC macro shield for alt entries, break-even / pyramid backtest, 1H+4H+1D conviction sizing, REST
resilience (retry, cache, spot proxy).  MockVenue / fake HTTP only; temp files; no network, no real ledger."""
import io
import json
import urllib.error

import pytest

from core import engine as eng
from core import trend_executor as tx
from core import trend_lane as tl
from tests.test_trend_regime_breakthrough import H4, data_for, setup, trend_bars


# ============================================================================ BTC shield
def info_at(vr, cs):
    return {'vol_ratio': [vr], 'cvd_share': [cs]}


def test_align_entry_blocks_alt_against_btc_with_volume_exception():
    al = tl.BtcAlign(mode='sign')
    assert tl.align_entry((1.0, 'MIXED'), 1.0, info_at(1.0, 0.0), 0, al)[0]               # with BTC
    ok, why = tl.align_entry((1.0, 'MIXED'), -1.0, info_at(2.0, -0.2), 0, al)
    assert not ok and 'against BTC BUY' in why
    assert tl.align_entry((1.0, 'TREND'), -1.0, info_at(3.5, -0.15), 0, al)[0]             # own flow: 3.5x, -15 %
    assert not tl.align_entry((1.0, 'TREND'), -1.0, info_at(3.5, -0.05), 0, al)[0]         # CVD share too small
    assert tl.align_entry((1.0, 'MIXED'), -1.0, info_at(1.0, 0.0), 0, tl.BtcAlign(mode='trend'))[0]
    assert not tl.align_entry((-1.0, 'TREND'), 1.0, info_at(1.0, 0.0), 0, tl.BtcAlign(mode='trend'))[0]
    assert 'UNKNOWN' in tl.align_entry(None, 1.0, info_at(1.0, 0.0), 0, al)[1]
    assert tl.align_entry((0.0, None), -1.0, info_at(1.0, 0.0), 0, al)[0]                  # BTC flat: no shield


def test_backtest_align_blocks_alt_short_while_btc_trends_up_and_eject_flattens():
    cfg = tl.TrendConfig(bar='4h')
    btc = trend_bars(n=600, drift=0.004, seed=1)
    alt = trend_bars(n=600, drift=-0.004, seed=3)
    alt['t'] = btc['t'][:]
    bars = {'BTCUSDT': btc, 'DOGEUSDT': alt}
    free = tl.backtest_brackets(bars, cfg, tl.Brackets(sl_atr=8.0))
    assert any(t['symbol'] == 'DOGEUSDT' and t['side'] == 'SHORT' for t in free['trades'])
    shield = tl.backtest_brackets(bars, cfg, tl.Brackets(sl_atr=8.0), align=tl.BtcAlign(mode='sign'))
    assert not [t for t in shield['trades'] if t['symbol'] == 'DOGEUSDT' and t['side'] == 'SHORT']
    assert [t for t in shield['trades'] if t['symbol'] == 'BTCUSDT']                      # the leader is not gated


# ============================================================================ pyramid / break-even (backtest)
def up_then_back(n=600):
    """Long entry at bar e; e+1 closes +2.5 % (break-even armed, add at that close); e+2 dips to -1 % intrabar."""
    b = trend_bars(n=n, drift=0.004)
    e = tl.backtest_brackets({'X': b}, tl.TrendConfig(bar='4h'), tl.Brackets(sl_atr=8.0))['trades'][0]['entry_t']
    i = b['t'].index(e)
    b = {k: v[:i + 3] for k, v in b.items()}
    px = b['c'][i]
    for k, (o, h, l, c) in zip((i + 1, i + 2), ((px, px * 1.026, px * 1.0, px * 1.025),
                                                  (px * 1.025, px * 1.026, px * 0.99, px * 1.01))):
        b['o'][k], b['h'][k], b['l'][k], b['c'][k] = o, h, l, c
    return b


def test_backtest_break_even_stop_exits_in_profit_and_add_is_tracked():
    cfg = tl.TrendConfig(bar='4h')
    b = up_then_back()
    be = tl.backtest_brackets({'X': b}, cfg, tl.Brackets(sl_atr=8.0), pyramid=tl.Pyramid(add_frac=0.0))
    stops = [t for t in be['trades'] if t.get('why') == 'BE']
    assert stops and all(t['pnl'] > -1e-9 for t in stops)                                  # entry +0.3 % - fees
    p2 = tl.backtest_brackets({'X': b}, cfg, tl.Brackets(sl_atr=8.0), pyramid=tl.Pyramid(add_frac=0.5))
    assert any(t.get('added') for t in p2['trades'])
    base = tl.backtest_brackets({'X': b}, cfg, tl.Brackets(sl_atr=8.0))
    assert sum(base['turnover'].values()) < sum(p2['turnover'].values())                    # the add trades


# ============================================================================ conviction
def test_conviction_series_needs_all_three_frames_closed():
    b4 = trend_bars(n=600)
    tw4 = [1.0] * 600
    t1 = [b4['t'][0] + k * 3600 for k in range(600 * 4)]
    c1 = [100.0 * 1.001 ** k for k in range(len(t1))]
    td = [b4['t'][0] + k * 86400 for k in range(100)]
    yes = tl.conviction_series(b4, t1, c1, td, [0.5] * 100, tw4)
    assert yes[-1] and not yes[0]                                                         # warm-up -> False
    assert not tl.conviction_series(b4, t1, c1, td, [-0.5] * 100, tw4)[-1]                 # 1D disagrees
    assert not tl.conviction_series(b4, t1, [100.0 * 0.999 ** k for k in range(len(t1))], td, [0.5] * 100, tw4)[-1]


def test_backtest_conviction_sizing_uses_the_bigger_ceiling_only_when_agreed():
    cfg = tl.TrendConfig(bar='4h')
    b = trend_bars(n=600)
    small, big = tl.Sizing(0.10, 0.01, 8.0), tl.Sizing(0.30, 0.02, 8.0)
    on = tl.backtest_brackets({'X': b}, cfg, tl.Brackets(sl_atr=8.0), sizing=small, conv={'X': [True] * 600}, conv_sizing=big)
    off = tl.backtest_brackets({'X': b}, cfg, tl.Brackets(sl_atr=8.0), sizing=small, conv={'X': [False] * 600}, conv_sizing=big)
    assert on['trades'][0]['conv'] and on['trades'][0]['w'] > off['trades'][0]['w']
    assert not off['trades'][0]['conv']


# ============================================================================ executor
def btc_up_sol_down():
    btc = trend_bars(n=520, drift=0.004, seed=1)
    sol = trend_bars(n=520, drift=-0.004, seed=3, buy_share=0.4)            # SELL, TREND, CVD with the short
    sol['t'] = btc['t'][:]
    return {'bars': {'BTCUSDT': btc, 'SOLUSDT': sol}, 'funding': {}}


async def test_executor_btc_shield_enforced_holds_back_alt_short(tmp_path):
    x, e, venue, events, clk = setup(tmp_path, trend_btc_align='sign')
    d = btc_up_sol_down()
    clk.t = d['bars']['SOLUSDT']['t'][-1] + H4 + 60
    t = x.targets(d, clk.t)
    assert t['SOLUSDT']['side'] < 0 and t['SOLUSDT']['gate_ok'] and t['SOLUSDT']['align_ok'] is False
    assert t['BTCUSDT']['side'] > 0 and t['SOLUSDT']['btc'][0] > 0
    await x.step(d)
    assert 'SOLUSDT' not in x.state and any('BTC shield' in m and 'held back' in m for m in events)


async def test_executor_btc_shield_shadow_logs_and_still_opens(tmp_path):
    x, e, venue, events, clk = setup(tmp_path, trend_btc_align='shadow')
    d = btc_up_sol_down()
    clk.t = d['bars']['SOLUSDT']['t'][-1] + H4 + 60
    await x.step(d)
    assert x.state['SOLUSDT']['side'] == 'SELL' and any('(shadow) would hold back SELL' in m for m in events)


def test_exit_check_btc_eject_only_for_alts():
    p = eng.replace(eng.PROFILES['demo'], symbol='SOLUSDT', trend_btc_eject=True, trend_chop_exit=False,
                    trend_lock_pct=None)
    st = {'side': 'SELL', 'entry': 100.0, 'best': 100.0}
    assert tx.TrendExecutor.exit_check(p, st, {'close': 101.0, 'atr': 1.0, 'regime': 'TREND', 'btc': (1.0, 'TREND')}) == 'BTC-EJECT'
    assert tx.TrendExecutor.exit_check(p, st, {'close': 101.0, 'atr': 1.0, 'regime': 'TREND', 'btc': (1.0, 'MIXED')}) is None
    pb = eng.replace(p, symbol='BTCUSDT')
    assert tx.TrendExecutor.exit_check(pb, st, {'close': 101.0, 'atr': 1.0, 'btc': (1.0, 'TREND')}) is None


async def test_break_even_moves_the_exchange_stop_once(tmp_path):
    x, e, venue, events, clk = setup(tmp_path, trend_be_pct=0.3, trend_lock_pct=None)
    up = trend_bars()
    clk.t = up['t'][-1] + H4 + 60
    await x.step(data_for(up))
    st = x.state['SOLUSDT']
    old = st['sl_cid']
    await x.break_even(e, st, {'close': st['entry'] * 1.01})                  # +1 %: not armed
    assert st['sl_cid'] == old and not st.get('be')
    await x.break_even(e, st, {'close': st['entry'] * 1.025})
    assert st['be'] and st['stop'] == pytest.approx(st['entry'] * 1.003)
    assert venue.orders[old]['status'] == 'CANCELED' and venue.orders[st['sl_cid']]['status'] == 'NEW'
    assert venue.orders[st['sl_cid']]['price'] == pytest.approx(st['entry'] * 1.003, rel=1e-3)
    cid = st['sl_cid']
    await x.break_even(e, st, {'close': st['entry'] * 1.05})                  # once only
    assert st['sl_cid'] == cid and any('BREAK-EVEN' in m for m in events)


async def test_break_even_stop_rejected_restores_old_level(tmp_path):
    x, e, venue, events, clk = setup(tmp_path, trend_be_pct=0.3, trend_lock_pct=None)
    up = trend_bars()
    clk.t = up['t'][-1] + H4 + 60
    await x.step(data_for(up))
    st = x.state['SOLUSDT']
    old_stop = st['stop']
    real, calls = venue.stop, []

    async def flaky(sym, side, px, cid):
        calls.append(px)
        if len(calls) == 1:
            raise RuntimeError('-2021 would immediately trigger')
        await real(sym, side, px, cid)
    venue.stop = flaky
    await x.break_even(e, st, {'close': st['entry'] * 1.03})
    assert not st.get('be') and st['stop'] == old_stop and venue.orders[st['sl_cid']]['status'] == 'NEW'
    assert any('rejected' in m for m in events)


def test_executor_conviction_size_and_risk_cap(tmp_path):
    x, e, *_ = setup(tmp_path, trend_conv_alloc_pct=0.30, trend_conv_risk_pct=0.02, trend_notional_max=1500.0)
    assert x.size(e, 0.01, 100.0, atr=0.5, conv=True) * 100 == pytest.approx(1500, abs=1)     # 30 % ceiling
    assert x.size(e, 0.01, 100.0, atr=2.5, conv=True) * 100 == pytest.approx(500, abs=1)      # 2 % / 20 % stop
    assert x.size(e, 0.01, 100.0, atr=0.5, conv=False) * 100 == pytest.approx(500, abs=1)     # base 10 %


def test_executor_conviction_from_1h_and_1d_bars(tmp_path):
    x, *_ = setup(tmp_path)
    b4 = trend_bars(n=520)
    now = b4['t'][-1] + H4 + 60
    t1 = [now - (300 - k) * 3600 for k in range(300)]
    h1 = {'t': t1, 'c': [100.0 * 1.001 ** k for k in range(300)]}
    td = [now - (120 - k) * 86400 for k in range(120)]
    cd = [100.0 * 1.01 ** k for k in range(120)]
    d1 = {'t': td, 'o': cd, 'h': [c * 1.01 for c in cd], 'l': [c * 0.99 for c in cd], 'c': cd}
    tw4 = {'SOLUSDT': [0.1]}
    data = {'tf': {'1h': {'SOLUSDT': h1}, '1d': {'SOLUSDT': d1}}, 'funding': {}}
    assert x.conviction(data, now, tw4) == {'SOLUSDT': True}
    assert x.conviction({'funding': {}}, now, tw4) == {'SOLUSDT': False}                     # UNKNOWN -> base size
    assert x.conviction(data, now, {'SOLUSDT': [-0.1]}) == {'SOLUSDT': False}


# ============================================================================ REST resilience
class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def kline_row(t_ms, close_ms):
    return [t_ms, '1', '2', '0.5', '1.5', '10', close_ms, '15', 5, '6', '9', '0']


def test_rest_json_retries_with_backoff_then_next_host_on_451():
    naps, seen = [], []

    def opener(url, timeout):
        seen.append((url, timeout))
        if 'a.example' in url:
            raise urllib.error.HTTPError(url, 451, 'geo', {}, None)               # definitive: no retry
        if len([u for u, _ in seen if 'b.example' in u]) < 3:
            raise urllib.error.URLError('_ssl.c:990: The handshake operation timed out')
        return FakeResp(b'[1]')
    out = eng.rest_json(['https://a.example/x', 'https://b.example/x'], timeout=12.0, sleep=naps.append,
                        opener=opener)
    assert out == [1] and naps == [0.5, 1.0] and [u for u, _ in seen].count('https://a.example/x') == 1
    assert all(t == 12.0 for _, t in seen)


def test_rest_json_raises_after_three_failures():
    naps = []

    def opener(url, timeout):
        raise TimeoutError('handshake')
    with pytest.raises(TimeoutError):
        eng.rest_json(['https://b.example/x'], sleep=naps.append, opener=opener)
    assert naps == [0.5, 1.0]


def test_fetch_klines_falls_back_to_cache_then_spot_proxy(monkeypatch):
    monkeypatch.setattr(eng, 'KLINE_CACHE', {})
    monkeypatch.setattr(eng, 'KLINE_SOURCE', {})
    rows = [kline_row(1000, 2000)]
    ok = eng.fetch_klines('SOLUSDT', '4h', 2, get=lambda urls, timeout: rows)
    assert ok[0][6:] == (15.0, 9.0, 10.0) and eng.KLINE_SOURCE[('SOLUSDT', '4h')] == 'fapi'

    def down(urls, timeout):
        if 'fapi' in urls[0]:
            raise OSError('_ssl.c:990: The handshake operation timed out')
        return rows
    assert eng.fetch_klines('SOLUSDT', '4h', 2, get=down) == ok                       # cache: same closed bars
    assert eng.KLINE_SOURCE[('SOLUSDT', '4h')] == 'cache'
    spot = eng.fetch_klines('DOGEUSDT', '4h', 2, get=down)                             # cold start: spot proxy
    assert len(spot[0]) == 6 and eng.KLINE_SOURCE[('DOGEUSDT', '4h')] == 'spot-proxy'
    d = eng.trend_fetch(['DOGEUSDT'], '4h', klines=lambda s, b, n: spot, funding=lambda s: [])
    assert 'v' not in d['bars']['DOGEUSDT']                                            # volume UNKNOWN -> no entry


def test_fetch_klines_timeout_default_is_twelve_seconds():
    import inspect
    assert inspect.signature(eng.fetch_klines).parameters['timeout'].default == 12.0
    assert inspect.signature(eng.fetch_funding).parameters['timeout'].default == 12.0


@pytest.mark.production_profile
def test_demo_profile_ff_adopted_parts_only_live_untouched():
    d, l = eng.PROFILES['demo'], eng.PROFILES['live']
    assert d.trend_be_pct == 0.3 and d.trend_pyr_arm_pct == 2.0
    assert d.trend_conv_alloc_pct == 0.30 and d.trend_conv_risk_pct == 0.02 and d.trend_notional_max == 1500.0
    assert d.trend_btc_align == 'shadow' and not d.trend_btc_eject
    assert l.trend_lane == 'off' and l.trend_conv_alloc_pct is None and l.trend_be_pct is None
