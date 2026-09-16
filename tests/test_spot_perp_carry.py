"""Deterministic venue ledger tests: actual quantities, commissions and faults."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from core.models import PairIntent, dec
from core.consensus_engine import TokenAuthority, ConsensusEngine, Vote
from live.binance_futures_client import BinanceError
from live.binance_spot_client import received_coins, gross_buy_quantity
from live.execution_gateway import PairExecutionGateway, Journal
from core.funding_carry import CarrySnapshot, plan_spot_perp, carry_votes, isolated_margin_ratio
from core.carry_runtime import CarryRuntime, arguments, venue_settings, futures_account, commission_bps
from phan_tich.carry_lab import pair_cashflow, wallet_reconciliation, weekly_report


def intent(**overrides):
    return replace(PairIntent('BTCUSDT', 'BTCUSDT', 'BTC', '1', '100', '100', '140',
                              '.001', '.001', '5', 7 * 86400, 'testnet'), **overrides)


def authorize(authority, pair):
    policy = ConsensusEngine(trend_floor=.768)
    decision = policy.evaluate([Vote(a, 'SELL', 1, 'verified fixture') for a in policy.weights])
    return authority.issue_pair(pair, decision)


class SpotVenue:
    """In-memory matching ledger with controllable rejection/transport faults."""
    def __init__(self):
        self.settings = SimpleNamespace(mode='testnet', armed=True)
        self.orders, self.calls = {}, []
        self.failures = 0
        self.lose_response = False
        self.delay = 0
        self.fee_asset = 'BNB'

    async def create_order(self, symbol, side, quantity, client_id, price=None):
        self.calls.append((side, quantity, price))
        if self.failures:
            self.failures -= 1
            raise BinanceError(-1013)
        q = dec(quantity)
        row = {'orderId': len(self.orders) + 1, 'status': 'FILLED', 'executedQty': str(q),
               'fills': [{'qty': str(q), 'price': '100', 'commission': str(q * dec('.001')),
                          'commissionAsset': self.fee_asset}]}
        self.orders[client_id] = row
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.lose_response:
            self.lose_response = False
            raise BinanceError('TRANSPORT', uncertain=True)
        return row

    async def order(self, symbol, cid):
        return self.orders[cid]

    async def fill_details(self, symbol, row):
        return row

    async def book_ticker(self, symbol):
        return {'askPrice': '100', 'bidPrice': '99.99'}


class FuturesVenue:
    def __init__(self):
        self.settings = SimpleNamespace(mode='testnet', armed=True)
        self.orders = {}
        self.entry_id = ''
        self.closed = dec(0)
        self.stop_calls = []

    async def request(self, method, path, params, **kwargs):
        self.entry_id = params['newClientOrderId']
        self.orders[self.entry_id] = {'status': 'NEW', 'executedQty': '0'}
        return self.orders[self.entry_id]

    def fill(self, quantity, status='PARTIALLY_FILLED'):
        self.orders[self.entry_id] = {'status': status, 'executedQty': str(quantity)}
        return {'e': 'ORDER_TRADE_UPDATE', 'o': {'c': self.entry_id, 's': 'BTCUSDT',
                'z': str(quantity), 'X': status}}

    async def order(self, symbol, cid):
        return self.orders[cid]

    async def cancel(self, symbol, cid):
        self.orders[cid]['status'] = 'CANCELED'
        return self.orders[cid]

    async def protect(self, symbol, side, stop, cid):
        self.stop_calls.append(cid)
        return {'algoStatus': 'NEW'}

    async def algo(self, cid):
        return {'algoStatus': 'NEW' if cid in self.stop_calls else 'CANCELED'}

    async def cancel_algo(self, cid):
        self.stop_calls.remove(cid)
        return {'status': 'CANCELED'}

    async def reduce_position(self, symbol, side, quantity, cid):
        self.closed += dec(quantity)
        self.orders[cid] = {'status': 'FILLED', 'executedQty': str(quantity)}
        return self.orders[cid]

    async def positions(self, symbol):
        return [{'positionAmt': str(self.closed - dec(self.orders[self.entry_id]['executedQty']))}]


@pytest.fixture
def venues(tmp_path):
    journal = Journal(tmp_path / 'pairs.sqlite')
    f, s, authority = FuturesVenue(), SpotVenue(), TokenAuthority()
    gateway = PairExecutionGateway(f, s, authority, journal)
    yield f, s, authority, gateway
    journal.close()


def test_base_fee_coin_matching():
    row = {'executedQty': '1', 'fills': [{'qty': '1', 'commission': '.001', 'commissionAsset': 'BTC'}]}
    assert received_coins(row, 'BTC') == dec('.999')
    assert gross_buy_quantity('.999', '.001', False, '.001') == 1


def test_bnb_does_not_reduce_received_coin():
    row = {'executedQty': '1', 'fills': [{'qty': '1', 'commission': '.001', 'commissionAsset': 'BNB'}]}
    assert received_coins(row, 'BTC') == 1


def test_missing_commissions_not_treated_as_zero():
    with pytest.raises(ValueError):
        received_coins({'executedQty': '1'}, 'BTC')


async def test_perp_first_no_spot_before_fill(venues):
    f, s, a, g = venues
    pair = intent()
    state = await g.submit(pair, authorize(a, pair))
    assert state.state == 'PENDING_LEG1' and not s.calls


async def test_partial_then_full_hedge_and_duplicate_event(venues):
    f, s, a, g = venues
    p = intent()
    state = await g.submit(p, authorize(a, p))
    event = f.fill('.4')
    await g.on_user_event(event)
    await g.on_user_event(event)
    assert state.state == 'PARTIAL_HEDGED' and len(s.calls) == 1
    await g.on_user_event(f.fill('1', 'FILLED'))
    assert state.state == 'FULLY_HEDGED' and dec(state.spot_owned) == 1
    assert [q for _, q, _ in s.calls] == [dec('.4'), dec('.6')]


async def test_three_ioc_retries_then_emergency_close(venues):
    f, s, a, g = venues
    p = intent()
    state = await g.submit(p, authorize(a, p))
    s.failures = 4
    await g.on_user_event(f.fill('.4'))
    assert len(s.calls) == 4 and s.calls[0][2] is None
    assert all(c[2] == '100' for c in s.calls[1:])
    assert state.state == 'CLOSED' and f.closed == dec('.4') and g.halted


async def test_unknown_spot_post_reconciled_no_duplicate(venues):
    f, s, a, g = venues
    p = intent()
    state = await g.submit(p, authorize(a, p))
    s.lose_response = True
    await g.on_user_event(f.fill('1', 'FILLED'))
    assert state.state == 'FULLY_HEDGED' and len(s.calls) == 1


async def test_hedge_timeout_closes_both_even_if_buy_filled(venues):
    f, s, a, g = venues
    p = intent()
    state = await g.submit(p, authorize(a, p))
    g.hedge_deadline = .01
    s.delay = .02
    await g.on_user_event(f.fill('1', 'FILLED'))
    assert g.halted and state.state == 'CLOSED' and f.closed == 1
    assert [c[0] for c in s.calls] == ['BUY', 'SELL']


async def test_shutdown_cancels_and_closes_both_legs(venues):
    f, s, a, g = venues
    p = intent()
    state = await g.submit(p, authorize(a, p))
    await g.on_user_event(f.fill('.4'))
    await g.shutdown()
    assert state.state == 'CLOSED' and dec(state.spot_owned) == 0 and not f.stop_calls


async def test_recovery_uses_venue_ledger_not_assumed_fills(venues):
    f, s, a, g = venues
    p = intent()
    state = await g.submit(p, authorize(a, p))
    await g.on_user_event(f.fill('.4'))
    recovered = PairExecutionGateway(f, s, a, g.journal)
    await recovered.recover()
    assert recovered.pairs[state.pair_id].state == 'CLOSED'
    assert [c[0] for c in s.calls] == ['BUY', 'SELL']


def test_pair_token_binds_both_symbols():
    a, p = TokenAuthority(), intent()
    token = authorize(a, p)
    with pytest.raises(PermissionError):
        a.consume_pair(token, replace(p, base_asset='ETH', spot_symbol='ETHUSDT', perp_symbol='ETHUSDT'))


def test_pair_token_replay_denied():
    a, p = TokenAuthority(), intent()
    token = authorize(a, p)
    a.consume_pair(token, p)
    with pytest.raises(PermissionError):
        a.consume_pair(token, p)


async def test_shadow_gateway_never_submits(venues):
    f, s, a, g = venues
    p = intent(mode='shadow')
    with pytest.raises(PermissionError):
        await g.submit(p, authorize(a, p))
    assert not f.orders and not s.calls


def snapshot(**changes):
    return replace(CarrySnapshot('BTCUSDT', 100, 100.01, 100.1, 100.11,
                   .0005, 8, 3600, 30_000_000, 0, (.0005,) * 21,
                   bnb_ok=True, capacity_ok=True, accounts_verified=True), **changes)


@pytest.mark.parametrize('hours', [1, 4, 8])
def test_actual_funding_interval_and_full_costs(hours):
    s = snapshot(interval_hours=hours)
    plan, reason = plan_spot_perp(s, 1)
    assert reason == 'ELIGIBLE' and plan.apr == pytest.approx(.0005 * 24 / hours * 365)
    assert plan.cost_bps > 26 and plan.net_bps == plan.gross_bps - plan.cost_bps


def test_basis_over_1_5_percent_veto():
    s = snapshot(perp_bid=102, perp_ask=102.01)
    assert plan_spot_perp(s, 1)[1] == 'RUNE_BASIS'
    decision, votes = carry_votes(s, None, gateway_ready=True, exits_ready=True, now=1)
    assert decision.veto and next(v for v in votes if v.agent == 'RUNE').veto


@pytest.mark.parametrize('changes', [{'bnb_ok': False}, {'margin_ratio': .651}, {'accounts_verified': False}])
def test_rune_blocks_unfunded_or_unsafe_account(changes):
    s = snapshot(**changes)
    plan, _ = plan_spot_perp(s, 1)
    decision, votes = carry_votes(s, plan, gateway_ready=True, exits_ready=True, now=1)
    assert decision.veto and next(v for v in votes if v.agent == 'RUNE').veto


def test_eight_votes_strict_policy():
    s = snapshot()
    plan, _ = plan_spot_perp(s, 1)
    decision, votes = carry_votes(s, plan, gateway_ready=True, exits_ready=True, now=1)
    assert len(votes) == 8 and decision.allowed and decision.threshold == .768


def test_margin_uses_isolated_wallet_not_total_account():
    assert isolated_margin_ratio({'isolatedWallet': '100', 'unRealizedProfit': '-90',
                                  'maintMargin': '7', 'totalWalletBalance': '100000'}) == .7
    assert isolated_margin_ratio({}) == float('inf')


def test_no_funding_lookahead_or_short_horizon():
    assert plan_spot_perp(snapshot(observed_at=2), 1)[1] == 'STALE_DATA'
    assert plan_spot_perp(snapshot(), 1, horizon_days=1)[1] == 'HORIZON_OUTSIDE_7_30_DAYS'


@pytest.mark.parametrize('kwargs,reason', [({'rate': .00001}, 'APR_BELOW_15_PERCENT'),
    ({'oi_usdt': 20_000_000}, 'INSUFFICIENT_OI'),
    ({'spot_ask': 100.04}, 'SPOT_SPREAD'), ({'interval_hours': 2}, 'UNKNOWN_FUNDING_INTERVAL')])
def test_plan_filters(kwargs, reason):
    assert plan_spot_perp(snapshot(**kwargs), 1)[1] == reason


def test_net_hurdle_is_stricter_than_gross_hurdle():
    s = snapshot(rate=.00014, funding_history=(.00014,) * 21)
    assert plan_spot_perp(s, 1)[1] == 'ZEPHR_NET_HURDLE'


def test_pair_authority_rejects_lowered_policy():
    from core.consensus_engine import Decision
    with pytest.raises(PermissionError):
        TokenAuthority(threshold=.7).issue_pair(intent(), Decision('SELL', .72, True, False, '', .7))


@pytest.mark.parametrize('argv', [['--mode', 'mainnet'], ['--mode', 'mainnet', '--arm'],
                               ['--mode', 'shadow', '--arm']])
def test_mainnet_double_optin_and_shadow_immutable(argv):
    with pytest.raises(SystemExit):
        arguments(argv)


def test_shadow_credentials_and_environment_arm_ignored():
    f, s = venue_settings(arguments([]), {'ARM_LIVE_EXECUTION': 'true', 'BINANCE_MAINNET_API_KEY': 'secret'})
    assert not f.armed and not s.armed and f.api_key == s.api_key == ''


async def test_v3_account_schema_join():
    class Client:
        async def account(self):
            return {'positions': [{'symbol': 'BTCUSDT', 'isolatedWallet': '100', 'maintMargin': '2',
                                   'unrealizedProfit': '-10', 'positionAmt': '-1'}]}

        async def request(self, method, path, **kwargs):
            return {'canTrade': True} if path.endswith('accountConfig') else [
                {'symbol': 'BTCUSDT', 'marginType': 'ISOLATED', 'leverage': 2}]
    result = await futures_account(Client())
    assert result['canTrade'] and result['positions'][0]['isolated']
    assert result['positions'][0]['leverage'] == 2
    assert isolated_margin_ratio(result['positions'][0]) == pytest.approx(2 / 90)


async def test_watchdog_over_65_percent_unwinds(venues):
    f, s, a, g = venues
    p = intent()
    pair = await g.submit(p, authorize(a, p))
    await g.on_user_event(f.fill('1', 'FILLED'))
    async def account():
        return {'availableBalance': '0', 'positions': [{'symbol': 'BTCUSDT', 'positionAmt': '-1',
                'isolatedWallet': '100', 'unrealizedProfit': '-90', 'maintMargin': '7'}]}
    original = f.request
    async def request(method, path, params=None, **kwargs):
        if path.endswith('accountConfig'):
            return {'canTrade': True}
        if path.endswith('symbolConfig'):
            return [{'symbol': 'BTCUSDT', 'marginType': 'ISOLATED', 'leverage': 1}]
        return await original(method, path, params, **kwargs)
    f.account, f.request = account, request
    runtime = CarryRuntime(arguments(['--mode', 'testnet']), f, s, g.journal)
    runtime.gateway = g
    await runtime.watchdog_once()
    assert g.halted and pair.state == 'CLOSED' and f.closed == 1


def test_negative_funding_counts_settlements_not_polls(venues):
    f, s, a, g = venues
    from live.execution_gateway import PairState
    pair = PairState('p', intent(), opened=1)
    r = CarryRuntime(arguments([]), f, s, g.journal)
    history = [{'fundingTime': t, 'fundingRate': '-.001'} for t in (2000, 3000, 4000)]
    assert r.count_negative_settlements(pair, history[:1]) == 1
    assert r.count_negative_settlements(pair, history[:1]) == 1
    assert r.count_negative_settlements(pair, history) == 3


def test_commission_budget_includes_tax_and_never_understates_baseline():
    row = {'standardCommission': {'taker': '.001'}, 'taxCommission': {'taker': '.001'},
           'discount': {'enabledForAccount': True, 'enabledForSymbol': True,
                        'discountAsset': 'BNB', 'discount': '.75'}}
    assert commission_bps(row) == (17.5, True)


def test_runtime_carry_dispatch_avoids_legacy_runtime(monkeypatch):
    import core.runtime
    import core.carry_runtime
    calls = []
    monkeypatch.setattr(core.carry_runtime, 'main', lambda argv: calls.append(argv))
    core.runtime.main('paper', ['--carry', '--seconds', '1'])
    assert calls == [['--carry', '--seconds', '1']]


def trade(ident, price, buyer, venue='spot', stamp=1000, commission='0.1', asset='USDT'):
    return {'id': ident, 'orderId': ident, 'symbol': 'BTCUSDT', 'qty': '1', 'price': str(price),
            'quoteQty': str(price), 'isBuyer': buyer, 'side': 'BUY' if buyer else 'SELL',
            'realizedPnl': '2' if venue == 'futures' and buyer else '0', 'time': stamp,
            'commission': commission, 'commissionAsset': asset}


def income(ident=1, stamp=2000):
    return {'tranId': ident, 'incomeType': 'FUNDING_FEE', 'asset': 'USDT', 'time': stamp, 'income': '.5'}


def test_real_pair_pnl_basis_sign_fees_and_funding():
    spot = [trade(1, 100, True), trade(2, 101, False, stamp=3000)]
    futures = [trade(1, 103, False, 'futures'), trade(2, 101, True, 'futures', 3000)]
    report = pair_cashflow('pair', spot, futures, [income(), income()], base_asset='BTC',
                          spot_bid=101, perp_ask=101, fee_prices={})
    # basis: entry 100-103=-3, exit 101-101=0: +3; funding .5; four fees .4.
    assert report['status'] == 'RECONCILED' and report['closed']
    assert dec(report['net_usdt']) == dec('3.1') and dec(report['funding_usdt']) == dec('.5')


def test_open_net_includes_liquidation_fee_reserve():
    report = pair_cashflow('p', [trade(1, 100, True)], [trade(1, 103, False, 'futures')],
                          [income()], base_asset='BTC', spot_bid=101, perp_ask=101, fee_prices={})
    assert report['status'] == 'RECONCILED' and not report['closed']
    assert dec(report['net_usdt']) == dec('3.3') - dec('101') * dec('.0014')


def test_missing_commission_fx_blocks_profit_claim():
    report = pair_cashflow('p', [trade(1, 100, True, asset='BNB')],
                          [trade(1, 103, False, 'futures')], [], base_asset='BTC',
                          spot_bid=100, perp_ask=103, fee_prices={})
    assert report['status'] == 'INCOMPLETE' and 'MISSING_COMMISSION_FX:BNB' in report['issues']


def test_unattributed_funding_is_not_profit():
    report = pair_cashflow('p', [], [], [income()], base_asset='BTC',
                          spot_bid=100, perp_ask=100, fee_prices={})
    assert report['status'] == 'INCOMPLETE' and dec(report['funding_usdt']) == 0


def test_missing_expected_settlement_blocks_reconciliation():
    report = pair_cashflow('p', [trade(1, 100, True)], [trade(1, 100, False, 'futures')], [],
                          base_asset='BTC', spot_bid=100, perp_ask=100, fee_prices={},
                          expected_settlements=[{'fundingTime': 2000, 'fundingRate': '.001'}])
    assert 'MISSING_FUNDING_SETTLEMENT' in report['issues']


def test_spot_wallet_bnb_fee_and_external_deposit():
    t = trade(1, 100, True, commission='.001', asset='BNB')
    before = {'USDT': '1000', 'BNB': '1'}
    after = {'USDT': '900', 'BNB': '.999', 'BTC': '1'}
    assert wallet_reconciliation(before, after, [t, t], {'BTCUSDT': 'BTC'})['status'] == 'RECONCILED'
    after['USDT'] = '901'
    result = wallet_reconciliation(before, after, [t], {'BTCUSDT': 'BTC'})
    assert result == {'status': 'UNMATCHED', 'residuals': {'USDT': '1'}}


def test_weekly_only_reports_reconciled_realized_cycles():
    rows = [{'pair_id': 'p', 'status': 'RECONCILED', 'closed': True, 'closed_at': 1789430000,
             'funding_usdt': '2', 'basis_pnl_usdt': '3', 'fee_usdt': '1', 'net_usdt': '4'},
            {'pair_id': 'bad', 'status': 'INCOMPLETE'}]
    report = weekly_report(rows)
    assert '| 1 | 2 | 3 | 1 | 4 |' in report and 'bad' in report


def test_base_commission_not_double_counted_in_pnl():
    s = trade(1, 100, True, commission='.001', asset='BTC')
    f = dict(trade(1, 100, False, 'futures', commission='0'), qty='.999')
    report = pair_cashflow('p', [s], [f], [], base_asset='BTC', spot_bid=100,
                          perp_ask=100, fee_prices={}, exit_fee_bps=0)
    assert report['status'] == 'RECONCILED' and dec(report['net_usdt']) == dec('-.1')


async def test_unknown_spot_sell_reconciled_before_retry(venues):
    f, s, a, g = venues
    p = intent()
    pair = await g.submit(p, authorize(a, p))
    await g.on_user_event(f.fill('1', 'FILLED'))
    original = s.order
    fail_lookup = True
    async def lookup(symbol, cid):
        nonlocal fail_lookup
        if fail_lookup:
            fail_lookup = False
            raise BinanceError('TRANSPORT')
        return await original(symbol, cid)
    s.order = lookup
    s.lose_response = True
    with pytest.raises(RuntimeError):
        await g.unwind(pair.pair_id, 'EXIT')
    assert pair.pending_spot_sell and pair.state == 'UNWINDING'
    await g.unwind(pair.pair_id, 'RETRY')
    assert pair.state == 'CLOSED' and [c[0] for c in s.calls] == ['BUY', 'SELL']


async def test_tiny_partial_fill_closes_perp_without_invalid_spot_order(venues):
    f, s, a, g = venues
    p = intent()
    pair = await g.submit(p, authorize(a, p))
    await g.on_user_event(f.fill('.001'))
    assert not s.calls and pair.state == 'CLOSED' and f.closed == dec('.001')


async def test_cancel_fill_race_closes_late_perp_fill(venues):
    f, s, a, g = venues
    p = intent()
    pair = await g.submit(p, authorize(a, p))
    await g.on_user_event(f.fill('.4'))
    f.fill('.6')  # fill arrives at the venue before the cancel ACK, not yet on WS
    await g.unwind(pair.pair_id, 'EXIT')
    assert pair.state == 'CLOSED' and f.closed == dec('.6')
    assert [c[1] for c in s.calls] == [dec('.4'), dec('.4')]


async def test_external_stop_reduction_closes_remaining_spot(venues):
    f, s, a, g = venues
    p = intent()
    pair = await g.submit(p, authorize(a, p))
    await g.on_user_event(f.fill('1', 'FILLED'))
    f.closed = dec('1')
    await g.on_user_event({'e': 'ORDER_TRADE_UPDATE', 'o': {'c': 'exchange-stop', 's': 'BTCUSDT', 'x': 'TRADE'}})
    assert pair.state == 'CLOSED' and [c[0] for c in s.calls] == ['BUY', 'SELL']


@pytest.mark.parametrize('mode', ['shadow', 'testnet'])
def test_launcher_carry_commands(mode, monkeypatch):
    import run_launcher
    calls = []
    monkeypatch.setattr(run_launcher, 'run_python', lambda argv: calls.append(argv) or 0)
    assert run_launcher.carry(mode) == 0
    assert calls == [['-m', 'core.runtime', '--carry', '--mode', mode]]
    assert 'GHI L2' not in run_launcher.MENU


def test_launcher_cannot_arm_mainnet():
    import run_launcher
    with pytest.raises(ValueError):
        run_launcher.carry('mainnet')


async def test_spot_rest_routes_and_ioc_params():
    from live.binance_spot_client import BinanceSpotClient
    class Client(BinanceSpotClient):
        def __init__(self):
            self.calls = []
        async def request(self, method, path, params=None, **kwargs):
            self.calls.append((method, path, params, kwargs))
            return {'orderId': 12, 'status': 'FILLED', 'executedQty': '0'}
    c = Client()
    await c.create_order('BTCUSDT', 'BUY', '.01', 'c1')
    await c.create_order('BTCUSDT', 'BUY', '.01', 'c2', price='100')
    await c.cancel('BTCUSDT', 'c2')
    await c.my_trades('BTCUSDT', 12)
    assert c.calls[0][2]['type'] == 'MARKET' and 'timeInForce' not in c.calls[0][2]
    assert c.calls[1][2]['timeInForce'] == 'IOC' and c.calls[1][2]['newOrderRespType'] == 'FULL'
    assert c.calls[2][0:2] == ('DELETE', '/api/v3/order')
    assert c.calls[3][2]['orderId'] == 12


async def test_spot_mutations_blocked_unarmed():
    from live.binance_spot_client import BinanceSpotClient
    c = BinanceSpotClient(SimpleNamespace(armed=False))
    with pytest.raises(PermissionError):
        await c.create_order('BTCUSDT', 'BUY', '.01', 'c1')


def test_bat_resolves_workspace_and_launcher():
    from pathlib import Path
    text = (Path(__file__).parents[1] / 'run.bat').read_text()
    assert 'cd /d "%~dp0"' in text and '"%PY%" run_launcher.py' in text
    assert '--arm' not in text


def test_funding_timestamp_jitter_and_interval_changes():
    from core.carry_runtime import normalized_funding
    history = [{'fundingTime': 0, 'fundingRate': '.001'},
               {'fundingTime': 28800003, 'fundingRate': '.001'},
               {'fundingTime': 32400001, 'fundingRate': '.0002'}]
    assert normalized_funding(history, 1) == (.000125, .0002)
    with pytest.raises(ValueError):
        normalized_funding([history[0], {'fundingTime': 57600000, 'fundingRate': '.001'}], 8)


async def test_hedge_dispatch_latency_recorded_after_journaling(venues):
    import json
    f, s, a, g = venues
    p = intent()
    pair = await g.submit(p, authorize(a, p))
    await g.on_user_event(f.fill('1', 'FILLED'))
    event = json.loads(g.journal.db.execute("SELECT payload FROM events WHERE kind='PAIR_HEDGE_LATENCY'").fetchone()[0])
    assert event['pair_id'] == pair.pair_id and event['dispatch_ms'] >= 0
    assert event['within_100ms'] == (event['dispatch_ms'] < 100)


def test_process_lock_excludes_duplicate_and_releases(tmp_path):
    from core.carry_runtime import CarryProcessLock
    path = tmp_path / 'carry.lock'
    with CarryProcessLock(path):
        with pytest.raises(RuntimeError):
            with CarryProcessLock(path):
                pytest.fail('Duplicate lock was acquired')
    with CarryProcessLock(path):
        assert path.exists()


def test_future_account_fee_above_baseline_used():
    plan, _ = plan_spot_perp(snapshot(futures_fee_bps=10), 1)
    baseline, _ = plan_spot_perp(snapshot(), 1)
    assert plan.cost_bps - baseline.cost_bps == pytest.approx(4)


async def test_full_reconciler_fetches_owned_orders_funding_and_trades(tmp_path):
    from phan_tich.carry_lab import PairReconciler
    from live.execution_gateway import PairState
    spot_rows = [trade(11, 100, True), trade(12, 101, False, stamp=3000)]
    future_rows = [trade(21, 103, False, 'futures'), trade(22, 101, True, 'futures', 3000)]
    class Spot:
        async def order(self, symbol, cid):
            return {'orderId': int(cid), 'executedQty': '1'}
        async def my_trades(self, symbol, order_id):
            return [t for t in spot_rows if t['orderId'] == order_id]
    class Future:
        async def order(self, symbol, cid):
            return {'orderId': int(cid), 'executedQty': '1'}
        async def history_since(self, symbol, stamp):
            return future_rows, True
        async def request(self, method, path, params, **kwargs):
            if path.endswith('/income'):
                return [income()]
            assert path.endswith('/fundingRate')
            return [{'fundingTime': 2000, 'fundingRate': '.005', 'markPrice': '100'}]
    journal = Journal(tmp_path / 'reconcile.sqlite')
    try:
        pair = PairState('p', intent(), state='CLOSED', opened=.5, closed_at=4,
                         perp_filled='1', perp_closed='1')
        for role, cid in [('SPOT_BUY', '11'), ('SPOT_SELL', '12'), ('PERP_ENTRY', '21'), ('PERP_CLOSE', '22')]:
            journal.write('PAIR_ORDER_INTENT', {'pair_id': 'p', 'client_id': cid, 'role': role, 'symbol': 'BTCUSDT'})
        report, trades = await PairReconciler(Future(), Spot(), journal).pair_report(pair, 101, 101, end_ms=4000)
        assert report['status'] == 'RECONCILED' and dec(report['net_usdt']) == dec('3.1')
        assert len(trades) == 2
    finally:
        journal.close()


async def test_watchdog_rebalances_only_bounded_free_futures_cash(venues):
    f, s, a, g = venues
    p = intent()
    pair = await g.submit(p, authorize(a, p))
    await g.on_user_event(f.fill('1', 'FILLED'))
    wallet = dec('100')
    transfers = []
    async def account():
        return {'availableBalance': '10', 'positions': [{'symbol': 'BTCUSDT', 'positionAmt': '-1',
                'isolatedWallet': str(wallet), 'unrealizedProfit': '-90', 'maintMargin': '7'}]}
    async def request(method, path, params=None, **kwargs):
        nonlocal wallet
        if path.endswith('accountConfig'):
            return {'canTrade': True}
        if path.endswith('symbolConfig'):
            return [{'symbol': 'BTCUSDT', 'marginType': 'ISOLATED', 'leverage': 1}]
        assert path.endswith('positionMargin') and params['type'] == 1
        transfers.append(dec(params['amount']))
        wallet += transfers[-1]
        return {'code': 200}
    f.account, f.request = account, request
    r = CarryRuntime(arguments(['--mode', 'testnet']), f, s, g.journal)
    r.gateway = g
    await r.watchdog_once()
    assert transfers == [dec('4')]
    assert pair.state == 'FULLY_HEDGED' and not g.halted


async def test_runtime_profit_target_requires_wallet_reconciliation(venues, tmp_path):
    f, s, a, g = venues
    p = intent()
    pair = await g.submit(p, authorize(a, p))
    await g.on_user_event(f.fill('1', 'FILLED'))
    r = CarryRuntime(arguments(['--mode', 'testnet', '--journal', str(tmp_path / 'pairs.sqlite')]), f, s, g.journal)
    r.gateway = g
    r.start_ms = 0
    r.wallet_before = {'USDT': dec('1000')}
    r.market = {'BTCUSDT': {'spot': {'bidPrice': '100'}, 'perp': {'askPrice': '100'}}}
    t = trade(1, 100, True)
    async def account():
        return {'balances': [{'asset': 'BTC', 'free': '1', 'locked': '0'},
                             {'asset': 'USDT', 'free': '899.9', 'locked': '0'}]}
    s.account = account
    class Reconciler:
        async def pair_report(self, pair, spot, perp, **kwargs):
            return {'pair_id': pair.pair_id, 'status': 'RECONCILED', 'closed': False,
                    'net_usdt': '2', 'reported_at': 1, 'funding_usdt': '3',
                    'basis_pnl_usdt': '0', 'fee_usdt': '1'}, [t]
    r.reconciler = Reconciler()
    await r.reconcile_accounts()
    assert r.wallet_status == 'RECONCILED' and pair.state == 'CLOSED'
    assert (tmp_path / 'testnet-weekly.md').exists()
