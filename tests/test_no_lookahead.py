from core.real_alpha_engine import AlphaEngine
from core.models import Book


def replay(count):
    engine = AlphaEngine(2, 2)
    history = []
    for i in range(count):
        price = 100+i*.01
        engine.on_book(Book('X', ((price, 10),), ((price+.1, 12),), i, i))
        engine.on_bar(i, price+.2, price-.2, price)
        engine.on_trade(price, 1, i % 2 == 0, i, i)
        history.append(engine.features())
    return history


def test_future_suffix_cannot_change_prefix():
    assert replay(30) == replay(60)[:30]
