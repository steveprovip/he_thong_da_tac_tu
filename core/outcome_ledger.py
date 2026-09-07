"""Versioned, unit-safe settled outcomes; untyped legacy floats are not imported."""
import json
import math


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
