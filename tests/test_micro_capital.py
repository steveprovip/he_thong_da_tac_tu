"""3.85 USDT channel math: admissibility, break-even, and the gates that must NOT move."""
import math

import pytest

from core.funding_carry import TWO_LEG_MIN_NOTIONAL_USDT, two_leg_capital_gate
from research.micro_capital import (CHANNELS, MAX_TRANSFER_OVERHEAD_USDT, Channel, capital_needed,
                                    evaluate, main, rank, winner)

K = 3.85


def ch(key):
    return next(c for c in CHANNELS if c.key == key)


def test_net_and_breakeven_formula():
    c = Channel('X', 'x', apr=.12, trade_cost_bps=100, fee_in_usdt=.05, fee_out_usdt=.05,
                min_capital_usdt=1, legal_vn=True, risk='')
    r = evaluate(c, 10.0, 12)
    assert r['gross_usdt'] == pytest.approx(1.2)
    assert r['cost_usdt'] == pytest.approx(.2)
    assert r['net_per_month_usdt'] == pytest.approx(1.0 / 12)
    assert r['breakeven_months'] == pytest.approx(.2 / .1)
    assert r['admissible']


def test_illegal_or_underfunded_or_costly_channels_are_rejected():
    assert 'ILLEGAL_IN_VN' in evaluate(ch('POLYMARKET_NEGRISK'), K)['reasons']
    assert any(r.startswith('CAPITAL') for r in evaluate(ch('BYBIT_CARRY'), K)['reasons'])
    defi = evaluate(ch('DEFI_SOLANA_LEND'), K)
    assert any(r.startswith('TRANSFER') for r in defi['reasons'])
    assert ch('DEFI_SOLANA_LEND').fee_in_usdt + ch('DEFI_SOLANA_LEND').fee_out_usdt > MAX_TRANSFER_OVERHEAD_USDT


def test_winner_for_3_85_usdt_is_the_zero_transfer_channel():
    w = winner(K)
    assert w['key'] == 'BINANCE_SIMPLE_EARN'
    assert w['net_per_month_usdt'] == pytest.approx(K * .02 / 12)
    assert rank(K)[0]['key'] == 'BINANCE_SIMPLE_EARN'


def test_bybit_carry_becomes_admissible_only_with_enough_capital():
    assert not evaluate(ch('BYBIT_CARRY'), K)['admissible']
    assert evaluate(ch('BYBIT_CARRY'), 10.0)['admissible']
    assert capital_needed(ch('BYBIT_CARRY'), 1.0) > 100
    assert math.isinf(capital_needed(ch('POLYMARKET_NEGRISK'), 1.0))


def test_capital_gate_is_not_lowered_for_micro_capital():
    """Venue minimums, not our gate, set the floor: a 2-USDT gate would only buy rejected
    orders or an unhedged single leg."""
    assert TWO_LEG_MIN_NOTIONAL_USDT == 10.0
    assert not two_leg_capital_gate(K, 2.0, 'testnet').allowed
    assert not two_leg_capital_gate(K, None, 'testnet').allowed


def test_cli_prints_a_choice(capsys):
    assert main(['--capital', '3.85']) == 0
    out = capsys.readouterr().out
    assert 'KENH CHON: Binance Simple Earn' in out
    assert main(['--capital', '1.0', '--earn-apr', '0']) == 0
    assert 'KHONG CO' in capsys.readouterr().out
