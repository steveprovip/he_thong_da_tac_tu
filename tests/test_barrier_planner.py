import pytest
from core.barrier_planner import BarrierPlanner, reservation_price


@pytest.mark.parametrize('side,sign', [('BUY', 1), ('SELL', -1)])
def test_barriers_and_monotonic_breakeven(side, sign):
    p = BarrierPlanner()
    initial = p.plan(side, 100, .001, .1, 0)
    assert sign*(100-initial.stop) >= .2-1e-9
    update = p.update(side, 100, 100+sign, .1, 0, 100, initial, initial.stop)
    assert sign*(update.stop-100) > 0
    next_update = p.update(side, 100, 100, .1, 0, 200, initial, update.stop)
    assert next_update.stop == update.stop
    assert abs(next_update.take_profit-100) <= abs(update.take_profit-100)+1e-9


def test_inventory_reservation_units():
    assert reservation_price(100, 2, .1, .5, 10) == 99
