from core.models import Filters, dec
from core.position_sizer import PositionSizer, DrawdownBrake

FILTERS = Filters(*map(dec, ['.1', '.001', '.001', '100', '100']))


def test_kelly_warmup_negative_edge_and_window():
    s = PositionSizer()
    assert s.kelly() is None
    assert s.size(10000, 0, 60000, 59000, FILTERS).reason == 'KELLY_WARMUP'
    for i in range(210):
        s.record(-1)
    assert len(s.outcomes) == 200 and s.kelly() == 0
    assert s.size(10000, 0, 60000, 59000, FILTERS).quantity == '0'


def test_round_up_minimum_without_breaking_caps():
    s = PositionSizer()
    result = s.size(1000, 0, 60000, 59000, FILTERS, bootstrap=True)
    assert result.quantity == '0.002'
    assert result.notional == 120
    assert s.size(10, 0, 60000, 59000, FILTERS, bootstrap=True).quantity == '0'
    assert s.size(1000, 749, 60000, 59000, FILTERS, bootstrap=True).quantity == '0'


def test_fractional_kelly_and_latched_brake():
    s = PositionSizer()
    for value in [2]*12+[-1]*8:
        s.record(value)
    assert abs(s.kelly()-.10) < 1e-10
    brake = DrawdownBrake()
    assert not brake.update(100)
    assert brake.update(90)
    assert brake.update(110)
