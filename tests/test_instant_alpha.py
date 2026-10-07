"""One-click instant alpha: System-1 gates, carry ranking, liquidation shock, adapters."""
import json
import math
import time

import pytest

from core.early_stop import COST_ALL_IN_BPS, EarlyStopGate
from core.instant_alpha import (LIQ_MIN_GROSS_BPS, LiquidationWindow, Shock, ShockShadow,
                                binance_snapshot, bybit_snapshot, parse_force_order,
                                rank_opportunities, settlements)
from core.jev_gatekeeper import (PERMIT_SCORE, JevGatekeeper, depth_imbalance, flow_imbalance,
                                 is_order_adverse_selected, regime_profitability_score,
                                 score_from_ratio)
from core.models import Book
from research.alternative_alphas import cex_dex, etf_basis, prediction_markets as pm
from phan_tich.slow_brain_contract import validate_verdict

NOW = 1_790_000_000.0


# ------------------------------------------------------------- System 1
def test_score_is_calibrated_to_gross_over_three_cost():
    assert score_from_ratio(3.0) == pytest.approx(PERMIT_SCORE)
    assert score_from_ratio(2.99) < PERMIT_SCORE < score_from_ratio(3.01)
    assert score_from_ratio(0) == 0 and score_from_ratio(float('nan')) == 0
    assert 0 <= score_from_ratio(1e9) <= 10


def test_regime_score_never_invents_an_edge():
    assert regime_profitability_score(4.0, 10.0) == 0.0                       # no funding, no evidence
    assert regime_profitability_score(4.0, 10.0, scalp_gross_bps=30.0) >= PERMIT_SCORE
    assert regime_profitability_score(1.0, 10.0, scalp_gross_bps=30.0) == 0.0  # outside spread band
    carry = regime_profitability_score(0.5, 0.0, funding_bps_8h=3.0, funding_periods=21, carry_cost_bps=21.0)
    assert carry == pytest.approx(score_from_ratio(3.0))


def test_adverse_selection_boolean_fail_closed():
    heavy_ask = {'bids': [(100, 1)] * 5, 'asks': [(101, 9)] * 5}
    assert depth_imbalance(heavy_ask) == pytest.approx(-.8)
    assert is_order_adverse_selected(heavy_ask, [1, -1], 'BUY')
    assert not is_order_adverse_selected(heavy_ask, [1, -1], 'SELL')
    balanced = Book('X', ((100., 5.),), ((100.01, 5.),), 0.)
    assert not is_order_adverse_selected(balanced, {'buy': 5, 'sell': 5}, 'BUY')
    assert is_order_adverse_selected(balanced, {'buy': 1, 'sell': 9}, 'BUY')        # sellers hitting bids
    assert flow_imbalance([3, -1]) == pytest.approx(.5)
    assert is_order_adverse_selected({'bids': [], 'asks': []}, [], 'BUY')
    assert is_order_adverse_selected(balanced, [1], 'HOLD')


def test_gatekeeper_latency_under_ten_ms_and_backend_can_only_block():
    gk = JevGatekeeper()
    book = Book('X', ((100., 5.), (99.99, 3.)), ((100.01, 4.), (100.02, 2.)), 0.)
    for _ in range(2000):
        gk.adverse(book, [1, -2, 3], 'BUY')
        gk.score(3.0, 10.0, funding_bps_8h=1.0)
    lat = gk.latency()
    assert lat['adverse']['p99_us'] < 10_000 and lat['score']['p99_us'] < 10_000
    raising = JevGatekeeper(backend=lambda k, p: 10.0 if k == 'score' else False)
    assert raising.score(3.0, 10.0) == 0.0                                   # cannot lift a zero
    blocking = JevGatekeeper(backend=lambda k, p: True if k == 'adverse' else 0.0)
    assert blocking.adverse(book, [1], 'BUY') is True
    assert blocking.score(4.0, 10.0, scalp_gross_bps=40.0) == 0.0


def test_gatekeeper_ignores_slow_or_broken_backend():
    def slow(kind, payload):
        time.sleep(.02)
        return True
    gk = JevGatekeeper(backend=slow)
    book = Book('X', ((100., 5.),), ((100.01, 5.),), 0.)
    assert gk.adverse(book, [1], 'BUY') is False and gk.ledger['adverse'].fallbacks == 1
    broken = JevGatekeeper(backend=lambda k, p: 1 / 0)
    assert broken.score(4.0, 10.0, scalp_gross_bps=40.0) >= PERMIT_SCORE
    assert broken.ledger['score'].fallbacks == 1


# ------------------------------------------------------------- carry ranking
def row(symbol, rate8, hist=None, spot=True, oi=5e7, spread=.01, interval=8):
    return {'symbol': symbol, 'rate_bps': rate8 * interval / 8, 'interval_h': interval,
            'rate_bps_8h': rate8, 'next_funding': NOW + 3600, 'mark': 100.0, 'index': 100.0,
            'quote_volume': 1e8, 'perp_bid': 100 - spread, 'perp_ask': 100 + spread,
            'spot_bid': (100 - spread) if spot else float('nan'), 'spot_ask': (100 + spread) if spot else float('nan'),
            'history_bps_8h': [rate8] * 21 if hist is None else hist, 'oi_usdt': oi}


def test_settlement_count():
    assert settlements(NOW, NOW + 3600, 8, 7) == 21
    assert settlements(NOW, NOW - 1, 8, 7) == 0


def test_rich_positive_funding_is_executable_and_thin_is_not():
    ranked = rank_opportunities({'RICHUSDT': row('RICHUSDT', 5.0), 'THINUSDT': row('THINUSDT', 1.0)}, now=NOW)
    rich = next(o for o in ranked if o.symbol == 'RICHUSDT')
    thin = next(o for o in ranked if o.symbol == 'THINUSDT')
    assert rich.executable and rich.ratio > 3 and rich.score >= PERMIT_SCORE
    assert rich.gross_bps == pytest.approx(5.0 * 21)
    assert not thin.executable and 'SCORE' in thin.reason
    assert ranked[0].symbol == 'RICHUSDT'


def test_projection_uses_min_of_current_and_history_and_blocks_unsafe_rows():
    spike = rank_opportunities({'S': row('S', 20.0, hist=[1.0] * 21)}, now=NOW)[0]
    assert spike.gross_bps == pytest.approx(21.0) and not spike.executable
    neg = rank_opportunities({'N': row('N', -30.0)}, now=NOW)[0]
    assert neg.channel == 'NEEDS_SPOT_SHORT' and not neg.executable
    nospot = rank_opportunities({'P': row('P', 9.0, spot=False)}, now=NOW)[0]
    assert not nospot.executable and nospot.reason == 'NO_SPOT_MARKET'
    lowoi = rank_opportunities({'L': row('L', 9.0, oi=1e6)}, now=NOW)[0]
    assert not lowoi.executable and 'OI' in lowoi.reason
    short_hist = rank_opportunities({'H': row('H', 9.0, hist=[9.0] * 5)}, now=NOW)[0]
    assert not short_hist.executable and 'HISTORY' in short_hist.reason


def test_cross_venue_is_research_only():
    bybit = {'RICHUSDT': {'symbol': 'RICHUSDT', 'rate_bps_8h': 15.0, 'interval_h': 8, 'bid': 99.99, 'ask': 100.01,
                          'turnover': 1e8}}
    ranked = rank_opportunities({'RICHUSDT': row('RICHUSDT', 5.0)}, bybit, now=NOW)
    x = next(o for o in ranked if o.channel == 'XVENUE_FUNDING')
    assert not x.executable and 'RESEARCH_ONLY' in x.reason and x.rate_bps_8h == pytest.approx(10.0)


def test_snapshot_parsers_with_injected_fetch():
    def fetch(url):
        if 'premiumIndex' in url:
            return [{'symbol': 'AUSDT', 'lastFundingRate': '0.0005', 'nextFundingTime': (NOW + 3600) * 1000,
                     'markPrice': '10', 'indexPrice': '10'},
                    {'symbol': 'BUSDT', 'lastFundingRate': '0.0001', 'nextFundingTime': 0, 'markPrice': '1',
                     'indexPrice': '1'}]
        if 'fundingInfo' in url:
            return [{'symbol': 'AUSDT', 'fundingIntervalHours': 4}]
        if 'ticker/24hr' in url:
            return [{'symbol': 'AUSDT', 'quoteVolume': '9e7'}, {'symbol': 'BUSDT', 'quoteVolume': '1'}]
        if 'bookTicker' in url:
            return [{'symbol': 'AUSDT', 'bidPrice': '9.99', 'askPrice': '10.01'}]
        if 'fundingRate?' in url:
            return [{'fundingRate': '0.0005'}] * 21
        if 'openInterest' in url:
            return {'openInterest': '5000000'}
        if 'tickers' in url:
            return {'result': {'list': [{'symbol': 'AUSDT', 'fundingRate': '0.001', 'bid1Price': '9.99',
                                         'ask1Price': '10.01', 'turnover24h': '1e8'}]}}
        if 'instruments-info' in url:
            return {'result': {'list': [{'symbol': 'AUSDT', 'fundingInterval': 240}]}}
        raise AssertionError(url)
    b = binance_snapshot(fetch)
    assert list(b) == ['AUSDT'] and b['AUSDT']['rate_bps_8h'] == pytest.approx(10.0)
    assert b['AUSDT']['oi_usdt'] == pytest.approx(5e7) and len(b['AUSDT']['history_bps_8h']) == 21
    y = bybit_snapshot(fetch)
    assert y['AUSDT']['rate_bps_8h'] == pytest.approx(20.0)


# ------------------------------------------------------------- liquidation shock
def test_liquidation_window_triggers_once_per_burst():
    w = LiquidationWindow(threshold_usdt=500_000, window_s=60)
    assert w.add('X', 'SELL', 100., 3000., 0.) is None
    shock = w.add('X', 'SELL', 100., 2500., 30.)
    assert shock and shock.notional_usdt == pytest.approx(550_000) and shock.fade_side == 'BUY'
    assert w.add('X', 'SELL', 100., 6000., 40.) is None              # cooldown
    assert w.add('X', 'SELL', 100., 1000., 200.) is None             # old events expired
    assert w.add('X', 'BUY', -1, 1, 1) is None


def test_force_order_parser():
    msg = {'stream': '!forceOrder@arr', 'data': {'e': 'forceOrder', 'o': {
        's': 'BTCUSDT', 'S': 'SELL', 'p': '60000', 'ap': '59990', 'q': '2', 'z': '2', 'T': 1700000000000}}}
    assert parse_force_order(msg) == ('BTCUSDT', 'SELL', 59990.0, 2.0, 1700000000.0)
    assert parse_force_order({}) is None


def test_shock_shadow_requires_confirmed_25bps_before_live():
    shadow = ShockShadow(horizon_s=300, gate=EarlyStopGate(min_samples=10))
    for i in range(10):
        shadow.open_shock(Shock('X', 'SELL', 6e5, i * 1000.0, 100.0))
        shadow.resolve({'X': 100.40}, i * 1000.0 + 301)                # +40 bps fade
    assert shadow.summary()['mean_gross_bps'] == pytest.approx(40.0)
    assert shadow.live_permitted()
    weak = ShockShadow(horizon_s=300, gate=EarlyStopGate(min_samples=10))
    for i in range(10):
        weak.open_shock(Shock('X', 'SELL', 6e5, i * 1000.0, 100.0))
        weak.resolve({'X': 100.12 + (i % 2) * .001}, i * 1000.0 + 301)   # ~12 bps: covers 8.5, not 25
    assert not weak.live_permitted()
    assert LIQ_MIN_GROSS_BPS == 25.0 and COST_ALL_IN_BPS == 8.5


# ------------------------------------------------------------- one-click runner
def test_run_alpha_instant_offline_shadow_and_capital_gate(tmp_path):
    import run_alpha_instant as rai
    snap = tmp_path / 'snap.json'
    snap.write_text(json.dumps({'now': NOW, 'binance': {'RICHUSDT': row('RICHUSDT', 5.0)}, 'bybit': {}}))
    code = rai.main(['--snapshot-file', str(snap), '--out', str(tmp_path / 'o'), '--no-liquidation'])
    rep = json.loads(next((tmp_path / 'o').glob('*.json')).read_text())
    assert code == 3 and rep['status'] == 'CAPITAL_GATE' and rep['selected']['symbol'] == 'RICHUSDT'
    code = rai.main(['--snapshot-file', str(snap), '--out', str(tmp_path / 'p'), '--no-liquidation',
                     '--equity', '50', '--notional', '20'])
    rep = json.loads(next((tmp_path / 'p').glob('*.json')).read_text())
    assert code == 0 and rep['status'] == 'SHADOW_PLAN'


def test_run_alpha_instant_exits_fast_when_nothing_pays(tmp_path):
    import run_alpha_instant as rai
    snap = tmp_path / 'snap.json'
    snap.write_text(json.dumps({'now': NOW, 'binance': {'THINUSDT': row('THINUSDT', 1.0)}}))
    t0 = time.monotonic()
    assert rai.main(['--snapshot-file', str(snap), '--out', str(tmp_path / 'o')]) == 2
    assert time.monotonic() - t0 < 5


def test_run_alpha_instant_never_launches_mainnet(tmp_path, monkeypatch):
    import run_alpha_instant as rai
    monkeypatch.setattr(rai.subprocess, 'call', lambda *a, **k: pytest.fail('must not launch'))
    snap = tmp_path / 'snap.json'
    snap.write_text(json.dumps({'now': NOW, 'binance': {'RICHUSDT': row('RICHUSDT', 5.0)}}))
    assert rai.main(['--snapshot-file', str(snap), '--out', str(tmp_path / 'o'), '--mode', 'mainnet',
                     '--equity', '50', '--notional', '20', '--no-liquidation']) == 0
    rep = json.loads(next((tmp_path / 'o').glob('*.json')).read_text())
    assert rep['status'] == 'MAINNET_REQUIRES_HUMAN' and '--arm' in rep['command']


def test_run_alpha_instant_testnet_handoff(tmp_path, monkeypatch):
    import run_alpha_instant as rai
    calls = []
    monkeypatch.setattr(rai.subprocess, 'call', lambda cmd, cwd=None: calls.append(cmd) or 0)
    snap = tmp_path / 'snap.json'
    snap.write_text(json.dumps({'now': NOW, 'binance': {'RICHUSDT': row('RICHUSDT', 5.0)}}))
    assert rai.main(['--snapshot-file', str(snap), '--out', str(tmp_path / 'o'), '--mode', 'testnet',
                     '--equity', '50', '--notional', '20', '--no-liquidation']) == 0
    assert calls and calls[0][2:5] == ['core.carry_runtime', '--carry', '--mode'] and 'RICHUSDT' in calls[0]
    assert '--seconds' not in calls[0]          # a carry is held, never force-closed at minute 15


def test_run_alpha_instant_caps_runtime_at_fifteen_minutes():
    import run_alpha_instant as rai
    with pytest.raises(SystemExit):
        rai.arguments(['--max-minutes', '16'])
    with pytest.raises(SystemExit):
        rai.arguments(['--horizon-days', '1'])


# ------------------------------------------------------------- adapters
def test_polymarket_and_kalshi_fees():
    assert pm.polymarket_fee(100, .5, 'crypto') == pytest.approx(100 * .07 * .25)
    assert pm.polymarket_fee(100, .5, 'geopolitics') == 0.0
    assert pm.polymarket_fee(100, .5, maker=True) == 0.0
    assert pm.kalshi_fee(100, .5) == pytest.approx(1.75)
    assert pm.kalshi_fee(1, .5) == pytest.approx(.02)
    assert pm.kalshi_fee(100, .5, maker=True) == pytest.approx(.44)
    with pytest.raises(ValueError):
        pm.polymarket_fee(1, 1.0)


def test_basket_arbitrage_and_mispricing():
    fee = lambda c, p: pm.polymarket_fee(c, p, 'politics')
    arb = pm.basket_arbitrage([.30, .30, .30], fee)
    assert arb['gross'] == pytest.approx(10.0) and arb['executable']
    fair = pm.basket_arbitrage([.34, .33, .34], fee)
    assert fair['net'] < 0 and not fair['executable']
    mis = pm.model_mispricing(.60, .50, fee)
    assert mis['ev'] > 0 and mis['executable']
    assert not pm.model_mispricing(.51, .50, fee)['executable']


def test_polymarket_event_scan_with_injected_fetch():
    events = [{'title': 'E1', 'negRisk': True, 'markets': [
        {'active': True, 'closed': False, 'bestAsk': .3}, {'active': True, 'closed': False, 'bestAsk': .3},
        {'active': True, 'closed': False, 'bestAsk': .3}]},
        {'title': 'E2', 'negRisk': False, 'markets': [{'active': True, 'closed': False, 'bestAsk': .4}] * 2}]
    out = pm.scan_polymarket_events(lambda url: events)
    assert len(out) == 1 and out[0]['event'] == 'E1' and out[0]['executable']


def test_etf_basis_is_capital_gated_and_verdict_blocks_only():
    f = etf_basis.fair_future(6000, .045, .013, .25)
    assert f == pytest.approx(6000 * math.exp(.032 * .25))
    small = etf_basis.basis_trade(6000, f + 3, .045, .013, .25, capital_usd=3.85)
    assert not small['executable']
    assert 'CAPITAL' in small['reason']
    rich = etf_basis.basis_trade(6000, f * 1.01, .045, .013, .25, capital_usd=1e6)
    assert rich['executable'] and rich['direction'] == 'CASH_AND_CARRY'
    v = etf_basis.verdict_from_agent_signals({'a': {'signal': 'bullish', 'confidence': 80},
                                              'b': {'signal': 'bearish', 'confidence': 70}}, 'SPY', 900, 950)
    assert v['regime'] == 'CHOP' and validate_verdict(v, 1000)[1] == 'SLOW_BRAIN_BLOCK'


def test_cex_dex_math_and_atomicity():
    sp = int(math.sqrt(2000) * cex_dex.Q96)
    out, new = cex_dex.v3_amount_out(1.0, sp, 10 ** 6, .0005, True)
    assert out == pytest.approx(2000 * .9995, rel=1e-3) and new < sp
    with pytest.raises(ValueError):
        cex_dex.v3_amount_out(1e6, sp, 10, .0005, True)
    tiny = cex_dex.gap_trade(2010, 2000, 100)
    assert not tiny['executable'] and tiny['risk'].startswith('inventory')
    big = cex_dex.gap_trade(2010, 2000, 1e6, atomic=True, builder_tip_share=.5)
    assert big['atomic'] and big['net_bps'] > 0
    assert cex_dex.breakeven_notional(50) > 0 and cex_dex.breakeven_notional(10) == math.inf


def test_typesafe_backend_adapter_with_fake_sdk(monkeypatch):
    import sys
    import types
    from core import jev_typesafe_backend as jb
    fake = types.ModuleType('typesafe_sdk')
    fake.Noul = lambda **kw: ('noul', kw)
    fake.Score = lambda **kw: ('score', kw)
    monkeypatch.setitem(sys.modules, 'typesafe_sdk', fake)

    class Client:
        def system_one(self, state, questions, timeout):
            name = next(iter(questions))
            ans = types.SimpleNamespace(noul=.9) if name == 'adverse' else types.SimpleNamespace(score=4.0)
            return types.SimpleNamespace(answers={name: ans})
    backend = jb.make_backend(Client())
    assert backend('adverse', {'side': 'BUY', 'book': object()}) is True
    assert backend('score', {'spread_bps': 3.0}) == 4.0
    gk = JevGatekeeper(backend=backend, budget_ns=10**9)
    assert gk.score(4.0, 10.0, scalp_gross_bps=40.0) == 4.0            # veto-only: lowered
    monkeypatch.delenv('TYPESAFE_API_KEY', raising=False)
    assert jb.load_client() is None                                   # no key -> no network backend
