"""Radar auto-trader: filters, bracket math, shadow lifecycle, kill latch, demo venue mapping."""
import json
from types import SimpleNamespace

import pytest

from live import radar_auto_trader as rt
from live.binance_futures_client import BinanceError
from research import alpha_radar as ar

LEG = dict(sl_mode='money', max_loss=0.05, kill_loss=0.05, tp_pcts=(2.0, 5.0), surge_min=3.0, taker_min=0.55,
           max_hold_s=4 * 3600.0)                                   # v1/v2 behaviour kept as regression
CFG = rt.TraderConfig(poll_s=0.0, scan_every_s=0.0, entry='market', **LEG)
RCFG = rt.TraderConfig(poll_s=0.0, scan_every_s=0.0, entry='retest', **LEG)
V3 = rt.TraderConfig(poll_s=0.0, scan_every_s=0.0)                    # defaults: ATR stop, 5x, 60 %
SPEC = {'step': 1.0, 'min_qty': 1.0, 'min_notional': 5.0, 'tick': 0.0001}
EXINFO = {'symbols': [{'symbol': 'AAAUSDT', 'status': 'TRADING', 'quoteAsset': 'USDT', 'contractType': 'PERPETUAL',
                       'filters': [{'filterType': 'LOT_SIZE', 'stepSize': '1', 'minQty': '1'},
                                   {'filterType': 'MIN_NOTIONAL', 'notional': '5'},
                                   {'filterType': 'PRICE_FILTER', 'tickSize': '0.0001'}]}]}


def pick(sym='AAAUSDT', side='LONG', price=1.0, cfg=None, **kw):
    long = side == 'LONG'
    p = {'symbol': sym, 'side': side, 'price': price, 'surge': 6.0, 'window_min': 15,
         'ret_pct': 1.0 if long else -1.0, 'rsi': 65.0 if long else 35.0, 'taker': 0.6 if long else 0.4, 'obi': 0.2,
         'score': 9.0, 'chase': False, 'trend': {'1h': 'UP', '4h': 'UP'}, 'trend_ok': True,
         'retest': {'price': price * (0.992 if long else 1.008), 'pullback_pct': 0.8, 'anchor': 'ema20'}}
    p.update(kw)
    p['plan'] = ar.trade_plan(price, side, SPEC, (cfg or CFG).radar_config(), atr=p.get('atr_pct'))
    return p


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def make(tmp_path, quote, picks=None, cfg=CFG, clock=None):
    q = {'AAAUSDT': quote}
    venue = rt.ShadowVenue(cfg, quote=lambda s: q[s], http=lambda url: EXINFO)
    calls = []

    def radar():
        calls.append(1)
        return {'picks': picks if picks is not None else [pick()]}
    tr = rt.AutoTrader(venue, cfg, radar, rt.Ledger(tmp_path / 'ledger.jsonl', 'shadow'),
                       state_file=tmp_path / 'state.json', shutdown_file=tmp_path / 'stop.json',
                       clock=clock or Clock())
    return tr, venue, q, calls


# ----------------------------------------------------------------------------- filters
@pytest.mark.parametrize('kw,why', [({'chase': True}, 'CHASE'), ({'ret_pct': 2.5}, 'MOVE'),
                                    ({'rsi': 80.0}, 'RSI'), ({'rsi': 50.0}, 'RSI'), ({'surge': 2.5}, 'SURGE')])
def test_eligible_rejects(kw, why):
    ok, reason = rt.eligible(pick(**kw), CFG)
    assert not ok and why in reason


def test_eligible_accepts_and_mirrors_short():
    assert rt.eligible(pick(), CFG) == (True, 'OK')
    assert rt.eligible(pick(side='SHORT'), CFG) == (True, 'OK')
    assert not rt.eligible(pick(side='SHORT', ret_pct=-2.5), CFG)[0]
    assert not rt.eligible(pick(side='SHORT', rsi=20.0), CFG)[0]


def test_eligible_rejects_margin_over_cap():
    p = pick()
    p['plan'] = dict(p['plan'], leverage=5, margin=1.2)
    assert rt.eligible(p, CFG) == (False, 'MARGIN')


def test_choose_skips_cooldown_and_ineligible():
    picks = [pick('AAAUSDT'), pick('BBBUSDT', chase=True), pick('CCCUSDT')]
    best, rej = rt.choose({'picks': picks}, CFG, {'AAAUSDT': 10.0}, 5.0)
    assert best['symbol'] == 'CCCUSDT' and len(rej) == 2


# ----------------------------------------------------------------------------- bracket math
def test_bracket_caps_loss_and_splits_tps():
    b = rt.bracket('BUY', 1.0006, 6.0, SPEC, CFG)
    assert b['worst_loss'] <= CFG.max_loss + 1e-9
    assert b['sl'] < 1.0006 and 0.5 < b['sl_pct'] < 0.9
    assert [q for _, q in b['tps']] == [3.0, 3.0]
    assert b['tps'][0][0] == pytest.approx(1.0006 * 1.02, abs=1e-4)
    short = rt.bracket('SELL', 1.0, 6.0, SPEC, CFG)
    assert short['sl'] > 1.0 and short['tps'][0][0] < 1.0


def test_bracket_single_tp_when_size_cannot_split():
    b = rt.bracket('BUY', 500.0, 0.01, {'step': 0.01, 'tick': 0.01}, CFG)
    assert b['tps'] == [(510.0, 0.01)]


def test_fmt_and_rounding():
    assert rt.fmt(0.1 + 0.2, 0.001) == '0.300' and rt.floor_step(3.9, 1.0) == 3.0
    assert rt.round_tick(0.99376, 0.0001, 'up') == 0.9938 and rt.round_tick(0.99376, 0.0001, 'down') == 0.9937


# ----------------------------------------------------------------------------- ledger
def test_ledger_today_kill_and_reset(tmp_path):
    lg = rt.Ledger(tmp_path / 'l.jsonl', 'shadow')
    lg.write('TRADE', pnl=-0.03)
    rt.Ledger(tmp_path / 'l.jsonl', 'demo').write('TRADE', pnl=-9.0)      # other mode ignored
    lg.write('KILL', reason='x')
    pnl, killed, trades = lg.today()
    assert pnl == pytest.approx(-0.03) and killed and len(trades) == 1
    lg.write('RESET')
    assert lg.today() == (0.0, False, [])


# ----------------------------------------------------------------------------- shadow lifecycle
async def test_full_cycle_tp1_then_breakeven_exit(tmp_path):
    tr, venue, q, calls = make(tmp_path, (0.9999, 1.0001))
    await tr.boot()
    assert await tr.step() == 'IN_POSITION AAAUSDT'
    p = tr.pos
    assert p['qty'] == 6.0 and p['sl'] < p['entry'] and len(p['tps']) == 2
    assert {o['kind'] for o in venue.orders.values() if o['status'] == 'NEW'} == {'SL', 'TP'}
    q['AAAUSDT'] = (1.021, 1.0212)                         # TP1 trades
    await tr.step()
    assert tr.pos['tps'][0]['filled'] and tr.pos['be'] and tr.pos['sl'] == pytest.approx(tr.pos['entry'])
    assert await venue.stop_live('AAAUSDT', tr.pos['sl_cid'])
    q['AAAUSDT'] = (1.0, 1.0002)                           # back through entry: BE stop
    assert await tr.step() == 'SCANNING'
    t = tr.trades[0]
    assert t['reason'] == 'TP1+BE' and t['pnl'] > 0.04
    assert not await venue.exposure()                      # nothing left working
    rows = [json.loads(x) for x in (tmp_path / 'ledger.jsonl').read_text().splitlines()]
    assert rows[-1]['kind'] == 'TRADE' and rows[-1]['reason'] == 'TP1+BE'
    s = json.loads((tmp_path / 'state.json').read_text(encoding='utf-8'))
    assert s['equity'] > 3.85 and s['trades'][0]['reason'] == 'TP1+BE' and s['realized_pnl'] > 0


async def test_stop_at_price_loses_at_most_cap(tmp_path):
    tr, venue, q, _ = make(tmp_path, (0.9999, 1.0001))
    await tr.boot()
    await tr.step()
    sl = tr.pos['sl']
    q['AAAUSDT'] = (sl, sl + 0.0002)
    await tr.step()
    assert tr.pos is None and tr.trades[0]['reason'] == 'STOP'
    assert -0.0502 <= tr.trades[0]['pnl'] < -0.04


async def test_gap_through_stop_latches_daily_kill(tmp_path):
    tr, venue, q, calls = make(tmp_path, (0.9999, 1.0001))
    await tr.boot()
    await tr.step()
    q['AAAUSDT'] = (0.99, 0.9902)                          # gaps past the stop
    assert (await tr.step()).startswith('HALTED') or tr.halted
    assert tr.halted and tr.trades[0]['pnl'] < -0.05
    n = len(calls)
    await tr.step()
    assert len(calls) == n                                 # halted: no more scans / entries
    tr2, *_ = make(tmp_path, (0.99, 0.9902))
    await tr2.boot()
    assert tr2.halted and 'daily kill' in tr2.halt_reason   # latch survives restart


async def test_unrealized_kill_flattens_before_stop(tmp_path):
    cfg = rt.TraderConfig(poll_s=0.0, scan_every_s=0.0, entry='market', **dict(LEG, kill_loss=0.01))
    tr, venue, q, _ = make(tmp_path, (0.9999, 1.0001), cfg=cfg)
    await tr.boot()
    await tr.step()
    q['AAAUSDT'] = (0.998, 0.9982)                         # above the stop, below the kill line
    await tr.step()
    assert tr.halted and tr.pos is None and venue.amt['AAAUSDT'] == 0
    assert tr.ledger.today()[1]                            # KILL latched


async def test_dashboard_stop_flattens_without_daily_latch(tmp_path):
    tr, venue, q, _ = make(tmp_path, (0.9999, 1.0001))
    await tr.boot()
    await tr.step()
    (tmp_path / 'stop.json').write_text('{}')
    assert await tr.step() == 'SHUTDOWN'
    assert tr.pos is None and venue.amt['AAAUSDT'] == 0 and not tr.ledger.today()[1]


async def test_one_position_max_no_scan_while_open(tmp_path):
    tr, venue, q, calls = make(tmp_path, (0.9999, 1.0001))
    await tr.boot()
    await tr.step()
    for _ in range(3):
        await tr.step()
    assert len(calls) == 1 and len([o for o in venue.orders.values() if o['kind'] == 'MARKET']) == 1


async def test_boot_refuses_existing_exposure(tmp_path):
    tr, venue, q, calls = make(tmp_path, (0.9999, 1.0001))
    venue.amt['AAAUSDT'] = 5.0
    await tr.boot()
    await tr.step()
    assert tr.halted and 'exposure' in tr.halt_reason and not calls


async def test_time_stop_closes(tmp_path):
    clock = Clock()
    cfg = rt.TraderConfig(poll_s=0.0, scan_every_s=1e9, entry='market', **dict(LEG, max_hold_s=10.0))
    tr, venue, q, _ = make(tmp_path, (0.9999, 1.0001), cfg=cfg, clock=clock)
    await tr.boot()
    await tr.step()
    clock.t += 11
    await tr.step()
    await tr.step()
    assert tr.pos is None and tr.trades[0]['reason'] == 'TIME'


async def test_stop_failure_closes_immediately(tmp_path):
    tr, venue, q, _ = make(tmp_path, (0.9999, 1.0001))

    async def broken(*a, **k):
        raise RuntimeError('algo down')
    venue.stop = broken
    await tr.boot()
    await tr.step()
    assert tr.pos is None and venue.amt['AAAUSDT'] == 0 and tr.trades[0]['reason'] == 'NO_STOP'


async def test_gtx_unfilled_is_cancelled_no_chase(tmp_path):
    cfg = rt.TraderConfig(poll_s=0.0, scan_every_s=0.0, entry='gtx', gtx_ttl_s=0.0, **LEG)
    tr, venue, q, _ = make(tmp_path, (0.9999, 1.0001), cfg=cfg)
    await tr.boot()
    assert await tr.step() == 'ENTRY_PENDING AAAUSDT'
    await tr.step()
    assert tr.pos is None and tr.pending is None and 'AAAUSDT' in tr.cooldown
    assert [o['status'] for o in venue.orders.values()] == ['CANCELED']


# ----------------------------------------------------------------------------- demo venue mapping
class FakeClient:
    def __init__(self, rest='https://demo-fapi.binance.com', mode='testnet'):
        self.settings = SimpleNamespace(rest=rest, mode=mode)
        self.calls = []

    async def request(self, method, path, params=None, signed=False, key=False, weight=1):
        self.calls.append((method, path, dict(params or {})))
        if path == '/fapi/v1/marginType':
            raise BinanceError(-4046, 400)
        if path == '/fapi/v1/leverage':
            return {'leverage': params['leverage']}
        if path == '/fapi/v1/order':
            return {'status': 'FILLED', 'avgPrice': '1.0005', 'executedQty': params['quantity']}
        if path == '/fapi/v1/positionSide/dual':
            return {'dualSidePosition': False}
        return {}

    async def reduce_maker(self, *a):
        raise BinanceError(-5022, 400)

    async def protect(self, symbol, side, stop, cid):
        self.calls.append(('POST', 'algo', {'side': side, 'stop': stop}))
        return {'algoStatus': 'NEW'}

    async def history_since(self, symbol, start):
        return ([{'realizedPnl': '0.10', 'commission': '0.01', 'commissionAsset': 'USDT'},
                 {'realizedPnl': '0', 'commission': '0.003', 'commissionAsset': 'USDT'}], True)


def test_demo_venue_refuses_mainnet():
    with pytest.raises(PermissionError):
        rt.DemoVenue(FakeClient(rest='https://fapi.binance.com', mode='mainnet'))
    with pytest.raises(PermissionError):
        rt.DemoVenue(FakeClient(rest='https://fapi.binance.com'))


async def test_demo_venue_maps_orders():
    c = FakeClient()
    v = rt.DemoVenue(c)
    await v.prepare('AAAUSDT', 3)
    assert [x[1] for x in c.calls] == ['/fapi/v1/marginType', '/fapi/v1/leverage']
    px, qty = await v.market('AAAUSDT', 'BUY', '6', 'cid')
    assert (px, qty) == (1.0005, 6.0)
    sent = c.calls[-1][2]
    assert sent['type'] == 'MARKET' and sent['newOrderRespType'] == 'RESULT' and 'reduceOnly' not in sent
    assert await v.take_profit('AAAUSDT', 'SELL', '3', '1.02', 't1') is False   # -5022 -> caller takes profit
    await v.stop('AAAUSDT', 'SELL', '0.9938', 's1')
    assert await v.realized('AAAUSDT', 0) == pytest.approx(0.087)


async def test_demo_venue_rejects_hedge_mode():
    c = FakeClient()

    async def dual(method, path, params=None, signed=False, key=False, weight=1):
        return {'dualSidePosition': True}
    c.request = dual
    c.sync_time = lambda: _noop()
    with pytest.raises(PermissionError):
        await rt.DemoVenue(c).start()


async def _noop():
    return None


def test_cli_has_no_mainnet_mode():
    with pytest.raises(SystemExit):
        rt.main(['--mode', 'mainnet'])


# ----------------------------------------------------------------------------- pullback retest (default)
def test_default_entry_is_market_and_retest_gates_trend():
    assert rt.TraderConfig().entry == 'market'      # retest lost more on 146 real signals
    assert rt.eligible(pick(trend_ok=False), RCFG)[0] is False
    assert 'TREND' in rt.eligible(pick(trend_ok=False), RCFG)[1]
    assert rt.eligible(pick(retest=None), RCFG) == (False, 'NO_RETEST_ZONE')
    assert rt.eligible(pick(retest=None), CFG)[0]                     # market mode does not need a zone


async def test_retest_rests_post_only_below_the_surge(tmp_path):
    tr, venue, q, _ = make(tmp_path, (0.9999, 1.0001), cfg=RCFG)
    await tr.boot()
    assert await tr.step() == 'ENTRY_PENDING AAAUSDT'
    pe = tr.pending
    assert pe['price'] == pytest.approx(0.992) and pe['pullback_pct'] == pytest.approx(0.8)
    assert [o['kind'] for o in venue.orders.values()] == ['ENTRY']      # no market order, no chase
    assert venue.amt.get('AAAUSDT', 0) == 0
    s = json.loads((tmp_path / 'state.json').read_text(encoding='utf-8'))
    assert s['positions'][0]['pending'] and 'ENTRY LIMIT GTX' in s['open_orders'][0]['kind']


async def test_retest_fill_protects_from_fill_with_deeper_stop_than_market(tmp_path):
    tr, venue, q, _ = make(tmp_path, (0.9999, 1.0001), cfg=RCFG)
    await tr.boot()
    await tr.step()
    q['AAAUSDT'] = (0.9915, 0.9917)                                  # pullback trades through the zone
    assert await tr.step() == 'IN_POSITION AAAUSDT'
    p = tr.pos
    assert p['entry'] == pytest.approx(0.992) and p['entry_mode'] == 'retest' and len(p['tps']) == 2
    assert {o['kind'] for o in venue.orders.values() if o['status'] == 'NEW'} == {'SL', 'TP'}
    m = rt.bracket('BUY', 1.0006, 6.0, SPEC, CFG)                     # same signal, market fill at the top
    assert p['sl'] < m['sl']                                          # stop sits below the support, not the spike
    q['AAAUSDT'] = (p['sl'], p['sl'] + 0.0002)                       # stop at price: loss still capped
    await tr.step()
    assert tr.trades[0]['reason'] == 'STOP' and -0.0502 <= tr.trades[0]['pnl'] < 0


async def test_retest_ttl_cancels_without_trade(tmp_path):
    clock = Clock()
    cfg = rt.TraderConfig(poll_s=0.0, scan_every_s=1e9, retest_ttl_s=240.0, entry='retest', **LEG)
    tr, venue, q, _ = make(tmp_path, (0.9999, 1.0001), cfg=cfg, clock=clock)
    await tr.boot()
    await tr.step()
    q['AAAUSDT'] = (1.01, 1.0102)                                    # price runs away, never retests
    clock.t += 120
    assert await tr.step() == 'ENTRY_PENDING AAAUSDT'
    clock.t += 121
    assert await tr.step() == 'SCANNING'
    assert tr.pending is None and tr.pos is None and venue.amt.get('AAAUSDT', 0) == 0
    assert tr.ledger.today() == (0.0, False, []) and 'AAAUSDT' in tr.cooldown


async def test_retest_already_in_zone_rests_at_touch(tmp_path):
    tr, venue, q, _ = make(tmp_path, (0.9900, 0.9902), cfg=RCFG)     # book already under the 0.992 zone
    await tr.boot()
    await tr.step()
    assert tr.pending['price'] == pytest.approx(0.99)                 # post-only at the bid, never crossing


class PartialVenue(rt.ShadowVenue):
    async def order(self, symbol, cid):
        o = self.orders.get(cid)
        if o and o['kind'] == 'ENTRY':
            if o['status'] == 'NEW':
                if not getattr(self, '_pf', False):
                    self._pf = True
                    self._fill(symbol, o['side'], 3.0, o['price'], self.cfg.maker_bps)
                return {'status': 'PARTIALLY_FILLED', 'filled': 3.0, 'avg': o['price']}
            return {'status': o['status'], 'filled': 3.0, 'avg': o['price']}
        return await super().order(symbol, cid)


async def test_partial_fill_cancels_rest_and_protects_filled_part(tmp_path):
    q = {'AAAUSDT': (0.9999, 1.0001)}
    venue = PartialVenue(RCFG, quote=lambda s: q[s], http=lambda url: EXINFO)
    tr = rt.AutoTrader(venue, RCFG, lambda: {'picks': [pick()]}, rt.Ledger(tmp_path / 'l.jsonl', 'shadow'),
                       state_file=tmp_path / 's.json', shutdown_file=tmp_path / 'x.json', clock=Clock())
    await tr.boot()
    await tr.step()
    await tr.step()
    entry = next(o for o in venue.orders.values() if o['kind'] == 'ENTRY')
    assert entry['status'] == 'CANCELED' and tr.pos['qty'] == 3.0
    assert any(o['kind'] == 'SL' and o['status'] == 'NEW' for o in venue.orders.values())


async def test_kill_while_resting_cancels_entry(tmp_path):
    tr, venue, q, _ = make(tmp_path, (0.9999, 1.0001), cfg=RCFG)
    await tr.boot()
    await tr.step()
    (tmp_path / 'stop.json').write_text('{}')
    assert await tr.step() == 'SHUTDOWN'
    assert tr.pending is None and tr.pos is None and not await venue.exposure()


# ----------------------------------------------------------------------------- v3: ATR stop + R:R ladder
def test_v3_config_defaults():
    c = rt.TraderConfig()
    assert (c.sl_mode, c.atr_sl_mult, c.max_loss, c.kill_loss, c.surge_min, c.taker_min) == \
        ('atr', 2.0, 0.15, 0.15, 5.0, 0.60)
    assert c.tp_pcts == (4.5, 8.0) and c.rr_min == 2.5 and c.max_hold_s == 12 * 3600


def test_v3_taker_and_risk_gates():
    assert rt.eligible(pick(cfg=V3, atr_pct=0.8, taker=0.55), V3)[1].startswith('TAKER')
    assert rt.eligible(pick(cfg=V3, atr_pct=3.0), V3) == (False, 'PLAN')      # 6 % stop costs > 0.15 U
    assert rt.eligible(pick(cfg=V3, atr_pct=0.8), V3) == (True, 'OK')


def test_v3_bracket_uses_atr_stop_and_refuses_over_cap():
    b = rt.bracket('BUY', 1.0, 6.0, SPEC, V3, sl_pct=1.6, tp_pcts=[4.5, 8.0])
    assert b['sl'] == pytest.approx(0.984) and b['tps'] == [(1.045, 3.0), (1.08, 3.0)]
    assert b['worst_loss'] <= 0.15
    with pytest.raises(ValueError):
        rt.bracket('BUY', 1.0, 6.0, SPEC, V3, sl_pct=3.0, tp_pcts=[7.5, 13.3])


async def test_v3_full_cycle_atr_stop_tp1_breakeven(tmp_path):
    tr, venue, q, _ = make(tmp_path, (0.9999, 1.0001), picks=[pick(cfg=V3, atr_pct=0.8)], cfg=V3)
    await tr.boot()
    assert await tr.step() == 'IN_POSITION AAAUSDT'
    p = tr.pos
    assert p['sl_pct'] == pytest.approx(1.6) and p['sl'] < p['entry'] * 0.985
    assert [round(t['price'], 4) for t in p['tps']] == [round(p['entry'] * 1.045, 4), round(p['entry'] * 1.08, 4)]
    q['AAAUSDT'] = (0.99, 0.9902)                        # -1 %: inside the old 0.8 % stop, outside the ATR stop
    await tr.step()
    assert tr.pos is not None                            # survives the wick that killed v1
    q['AAAUSDT'] = (1.047, 1.0472)                       # TP1
    await tr.step()
    assert tr.pos['be'] and tr.pos['tps'][0]['filled']
    q['AAAUSDT'] = (1.0, 1.0002)                         # back to entry -> breakeven exit
    await tr.step()
    assert tr.pos is None and tr.trades[0]['reason'] == 'TP1+BE' and tr.trades[0]['pnl'] > 0.1


async def test_v3_stop_loss_stays_inside_015(tmp_path):
    tr, venue, q, _ = make(tmp_path, (0.9999, 1.0001), picks=[pick(cfg=V3, atr_pct=0.8)], cfg=V3)
    await tr.boot()
    await tr.step()
    sl = tr.pos['sl']
    q['AAAUSDT'] = (sl, sl + 0.0002)
    await tr.step()
    t = tr.trades[0]
    assert t['reason'] == 'STOP' and -0.15 <= t['pnl'] < -0.08
