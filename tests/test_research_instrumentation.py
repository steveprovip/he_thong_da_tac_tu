"""Research instrumentation + sizing safety (2026-10-07q).

Accounting: UNKNOWN gross/fee never summed as 0. Execution: one engine_orders.jsonl row per entry attempt
(FILLED / PARTIAL / UNFILLED / REJECTED / TOXIC / REFUSED) with send->ack and ack->fill split by perf_counter.
Ledger schema 2: policy id, code commit, signal-vs-venue basis, post-fill markouts. Dashboard KILL records the
closed trade. Sizing: no size lift from the uncalibrated swarm score and no Kelly scaling below 20 trades.
These tests prove software behaviour only; they are not evidence of profitability."""
import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from core import engine as eng
from tests.test_two_mode_engine import book_at, mock_engine, opened

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import ledger_audit as la  # noqa: E402

LEDGER = ROOT / 'data' / 'engine_trades.jsonl'


def orders(tmp_path):
    p = tmp_path / 'engine_orders.jsonl'
    return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines()] if p.exists() else []


# ============================================================================= accounting: UNKNOWN != 0
def test_session_and_stats_skip_rows_without_gross_fee(tmp_path):
    e, _, _ = mock_engine(tmp_path)
    test_row = {'symbol': 'BTCUSDT', 'pnl': -0.0697, 'tag': 'TEST'}                 # ledger #1: no gross / fee
    auto_row = {'symbol': 'BTCUSDT', 'pnl': -0.0754, 'gross': 0.027, 'fee': 0.1024, 'tag': 'AUTO'}
    e.history.extend([test_row, auto_row])
    e.all_rows[:] = [test_row, auto_row]
    s = e.snapshot()['session']
    assert s['gross'] == pytest.approx(0.027) and s['fees'] == pytest.approx(0.1024) and s['rows_unknown_cost'] == 1
    st = e.stats()
    assert st['rows_unknown_cost'] == 1 and st['gross_known'] == pytest.approx(0.027)
    assert st['net'] == pytest.approx(-0.1451) and st['fees'] == pytest.approx(0.1024)


def test_ledger_audit_on_synthetic_rows():
    rows = [{'t': 0, 'pnl': -0.07, 'tag': 'TEST', 'symbol': 'BTCUSDT'},
            {'t': 10, 'pnl': 0.1, 'gross': 0.14, 'fee': 0.04, 'tag': 'AUTO', 'symbol': 'BTCUSDT', 'entry_type': 'GTX',
             'maker_fills': 2, 'taker_fills': 0},
            {'t': 20, 'pnl': -0.2, 'gross': -0.15, 'fee': 0.05, 'tag': 'AUTO', 'symbol': 'SOLUSDT', 'entry_type': 'IOC',
             'maker_fills': 1, 'taker_fills': 1},
            {'t': 5000, 'pnl': -0.03, 'gross': 0.01, 'fee': 0.04, 'tag': 'AUTO', 'symbol': 'SOLUSDT',
             'entry_type': 'GTX', 'maker_fills': 2, 'taker_fills': 0}]
    r = la.audit(rows)
    a = r['all']
    assert a['rows_unknown_gross_fee'] == 1 and a['gross_known'] == pytest.approx(0.0) and a['fee_known'] == 0.13
    assert a['net'] == pytest.approx(-0.2) and a['gross_positive_net_negative'] == 1
    assert a['max_drawdown'] == pytest.approx(-0.23) and a['taker_share'] == pytest.approx(1 / 6, abs=1e-4)
    assert r['unknown_fields']['entry_type'] == [1] and r['reconcile_gross_minus_fee_ne_net'] == []
    assert r['by_entry_type']['UNKNOWN']['n'] == 1 and len(r['sessions']) == 2
    assert r['auto_only']['n'] == 3


def test_block_bootstrap_interval_brackets_the_mean():
    xs = [0.1, -0.05, 0.02, -0.08, 0.04, -0.01, 0.03, -0.02, 0.05]
    lo, hi = la.block_bootstrap_mean(xs)
    assert lo < sum(xs) / len(xs) < hi
    assert la.block_bootstrap_mean([0.1]) == (None, None)


def test_ledger_audit_reproduces_the_34_row_baseline():
    if not LEDGER.exists() or len(LEDGER.read_text(encoding='utf-8').splitlines()) < 34:
        pytest.skip('needs the 34-trade ledger')
    rows = la.load(LEDGER)[:34]
    r = la.audit(rows)
    a = r['all']
    assert a['n'] == 34 and a['wins'] == 9 and a['net'] == pytest.approx(-1.22563407, abs=1e-8)
    assert a['gross_known'] == pytest.approx(0.95817999, abs=1e-8) and a['fee_known'] == pytest.approx(2.1141432)
    assert a['profit_factor'] == pytest.approx(0.511, abs=1e-3) and r['unknown_fields']['gross'] == [1]
    assert r['by_entry_type']['GTX']['net'] == pytest.approx(-1.42585414, abs=1e-8)
    assert r['by_entry_type']['IOC']['net'] == pytest.approx(0.26989093, abs=1e-8)
    assert r['by_symbol']['SOLUSDT']['net'] == pytest.approx(0.22750818, abs=1e-8)
    assert r['reconcile_gross_minus_fee_ne_net'] == [] and a['gross_positive_net_negative'] == 8


# ============================================================================= order outcome log
async def test_filled_entry_writes_an_order_row_with_stage_latency(tmp_path):
    e, venue, feed = await opened(tmp_path)
    rows = orders(tmp_path)
    assert len(rows) == 1
    o = rows[0]
    assert o['outcome'] == 'FILLED' and o['route'] == 'gtx' and o['qty_filled'] == pytest.approx(o['qty_req'])
    assert o['ack_ms'] is not None and o['ack_ms'] >= 0 and o['fill_wait_ms'] is not None and o['fill_wait_ms'] >= 0
    assert o['total_ms'] >= o['fill_wait_ms'] and o['decide_to_send_ms'] >= 0
    assert len(o['policy']) == 10 and o['basis_bps'] == pytest.approx(0.0)        # mock: one book for both
    assert e.pos['ack_ms'] == o['ack_ms'] and e.pos['basis_bps'] == pytest.approx(0.0)


async def test_unfilled_entry_is_logged_not_lost(tmp_path):
    e, venue, feed = mock_engine(tmp_path)
    await e.sync()
    d = await e.tick()                                                              # book never trades through
    assert d.allowed and e.pos is None
    o = orders(tmp_path)[-1]
    assert o['outcome'] == 'UNFILLED' and o['fill_wait_ms'] is None and o['qty_filled'] is None
    assert o['total_ms'] >= 0.5 * 1000 * e.p.gtx_ttl_s


async def test_post_only_rejects_are_logged(tmp_path):
    e, venue, feed = mock_engine(tmp_path)
    await e.sync()

    async def always(*a):
        raise eng.PostOnlyReject('-5022')
    venue.limit_gtx = always
    await e.tick()
    o = orders(tmp_path)[-1]
    assert o['outcome'] == 'REJECTED' and o['post_rejects'] == 3 and o['ack_ms'] is None


async def test_sizing_refusal_is_logged_as_refused(tmp_path):
    e, venue, feed = mock_engine(tmp_path, alt_boost=True)                         # BTC probe below $100 min
    e.vfr_view = lambda f=None: {'vfr': 4.0, 'frozen': False, 'why': ''}
    await e.sync()
    await e.tick()
    o = orders(tmp_path)[-1]
    assert o['outcome'] == 'REFUSED' and 'dưới sàn' in o['error'] and o['decide_to_send_ms'] is None


async def test_busy_attempts_write_nothing(tmp_path):
    e, venue, feed = await opened(tmp_path)
    n = len(orders(tmp_path))
    res = await e.execute('BUY', e.decision, 'SWARM')
    assert not res['ok'] and len(orders(tmp_path)) == n


async def test_order_log_can_be_switched_off(tmp_path):
    e, venue, feed = await opened(tmp_path, order_log=False)
    assert orders(tmp_path) == []


# ============================================================================= ledger schema 2
async def test_ledger_row_carries_policy_basis_and_markouts(tmp_path):
    e, venue, feed = await opened(tmp_path)
    entry = e.pos['entry']
    book_at(feed, round(entry * 1.0005, 1))                                        # +5 bps with the long
    e.pos['opened'] -= 31
    await e.tick()
    mk = e.pos['markout_bps']
    assert set(mk) == {'1s', '5s', '30s'} and mk['1s'][0] == pytest.approx(5.0, abs=0.2) and mk['30s'][1] >= 30
    await e.close_now()
    row = json.loads((tmp_path / 'trades.jsonl').read_text(encoding='utf-8').splitlines()[-1])
    assert row['schema'] == 2 and len(row['policy']) == 10 and row['code'] == eng.CODE_VERSION
    assert row['markout_bps']['5s'][0] == pytest.approx(5.0, abs=0.2) and row['basis_bps'] == pytest.approx(0.0)
    assert 'basis_exit_bps' in row and row['ack_ms'] is not None and row['fill_wait_ms'] is not None


def test_basis_sign_and_unknown():
    assert eng.basis_bps(115.755, 116.05) == pytest.approx(25.485, abs=0.01)     # 14:29 UTC telemetry snapshot
    assert eng.basis_bps(None, 116.0) is None and eng.basis_bps(116.0, None) is None


def test_policy_id_is_stable_and_config_sensitive():
    p = eng.PROFILES['demo']
    assert eng.policy_id(p) == eng.policy_id(replace(p)) and len(eng.policy_id(p)) == 10
    assert eng.policy_id(p) != eng.policy_id(replace(p, min_score=0.83))


def test_code_version_reads_git_head(tmp_path):
    g = tmp_path / '.git'
    (g / 'refs' / 'heads').mkdir(parents=True)
    (g / 'HEAD').write_text('ref: refs/heads/main\n')
    (g / 'refs' / 'heads' / 'main').write_text('46dcdf9abcdef0123\n')
    assert eng.code_version(tmp_path) == '46dcdf9abc'
    (g / 'refs' / 'heads' / 'main').unlink()
    (g / 'packed-refs').write_text('# pack\n1234567890abcdef refs/heads/main\n')
    assert eng.code_version(tmp_path) == '1234567890'
    (g / 'HEAD').write_text('fedcba9876543210\n')
    assert eng.code_version(tmp_path) == 'fedcba9876'
    assert eng.code_version(tmp_path / 'nope') is None


# ============================================================================= dashboard KILL is recorded
async def test_kill_switch_closes_through_marin_and_writes_the_ledger(tmp_path):
    e, venue, feed = await opened(tmp_path)
    await eng.kill_switch([e])
    assert e.pos is None and not venue.amt.get(e.p.symbol)
    row = json.loads((tmp_path / 'trades.jsonl').read_text(encoding='utf-8').splitlines()[-1])
    assert row['reason'] == 'KILL' and row['schema'] == 2


async def test_kill_switch_flattens_untracked_positions(tmp_path):
    e, venue, feed = mock_engine(tmp_path)
    calls = []

    async def flat(sym):
        calls.append(sym)
    venue.flatten = flat
    await eng.kill_switch([e])
    assert calls == [e.p.symbol]


# ============================================================================= sizing safety defaults
def test_size_lifts_off_by_default_and_kelly_needs_20():
    for name in ('demo', 'live'):
        p = eng.PROFILES[name]
        assert not p.convex_sizing and not p.alt_size_boost and p.alt_kelly_min_n == 20 and p.order_log
    d = eng.PROFILES['demo']
    assert d.alt_boost and d.alt_risk_share == 0.85                     # BTC risk share / probe rule kept


def test_no_size_lift_from_a_90pct_vote_by_default(tmp_path):
    e, _, _ = mock_engine(tmp_path, alt_boost=True)
    e.p = replace(e.p, symbol='SOLUSDT')
    from tests.test_world_quant_economic_engine import BUY_DEC
    assert not e.alt_boosted(BUY_DEC, {'consensus': 1.0})
    e.p = replace(e.p, alt_size_boost=True)
    assert e.alt_boosted(BUY_DEC, {'consensus': 1.0})


def test_no_edge_kelly_falls_back_to_the_minimum_size(tmp_path):
    """OBSERVED (traced for the handoff): with >= 20 trades and f* <= 0, kelly_qty() returns None and execute()
    keeps the profile / exchange-minimum size. There is no 'NO EDGE -> no entry' gate yet."""
    e, _, _ = mock_engine(tmp_path)
    e.all_rows[:] = [{'symbol': 'BTCUSDT', 'pnl': -0.1}] * 15 + [{'symbol': 'BTCUSDT', 'pnl': 0.05}] * 5
    e.account = {'wallet': 5000.0}
    k = e.kelly()
    assert k['state'] in ('NO EDGE',) or (k['f'] is not None and k['f'] <= 0)
    assert e.kelly_qty(99_800.0, e.p) is None
