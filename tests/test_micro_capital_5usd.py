"""11-09 micro-capital mode for a ~5 USDT wallet (docs/harness/plan_micro_capital.md): entry notional window
[5.05, 6.0] U (Binance -4164 minimum 5 U), loss at the stop <= 0.05 U, BTC / SOL observer-only, one position, daily kill
0.15 U, 24 h halt after 3 AUTO losses.  Synthetic data on the mock venue; nothing here talks to an exchange and none of
it is evidence that a 5 U account makes money."""
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from config.settings import Settings, micro_capital_env
from core import engine as eng
from core import invariants as inv
from core import micro_signals as msig
from core import micro_swarm as ms
from core.position_sizer import MicroCapitalSizer
from tests.test_two_mode_engine import depth, mock_engine, sig

DOGE, SOL = 'DOGEUSDT', 'SOLUSDT'
BUY = ms.evaluate(sig(FLOW=('BUY', 1.0, 20), VWAP=('BUY', 1.0, 20)), ms.Gates(cost_bps=6.0))[0]
SPEC = eng.FALLBACK_SPECS
LO, HI = 5.05, 6.0
FEES = 0.08                                    # maker in 2 + taker stop 5 + slip 1 bps, in percent


def lot(sym, px, **kw):
    s = SPEC[sym]
    return MicroCapitalSizer(**kw).qty(px, s['step'], s['min_qty'], s['min_notional'])


# ============================================================================= sizer: lot / notional arithmetic
@pytest.mark.parametrize('px', [0.05, 0.0711, 0.0888, 0.10, 0.17, 0.20, 0.31])
def test_doge_notional_is_inside_the_window_and_the_smallest_lot_that_clears_it(px):
    q, why = lot(DOGE, px)
    n = q * px
    assert why == '' and LO <= n <= HI and q == int(q)                    # DOGE lot step 1
    assert n >= 5.0 * 1.02 - 1e-9                                         # the engine's own 2 % venue buffer
    assert (q - 1) * px < max(LO, 5.0 * 1.02)                             # one lot less would be under the floor


def test_doge_sizing_follows_the_price_not_a_fixed_25_to_30_lots():
    """At 0.0711 the directive's 25-30 DOGE is 1.78-2.13 U: rejected by the venue (-4164).  72 lots are needed."""
    q, _ = lot(DOGE, 0.0711)
    assert q == 72 and 72 * 0.0711 >= LO
    assert 30 * 0.0711 < 5.0 and 25 * 0.0711 < 5.0
    assert lot(DOGE, 0.17)[0] == 30                                       # 25-30 lots only fit at ~0.17-0.20


def test_sol_has_exactly_one_lot_in_the_window_on_the_engine_spec():
    q, why = lot(SOL, 86.0)
    assert (q, why) == (0.06, '') and LO <= q * 86.0 <= HI                # 5.16 U
    assert (0.07 * 86.0) > HI                                             # the next lot is already over the cap


def test_a_coarse_lot_is_refused_with_the_reason_not_rounded_into_a_rejected_order():
    s = MicroCapitalSizer()
    q, why = s.qty(86.0, 0.1, 0.1, 5.0)                                   # SOL with a 0.1 lot: 8.6 U
    assert q is None and 'smallest lot 0.1 = 8.60 U' in why and 'too coarse' in why


def test_btc_can_never_trade_on_a_small_wallet():
    q, why = lot('BTCUSDT', 74_000.0)
    assert q is None and 'venue minimum notional 100 U' in why            # 102 U needed > the 6 U cap
    assert SPEC['BTCUSDT']['min_notional'] / 5.0 > 5.0                    # 20 U of margin at 5x on a 5 U wallet


def test_sizer_refuses_bad_inputs_and_bad_parameters():
    s = MicroCapitalSizer()
    for px in (0.0, -1.0, float('nan'), float('inf')):
        assert s.qty(px, 1.0, 1.0, 5.0)[0] is None
    assert s.qty(0.07, 0.0, 1.0, 5.0)[0] is None
    for bad in ({'notional_min': 7.0}, {'notional_max': 0}, {'max_positions': 0}, {'symbols': ()},
                {'equity_max': float('nan')}, {'max_consec_losses': 0}):
        with pytest.raises(ValueError):
            MicroCapitalSizer(**bad)


@pytest.mark.parametrize('sl', [0.15, 0.20])
def test_loss_at_the_stop_stays_under_0_05_across_the_whole_window(sl):
    s = MicroCapitalSizer()
    for n in (LO, 5.16, 5.5, HI):
        loss = s.loss_at_stop(n, sl, FEES)
        assert loss <= 0.05 and loss == pytest.approx(n * (sl + FEES) / 100)
    assert s.loss_at_stop(HI, 0.20, FEES) == pytest.approx(0.0168)
    assert 0.05 / ((0.20 + FEES) / 100) == pytest.approx(17.857, abs=1e-3)   # the cap only binds above ~17.9 U


def test_active_rule_is_inclusive_at_15_and_unknown_is_not_small():
    s = MicroCapitalSizer()
    assert s.active(5.0) and s.active(15.0) and not s.active(15.01) and not s.active(5000.0)
    for unknown in (None, 0.0, -1.0, float('nan')):
        assert not s.active(unknown)
    assert s.active(5000.0, 'on') and not s.active(5.0, 'off')
    with pytest.raises(ValueError):
        s.active(5.0, 'maybe')


# ============================================================================= sizer: 24 h halt
def loss_row(t, tag='AUTO', pnl=-0.01):
    return {'t': t, 'tag': tag, 'pnl': pnl, 'symbol': DOGE}


def test_three_auto_losses_halt_for_24h_from_the_last_loss():
    s, now = MicroCapitalSizer(), 1_000_000.0
    rows = [loss_row(now - 300), loss_row(now - 200), loss_row(now - 100)]
    assert s.halt_until(rows, now) == pytest.approx(now - 100 + 86_400)
    assert s.halt_until(rows, now + 86_400) is None                       # expired exactly after 24 h from the last loss
    assert s.halt_until(rows[:2], now) is None                            # two losses are not enough
    assert s.halt_until(rows + [loss_row(now - 50, pnl=0.02)], now) is None   # a winner resets the streak
    assert s.halt_until(rows + [loss_row(now - 50, tag='TEST')], now) == pytest.approx(now - 100 + 86_400)
    assert s.halt_until([loss_row(now - 9, tag='TEST', pnl=-1.0)] * 5, now) is None   # only AUTO losses count


def test_halt_ignores_rows_it_cannot_date_and_a_fourth_loss_extends_it():
    s, now = MicroCapitalSizer(), 1_000_000.0
    rows = [loss_row(now - 400), loss_row(now - 300), {'tag': 'AUTO', 'pnl': -0.01}, loss_row(now - 200)]
    assert s.halt_until(rows, now) == pytest.approx(now - 200 + 86_400)   # the undated row is UNKNOWN, not a loss
    rows.append(loss_row(now - 10))
    assert s.halt_until(rows, now) == pytest.approx(now - 10 + 86_400)


# ============================================================================= engine: mode follows the wallet
def micro_engine(tmp_path, sym=DOGE, wallet=5.0, **prof):
    prof.setdefault('micro_align', False)          # the KER / alignment gate and the noise-floor bracket have their own tests (tests/test_micro_noise.py)
    e, venue, feed = mock_engine(tmp_path, mock_balance=wallet, **prof)
    e.p = replace(e.p, symbol=sym, qty=0.0)
    e.spec = dict(SPEC[sym])
    e.account = {'wallet': wallet, 'available': wallet}
    return e, venue, feed


@pytest.mark.parametrize('wallet, mode, on', [(5.0, 'auto', True), (15.0, 'auto', True), (15.01, 'auto', False),
                                              (5000.0, 'auto', False), (5000.0, 'on', True), (5.0, 'off', False)])
def test_engine_mode_follows_the_wallet(tmp_path, wallet, mode, on):
    e, _, _ = micro_engine(tmp_path, wallet=wallet, micro_capital=mode)
    assert e.micro_active() is on


def test_engine_with_an_unknown_wallet_keeps_the_normal_rules(tmp_path):
    e, _, _ = micro_engine(tmp_path)
    e.account = {}
    assert not e.micro_active() and e.max_positions_eff() == e.p.max_positions


def test_btc_and_sol_observe_and_doge_trades_on_a_small_wallet(tmp_path):
    for sym, observer in (('BTCUSDT', True), (SOL, True), (DOGE, False)):
        e, _, _ = micro_engine(tmp_path, sym=sym, wallet=5.0)
        assert e.observer is observer, sym
    big, _, _ = micro_engine(tmp_path, sym=SOL, wallet=5000.0)
    assert not big.observer                                               # untouched on the 5 000 U demo account
    off, _, _ = micro_engine(tmp_path, sym=SOL, wallet=5.0, micro_capital='off')
    assert not off.observer


async def test_an_observer_symbol_sends_no_order(tmp_path):
    e, venue, feed = micro_engine(tmp_path, sym=SOL)
    await e.sync()
    r = await e.execute('BUY', BUY, 'SWARM')
    assert not r['ok'] and r['error'].startswith('OBSERVER') and not venue.fills and e.pos is None


def test_shipped_profiles_keep_every_invariant_and_live_is_micro_by_default():
    for name, p in eng.PROFILES.items():
        assert inv.check_profile(p) == [], name
        assert (p.micro_capital, p.micro_equity_max, p.micro_symbols) == ('auto', 15.0, eng.MICRO_UNIVERSE), name
    live = eng.PROFILES['live']
    assert live.symbol in live.micro_symbols and live.mock_balance <= live.micro_equity_max
    assert live.equity_cap == 3.85                                        # I6 caps one position's margin; not touched


# ============================================================================= engine: the funnel
def book(feed, bid, sym=DOGE):
    for _ in range(3):
        feed.on_message(depth(bid, round(bid + 0.00001, 5), bq=50_000.0, aq=50_000.0, sym=sym))


async def doge_funnel(tmp_path, px, wallet=5.0, **prof):
    prof.setdefault('micro_align', False)          # the KER / alignment gate and the noise-floor bracket have their own tests (tests/test_micro_noise.py)
    feed = msig.MicroFeed(DOGE, msig.config_for(DOGE))
    book(feed, px)
    e, venue, _ = mock_engine(tmp_path, feed=feed, mock_balance=wallet, gtx_ttl_s=0.2, **prof)
    e.p = replace(e.p, symbol=DOGE, qty=0.0)
    e.spec = dict(SPEC[DOGE])
    await e.sync()
    return e, venue, feed


def order_rows(tmp_path):
    f = tmp_path / 'engine_orders.jsonl'
    return [json.loads(x) for x in f.read_text(encoding='utf-8').splitlines()] if f.exists() else []


@pytest.mark.parametrize('px', [0.0711, 0.0888, 0.17])
async def test_doge_entry_is_sent_inside_the_notional_window(tmp_path, px):
    e, venue, feed = await doge_funnel(tmp_path, px)
    assert e.micro_active()
    res = await e.execute('BUY', BUY, 'SWARM')
    sent = [r for r in order_rows(tmp_path) if r.get('qty_req')]
    assert sent, (res, [ev['message'] for ev in e.events])
    q = sent[-1]['qty_req']
    assert LO <= q * px <= HI and q == int(q)
    assert q == lot(DOGE, px)[0]                                          # the smallest lot, not Kelly / conviction size


async def test_micro_overrides_a_big_profile_size(tmp_path):
    e, venue, feed = await doge_funnel(tmp_path, 0.0888)
    e.p = replace(e.p, qty=500.0)                                         # a 44 U order if the normal chain ran
    await e.execute('BUY', BUY, 'SWARM')
    rows = [r for r in order_rows(tmp_path) if r.get('qty_req')]
    assert rows and rows[-1]['qty_req'] == 58 and LO <= rows[-1]['qty_req'] * 0.0888 <= HI


async def test_a_coarse_lot_symbol_is_refused_by_the_funnel(tmp_path):
    feed = msig.MicroFeed(SOL, msig.config_for(SOL))
    for _ in range(3):
        feed.on_message(depth(86.0, 86.01, bq=900.0, aq=900.0, sym=SOL))
    e, venue, _ = mock_engine(tmp_path, feed=feed, mock_balance=5.0, micro_symbols=(SOL,), micro_capital='on',
                              micro_align=False)
    e.p = replace(e.p, symbol=SOL, qty=0.0, observe_only=())
    e.spec = {**SPEC[SOL], 'step': 0.1, 'min_qty': 0.1}                   # a venue whose SOL lot is 0.1
    await e.sync()
    r = await e.execute('BUY', BUY, 'SWARM')
    assert not r['ok'] and r['error'].startswith('MICRO: smallest lot 0.1') and not venue.fills


def plan_dict(notional, loss, sl=0.2):
    return {'notional': notional, 'loss_at_sl': loss, 'sl_pct': sl, 'fees_pct': FEES}


def test_micro_check_enforces_the_window_and_the_loss_budget(tmp_path):
    e, _, _ = micro_engine(tmp_path)
    assert e.micro_check(plan_dict(5.15, 0.0144)) is None
    assert 'outside [5.05, 6] U' in e.micro_check(plan_dict(6.2, 0.0174))
    assert 'outside' in e.micro_check(plan_dict(4.9, 0.0137))
    why = e.micro_check(plan_dict(5.5, 0.0501))
    assert why.startswith('MICRO: loss at the stop 0.0501 U > 0.05 U')


# ============================================================================= engine: one position, kill, halt
def test_one_position_at_a_time_on_a_small_wallet_three_on_a_big_one(tmp_path):
    peer = SimpleNamespace(pos={'x': 1}, busy=False, view={}, risk_at_stop=lambda: 0.0, margin_used=lambda: 0.0,
                           p=SimpleNamespace(symbol=SOL))
    small, _, _ = micro_engine(tmp_path, wallet=5.0)
    small.risk.engines = [small, peer]
    assert small.max_positions_eff() == 1 and small.slot_block() == 'PORTFOLIO FULL 1/1'
    big, _, _ = micro_engine(tmp_path / 'b', wallet=5000.0)
    big.risk.engines = [big, peer]
    assert big.max_positions_eff() == 3 and big.slot_block() is None


def test_daily_kill_is_0_15_on_a_small_wallet_only(tmp_path):
    f = {}
    small, _, _ = micro_engine(tmp_path, wallet=5.0)
    assert small.daily_kill_eff() == 0.15 and small.p.daily_kill_usdt == 5.0
    small.day_pnl = -0.16
    ok, why = small.gates(f).rune
    assert not ok and 'daily loss' in why and '0.15' in why
    big, _, _ = micro_engine(tmp_path / 'b', wallet=5000.0)
    big.day_pnl = -0.16
    assert big.daily_kill_eff() == 5.0 and big.gates(f).rune[0]


def write_ledger(tmp_path, rows):
    (tmp_path / 'trades.jsonl').write_text('\n'.join(json.dumps(r) for r in rows) + '\n', encoding='utf-8')


def ledger_row(t, pnl, tag='AUTO'):
    return {'t': t, 'mode': 'demo', 'symbol': DOGE, 'tag': tag, 'pnl': pnl, 'day': '1999-01-01', 'side': 'BUY', 'micro': True}


def test_the_24h_halt_survives_a_restart_and_clears_after_24h_or_a_win(tmp_path):
    now = time.time()
    write_ledger(tmp_path, [ledger_row(now - 900, -0.01), ledger_row(now - 600, -0.01), ledger_row(now - 300, -0.01)])
    e, _, _ = micro_engine(tmp_path)                                      # a brand-new engine reading the ledger
    assert e.consec_losses == 0 and e.pause_until == 0.0                  # the in-memory counters did not survive ...
    until = e.micro_halt_until()
    assert until == pytest.approx(now - 300 + 86_400, abs=2)              # ... the ledger-derived halt did
    ok, why = e.gates({}).rune
    assert not ok and why.startswith('MICRO HALT: 3 losses in a row') and 'left' in why
    e.all_rows.append(ledger_row(now - 5, 0.02))
    assert e.micro_halt_until() is None                                   # a winner ends the streak
    off, _, _ = micro_engine(tmp_path / 'o', micro_capital='off')
    off.all_rows.extend([ledger_row(now - 30, -0.01)] * 3)
    assert off.micro_halt_until() is None                                 # not a small-account rule when it is off


# ============================================================================= settings flag
def test_settings_flag_parses_validates_and_reaches_the_profile():
    assert micro_capital_env({}) == {}
    assert micro_capital_env({'MICRO_CAPITAL_MODE': ' ON ', 'MICRO_CAPITAL_EQUITY_MAX': '12.5'}) == \
        {'micro_capital': 'on', 'micro_equity_max': 12.5}
    for bad in ({'MICRO_CAPITAL_MODE': 'yes'}, {'MICRO_CAPITAL_EQUITY_MAX': 'abc'}, {'MICRO_CAPITAL_EQUITY_MAX': '0'},
                {'MICRO_CAPITAL_EQUITY_MAX': '5000'}):
        with pytest.raises(ValueError):
            micro_capital_env(bad)
    s = Settings.load('testnet', env={'MICRO_CAPITAL_MODE': 'off', 'MICRO_CAPITAL_EQUITY_MAX': '20'})
    assert (s.micro_capital_mode, s.micro_capital_equity_max) == ('off', 20.0)
    d = Settings.load('testnet', env={})
    assert (d.micro_capital_mode, d.micro_capital_equity_max) == ('auto', 15.0)
    r = eng.Runner('demo', False, env={'MICRO_CAPITAL_MODE': 'off', 'MICRO_CAPITAL_EQUITY_MAX': '9'})
    p = r.profile('demo')
    assert (p.micro_capital, p.micro_equity_max) == ('off', 9.0)
    assert eng.Runner('demo', False, env={}).profile('demo').micro_capital == 'auto'


# ============================================================================= the "recent SOL short" arithmetic
def test_the_reported_sol_short_was_demo_sized_and_the_new_guards_do_not_touch_it(tmp_path):
    """+0.41 % ROE at 5x is a 0.082 % price move; +0.6321 U on it is ~9 SOL = ~771 U of notional: the 5 000 U demo
    account.  At +0.082 % the soft BE (needs MFE >= 0.12 %) is not armed and the EJECT hold (only below the 0.04 %
    fee hurdle) does not hold, so neither guard changes that trade.  Arithmetic only, not a replay."""
    move = 0.41 / 5
    qty = 0.6321 / (85.98 * move / 100)
    assert move == pytest.approx(0.082) and qty == pytest.approx(8.97, abs=0.05) and qty * 85.98 > 700
    e, _, _ = micro_engine(tmp_path, sym=SOL, wallet=5000.0)
    p = {'side': 'SELL', 'entry': 85.98, 'entry_fee_bps': 2.0, 'mfe_pct': move, 'sl': 86.3, 'opened': e.clock()}
    assert e.be_arm_pct(p) == pytest.approx(0.12, abs=0.01) and not e.fee_hurdle_covered(p)
    assert e.eject_escalate_ok(p, {}, move, 360.0) is True                # gross above the 0.04 % hurdle: nothing held
    small, _, _ = micro_engine(tmp_path / 's', sym=SOL, wallet=5.0)
    pl = {'notional': qty * 85.98, 'loss_at_sl': 0.1, 'sl_pct': 0.2, 'fees_pct': FEES}
    assert 'outside' in small.micro_check(pl)                             # and it would not fit a 5 U account


# ============================================================================= the margin guard (found by the funnel test)
@pytest.mark.parametrize('wallet', [3.85, 5.0, 10.0])
def test_one_micro_lot_passes_the_portfolio_margin_guard(tmp_path, wallet):
    """20 % of a 5 U wallet is 1.0 U < the 1.02 U margin (5.10 U / 5x) of the smallest lot: every micro entry, and every
    entry of the shipped live profile (0.77 U on 3.85 U), used to be refused by RUNE before it reached the venue."""
    e, _, _ = micro_engine(tmp_path, wallet=wallet)
    assert e.portfolio_check({'loss_at_sl': 0.014, 'margin': 1.02}) is None
    assert e.portfolio_check({'loss_at_sl': 0.017, 'margin': 1.2}) is None          # the largest lot of the window
    why = e.portfolio_check({'loss_at_sl': 0.017, 'margin': 1.21})
    if 0.2 * wallet < 1.2:                                                           # the lot window sets the cap
        assert why and why.startswith('PORTFOLIO MARGIN 1.21 U > 1.20 U')
    else:                                                                            # 20 % of the wallet is already more
        assert why is None


def test_the_margin_guard_is_unchanged_outside_micro_mode(tmp_path):
    big, _, _ = micro_engine(tmp_path, wallet=5000.0)
    assert big.portfolio_check({'loss_at_sl': 0.1, 'margin': 999.0}) is None
    assert big.portfolio_check({'loss_at_sl': 0.1, 'margin': 1001.0}).endswith('20% ví')
    off, _, _ = micro_engine(tmp_path / 'o', wallet=5.0, micro_capital='off')
    assert off.portfolio_check({'loss_at_sl': 0.014, 'margin': 1.02}).endswith('20% ví')   # the old behaviour, by choice
