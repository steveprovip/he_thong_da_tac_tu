"""Funding-carry engine: fee-hurdle math, side selection, deadline clamps."""
import pytest

from core.funding_carry import CarryConfig, FundingCarry
from core.models import Intent, quantize

NOW = 1_700_000_000.0
NEXT = NOW + 3600


def premium_row(symbol='BTCUSDT', rate='0.0001', mark='100', index='100', next_s=None):
    return {'symbol': symbol, 'lastFundingRate': rate, 'markPrice': mark,
            'indexPrice': index,
            'nextFundingTime': int((NEXT if next_s is None else next_s)*1000)}


def engine(rate, **config):
    carry = FundingCarry(CarryConfig(**config))
    carry.update([premium_row(rate=rate)], NOW)
    return carry


def test_parse_drops_invalid_rows():
    carry = FundingCarry()
    carry.update([
        premium_row(),
        {'symbol': 'BADUSDT'},
        'not-a-row',
        premium_row(symbol='NANUSDT', rate='nan'),
        premium_row(symbol='ZEROMARK', mark='0'),
        premium_row(symbol='HUGERATE', rate='0.5'),
        premium_row(symbol='PAST', next_s=NOW-1),
    ], NOW)
    # Parse keeps well-formed rows even if the print already passed; plan() refuses them.
    assert set(carry.rows) == {'BTCUSDT', 'PAST'}
    assert carry.plan('PAST', 100.0, 1.0, 0.5, 0.01, NOW)[1] == 'FUNDING_STALE'


def test_non_list_payload_is_ignored():
    carry = FundingCarry()
    carry.update({'error': 'x'}, NOW)
    assert carry.rows == {}


def test_side_follows_funding_sign():
    long_plan, _ = engine('0.005').plan('BTCUSDT', 100.0, 1.0, 0.5, 0.01, NOW)
    short_plan, _ = engine('-0.005').plan('BTCUSDT', 100.0, 1.0, 0.5, 0.01, NOW)
    assert long_plan.side == 'SELL' and short_plan.side == 'BUY'
    # Bracket ordering must satisfy Intent validation for each side.
    assert short_plan.stop < 100.0 < short_plan.take_profit
    assert long_plan.take_profit < 100.0 < long_plan.stop


def test_tiny_funding_is_noise():
    plan, why = engine('0.0001').plan('BTCUSDT', 100.0, 1.0, 0.5, 0.01, NOW)
    assert plan is None and why == 'CARRY_TOO_SMALL'


def test_fee_hurdle_rejects_moderate_funding():
    # 10 bps/8h vs cost 6.5 bps (6.0 + 1.0/2): edge 3.5 < 1.8*6.5 = 11.7 -> refuse.
    plan, why = engine('0.001').plan('BTCUSDT', 100.0, 1.0, 0.5, 0.01, NOW)
    assert plan is None and why == 'EDGE_BELOW_HURDLE'


def test_extreme_funding_authorized():
    plan, why = engine('0.005').plan('BTCUSDT', 100.0, 1.0, 0.5, 0.01, NOW)
    assert why == 'AUTHORIZED' and plan is not None
    assert plan.rate_bps == pytest.approx(50.0)
    assert plan.cost_bps == pytest.approx(6.5)
    assert plan.edge_bps == pytest.approx(43.5)
    assert plan.horizon == pytest.approx(NEXT-NOW+120)


def test_plan_survives_intent_validation():
    from decimal import Decimal
    for rate in ('0.005', '-0.005'):
        plan, _ = engine(rate).plan('BTCUSDT', 100.0, 1.0, 0.5, 0.01, NOW)
        tick = Decimal('0.01')
        intent = Intent(plan.symbol, plan.side, '1.0', '100.00',
                        format(quantize(plan.stop, tick), 'f'),
                        format(quantize(plan.take_profit, tick), 'f'),
                        plan.horizon, 'testnet')
        assert intent.symbol == 'BTCUSDT'


def test_stale_and_missing_funding():
    carry = FundingCarry()
    assert carry.plan('BTCUSDT', 100.0, 1.0, 0.5, 0.01, NOW)[1] == 'NO_FUNDING_DATA'
    carry.update([premium_row(next_s=NOW-30)], NOW)
    assert carry.plan('BTCUSDT', 100.0, 1.0, 0.5, 0.01, NOW)[1] == 'FUNDING_STALE'


def test_wide_spread_refused():
    plan, why = engine('0.005').plan('BTCUSDT', 100.0, 30.0, 0.5, 0.01, NOW)
    assert plan is None and why == 'SPREAD_TOO_WIDE'


def test_horizon_clamped_to_config():
    near, _ = engine('0.005').plan('BTCUSDT', 100.0, 1.0, 0.5, 0.01, NOW+3540)
    assert near.horizon == 300.0  # at least min_horizon even if print is close
    far = FundingCarry(CarryConfig(max_horizon=900.0))
    far.update([premium_row(rate='0.005', next_s=NOW+8*3600)], NOW)
    plan, _ = far.plan('BTCUSDT', 100.0, 1.0, 0.5, 0.01, NOW)
    assert plan.horizon == 900.0


def test_snapshot_ranks_by_annualized_extremes():
    carry = FundingCarry()
    carry.update([premium_row('BTCUSDT', rate='0.001'),
                  premium_row('ETHUSDT', rate='-0.004'),
                  premium_row('ADAUSDT', rate='0.0002')], NOW)
    snap = carry.snapshot()
    assert [row['symbol'] for row in snap[:2]] == ['ETHUSDT', 'BTCUSDT']
    assert snap[0]['annualized_bps'] == pytest.approx(-0.004*3*365*1e4)
    assert snap[0]['side'] == 'BUY'


def test_cli_flags():
    from core.runtime import arguments
    assert arguments('testnet', []).no_carry is False
    assert arguments('testnet', ['--no-carry']).no_carry is True


async def test_funding_scanner_never_fabricates_consensus_or_opens_position(monkeypatch):
    """Extreme unhedged funding is an observation, not eight agent approvals."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from decimal import Decimal
    from config.settings import Settings
    from core.models import Book, Filters
    from core.runtime import Runtime, arguments
    from core.web_ui_server import Telemetry

    rt = Runtime(Settings.load('paper', env={}),
                 arguments('paper', ['--no-ui']), Telemetry())
    rt.client = SimpleNamespace(premium_index=AsyncMock(
        return_value=[premium_row('BTCUSDT', rate='0.005')]), offset_ms=0)
    now = NOW + 100
    book = Book('BTCUSDT', ((99.99, 5), (99.98, 5)),
                ((100.01, 5), (100.02, 5)), timestamp=now)
    rt.books['BTCUSDT'] = book
    rt.filters['BTCUSDT'] = Filters(Decimal('0.01'), Decimal('0.01'),
                                    Decimal('0.01'), Decimal('10000'), Decimal('5'))
    rt.alpha['BTCUSDT'] = SimpleNamespace(features=lambda: SimpleNamespace(
        ready=True, vpin=None, atr=0.5, variance_per_second=0.0))
    await rt.maybe_carry(now, {s: a.features() for s, a in rt.alpha.items()})
    assert rt.carry_tag is None
    assert not rt.paper.orders
    assert rt.authorized == 0
    assert rt.carry.snapshot()[0]['symbol'] == 'BTCUSDT'
    # Second scan is rate-limited and a carry slot is already taken.
    await rt.maybe_carry(now, {s: a.features() for s, a in rt.alpha.items()})
    assert rt.client.premium_index.await_count == 1
