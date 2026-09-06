import pytest
from simulation.paper_engine import PaperEngine
from core.models import Intent, Book


def test_queue_latency_partial_fills_and_fees():
    engine = PaperEngine(latency=.1)
    book = Book('X', ((100., 2.),), ((101., 2.),), 0)
    order = Intent('X', 'BUY', '1', '100', '99', '103', 300, 'paper')
    engine.submit(order, book, 0)
    assert engine.trade('X', 100, 5, False, .05, 1) == 0
    assert engine.trade('X', 100, 2.5, False, .2, 2) == .5
    assert engine.trade('X', 100, 2.5, False, .2, 2) == 0
    assert engine.trade('X', 100, .5, False, .3, 3) == .5
    result = engine.close('X', Book('X', ((102., 2.),), ((103., 2.),), 1))
    assert result['net_pnl'] == pytest.approx(2-.02-.051)
    assert engine.cash == pytest.approx(10000+result['net_pnl'])


def test_no_invented_depth_and_cash_transfer():
    engine = PaperEngine(latency=0)
    book = Book('X', ((100., 1.),), ((101., 1.),), 0)
    engine.submit(Intent('X', 'BUY', '2', '100', '99', '104', 300, 'paper'), book, 0)
    engine.trade('X', 100, 3, False, 1, 1)
    assert engine.close('X', book) is None
    assert engine.orders['X'].filled == 1
    engine.close('X', book)
    engine.transfer(100)
    assert engine.initial == 10100
