"""Emergency flatten: the one code path allowed to act without a VESKA token.

Used by the user-initiated full shutdown (dashboard kill switch and
tools/shutdown_system.py). Every action here is risk-reducing by construction:
it never opens exposure -- it cancels working orders, closes existing positions
reduce-only, and removes protection algos only after the position is flat.
"""
import uuid

from live.binance_futures_client import BinanceError

# Binance codes that mean "nothing to cancel" and are safe to ignore here.
_BENIGN_CANCEL_CODES = (-2011, -2013)


async def flatten_venue(client, symbols=(), journal=None):
    """Cancel every order and algo, close every position reduce-only.

    symbols: explicit watchlist to sweep; when empty it is derived from the
    venue's non-zero positions, so this also works against a stale account
    whose positions were opened by a session that is already gone.
    Returns the list of close records: {'symbol', 'quantity', 'client_id'}.
    """
    report = []
    if symbols:
        check = list(symbols)
    else:
        inventory = await client.positions()
        check = sorted({p.get('symbol') for p in inventory if float(p.get('positionAmt', 0))})
    for symbol in check:
        closed = None
        for order in await client.open_orders(symbol):
            try:
                await client.cancel(symbol, order.get('origClientOrderId', ''))
            except BinanceError as error:
                if error.uncertain or error.code not in _BENIGN_CANCEL_CODES:
                    raise
        legs = await client.positions(symbol)
        amount = sum(float(p.get('positionAmt', 0)) for p in legs)
        if amount:
            client_id = 'gf-'+uuid.uuid4().hex[:28]
            await client.reduce_position(symbol, 'SELL' if amount > 0 else 'BUY', abs(amount), client_id)
            closed = {'symbol': symbol, 'quantity': abs(amount), 'client_id': client_id}
            report.append(closed)
        # Protection is removed only after the position is flat, so the venue
        # stop keeps guarding the leg while the market order is in flight.
        for algo in await client.open_algos(symbol):
            try:
                await client.cancel_algo(algo.get('clientAlgoId', ''))
            except BinanceError as error:
                if error.uncertain or error.code not in _BENIGN_CANCEL_CODES:
                    raise
        if journal:
            journal.write('FLATTEN', {'symbol': symbol, 'closed': closed})
    return report