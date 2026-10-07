"""Versioned, unit-safe settled outcomes; untyped legacy floats are not imported.

Also hosts R4, the 07:00 Asia/Saigon daily brief. The brief's arithmetic is
fixed and is not a model:

    PnL = dV_spot + dV_perp + sum(funding) - sum(fees)

All four terms are required on every leg. A brief that quietly reads a missing
funding or fee field as zero is the single easiest way to make a carry look
profitable, so `daily_brief` refuses an incomplete row instead of defaulting it.
"""
import json
import math

from core.routine_scheduler import SAIGON, brief_label, brief_window_start


class OutcomeLedger:
    def __init__(self, path, source):
        self.path, self.source = path, source
        self.records = []
        if path.exists():
            data = json.loads(path.read_text(encoding='utf-8'))
            if data.get('schema') != 2 or data.get('source') != source:
                raise ValueError('Outcome source/schema mismatch')
            for row in data['records']:
                self._validate(row)
            self.records = data['records'][-200:]

    ORIGINS = ('live', 'backfill')

    @staticmethod
    def _validate(row):
        fields = ('net_pnl_usdt', 'entry_notional_usdt', 'net_return', 'closed_at')
        if any(not isinstance(row.get(k), (float, int)) or not math.isfinite(row[k]) for k in fields):
            raise ValueError('Non-finite outcome')
        if row.get('origin', 'live') not in OutcomeLedger.ORIGINS:
            raise ValueError('Unknown outcome origin')
        if row['entry_notional_usdt'] <= 0 or not math.isclose(
                row['net_return'], row['net_pnl_usdt']/row['entry_notional_usdt'], rel_tol=1e-10, abs_tol=1e-12):
            raise ValueError('Outcome units do not reconcile')

    def window(self, start, end):
        """Settled rows with start <= closed_at < end, for the R4 cross-check."""
        return [r for r in self.records if start <= r['closed_at'] < end]

    def append(self, pnl, entry_notional, closed_at, symbol='', trade_id='', origin='live'):
        """Append one settled outcome. `origin` is provenance, never a filter.

        'backfill' marks a row the reconciler recovered after the fact from
        exchange history rather than one the live settler recorded. Backfilled
        rows carry the same venue numbers and MUST count toward Kelly exactly
        like live ones: the rows that go missing are the messy partial-fill
        ones, so dropping them is what biases the edge estimate optimistic in
        the first place.
        """
        row = {'net_pnl_usdt': pnl, 'entry_notional_usdt': entry_notional,
               'net_return': pnl/entry_notional, 'closed_at': closed_at,
               'symbol': symbol, 'trade_id': trade_id, 'origin': origin}
        self._validate(row)
        if trade_id and any(r.get('trade_id') == trade_id for r in self.records):
            return False
        self.records = (self.records+[row])[-200:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps({'schema': 2, 'source': self.source, 'records': self.records}, indent=2, allow_nan=False), encoding='utf-8')
        temp.replace(self.path)
        return True


# ================================================================= R4: daily brief
CARRY_COMPONENTS = ('delta_spot_usdt', 'delta_perp_usdt', 'funding_usdt', 'fees_usdt')


def carry_pnl(delta_spot, delta_perp, funding, fees):
    """PnL = dV_spot + dV_perp + sum(funding) - sum(fees). Fees enter POSITIVE."""
    values = (delta_spot, delta_perp, funding, fees)
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v)
           for v in values):
        raise ValueError('Non-finite carry component')
    return math.fsum((float(delta_spot), float(delta_perp), float(funding), -float(fees)))


def _leg(row):
    if not isinstance(row, dict):
        raise ValueError('Carry leg must be a mapping')
    missing = [k for k in CARRY_COMPONENTS if k not in row]
    if missing:
        raise ValueError('Carry leg is missing ' + ', '.join(missing))
    closed = row.get('closed_at')
    if not isinstance(closed, (int, float)) or isinstance(closed, bool) or not math.isfinite(closed):
        raise ValueError('Carry leg needs a finite closed_at')
    return float(closed), [float(row[k]) for k in CARRY_COMPONENTS]


def daily_brief(legs, now, settled=None, tz=SAIGON):
    """R4: settle one business day, 07:00 local to 07:00 local.

    `legs` are reconciled carry legs; only those closed inside the window count.
    `settled`, when given, is the ledger's own view of the same window
    (OutcomeLedger.window); the brief then cross-checks the two totals and
    reports UNMATCHED rather than publishing a number nothing agrees with.
    """
    end = brief_window_start(now, tz)
    start = end - 86400.0
    spot = perp = funding = fees = 0.0
    by_symbol, counted = {}, 0
    for row in legs or ():
        closed, (d_spot, d_perp, fund, fee) = _leg(row)
        if not start <= closed < end:
            continue
        counted += 1
        spot, perp, funding, fees = spot + d_spot, perp + d_perp, funding + fund, fees + fee
        symbol = str(row.get('symbol', ''))
        bucket = by_symbol.setdefault(symbol, {'legs': 0, 'net_usdt': 0.0})
        bucket['legs'] += 1
        bucket['net_usdt'] += carry_pnl(d_spot, d_perp, fund, fee)
    net = carry_pnl(spot, perp, funding, fees)
    brief = {'routine': 'reconcile-brief', 'label': brief_label(now, tz),
             'window_start': start, 'window_end': end, 'timezone': str(tz),
             'legs': counted, 'delta_spot_usdt': spot, 'delta_perp_usdt': perp,
             'funding_usdt': funding, 'fees_usdt': fees, 'net_pnl_usdt': net,
             'by_symbol': by_symbol, 'status': 'RECONCILED'}
    if settled is not None:
        ledger_net = math.fsum(float(r['net_pnl_usdt']) for r in settled)
        brief['ledger_net_pnl_usdt'] = ledger_net
        brief['ledger_rows'] = len(list(settled))
        if not math.isclose(net, ledger_net, rel_tol=1e-9, abs_tol=1e-9):
            brief['status'] = 'UNMATCHED'
            brief['reason'] = ('legs net %.8f != ledger net %.8f' % (net, ledger_net))
    return brief
