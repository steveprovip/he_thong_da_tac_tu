"""2026-11-07: tools/nightly_audit.py measures slippage and regime accuracy, reports UNKNOWN, and only writes reports."""
import gzip
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

import tools.nightly_audit as na
from core.regime_classifier import CALM_TREND, CRASH, JUMPY, SIDEWAYS, UNKNOWN, RegimeConfig

NOW = datetime(2026, 11, 7, 8, 0, tzinfo=timezone.utc)


def test_window_is_the_24h_ending_at_the_last_utc_midnight():
    start, end = na.window_for(None, NOW)
    assert (na.utc(start), na.utc(end)) == ('2026-11-06 00:00', '2026-11-07 00:00')
    start, end = na.window_for('2026-10-10')
    assert end - start == 86400 and na.utc(start) == '2026-10-09 00:00'


def row(t, side='BUY', entry=100.0, exit_=100.5, vmid=99.99, ref=100.52, fee=4.0, **kw):
    return dict(t=t, side=side, entry=entry, exit=exit_, venue_mid=vmid, exit_ref=ref, fee_bps=fee,
                cost_complete=True, mode='demo', venue='BINANCE DEMO', entry_type='GTX', exit_mode='maker', **kw)


def test_trade_costs_signs_and_unknown_components():
    c = na.trade_costs(row(0))
    assert c['entry_slip_bps'] == pytest.approx((100.0 - 99.99) / 99.99 * 1e4)
    assert c['exit_slip_bps'] == pytest.approx((100.52 - 100.5) / 100.52 * 1e4)
    assert c['all_in_bps'] == pytest.approx(4.0 + c['entry_slip_bps'] + c['exit_slip_bps'])
    short = na.trade_costs(row(0, side='SELL', entry=100.0, vmid=100.01, exit_=99.5, ref=99.48))
    assert short['entry_slip_bps'] > 0 and short['exit_slip_bps'] > 0      # sold below mid, bought back above ref
    assert na.trade_costs(row(0, vmid=None))['all_in_bps'] is None
    assert na.trade_costs(row(0, ref=None))['exit_slip_bps'] is None
    assert na.trade_costs(dict(row(0), cost_complete=False))['fee_bps'] is None
    assert na.trade_costs(dict(row(0), side=None))['entry_slip_bps'] is None


def test_slippage_section_counts_unknown_and_needs_30_round_trips():
    start, end = na.window_for('2026-10-10')
    assert na.slippage_section([], start, end)['verdict'].startswith('UNKNOWN: no round trip closed')
    rows = [row(start + 60 * i) for i in range(10)] + [row(start + 5, vmid=None), row(end + 5)]
    sec = na.slippage_section(rows, start, end)
    assert sec['rows'] == 11 and sec['all_in_bps']['n'] == 10 and sec['all_in_bps']['unknown'] == 1
    assert sec['entry_slip_bps']['unknown'] == 1 and 'n=10 < 30' in sec['verdict']
    rich = [row(start + 60 * i, fee=10.0 + (i % 3)) for i in range(40)]
    assert na.slippage_section(rich, start, end)['verdict'].startswith('ABOVE MODEL')
    cheap = [row(start + 60 * i, fee=1.0, vmid=100.0, ref=100.5) for i in range(40)]
    assert na.slippage_section(cheap, start, end)['verdict'].startswith('BELOW MODEL')


def per_second(steps_bps, t0):
    ts = np.arange(len(steps_bps), dtype=np.int64) + int(t0)
    return ts, 100.0 * np.exp(np.cumsum(np.asarray(steps_bps, dtype=float)) / 1e4)


def test_forward_labels_follow_the_classifier_definitions():
    cfg = RegimeConfig()
    zig = [1.0 if i % 2 else -1.0 for i in range(4000)]
    ts, px = per_second(zig, 1_000_000)
    assert set(na.forward_labels(ts, px, np.array([1_001_000, 1_002_000]), cfg)) == {SIDEWAYS}
    rng = np.random.default_rng(1)
    ts, px = per_second(0.4 + rng.normal(0, 0.2, 4000), 1_000_000)
    assert na.forward_labels(ts, px, np.array([1_001_000]), cfg) == [CALM_TREND]
    steps = zig[:1500] + [-8.0] * 30 + zig[:2500]
    ts, px = per_second(steps, 1_000_000)
    assert na.forward_labels(ts, px, np.array([1_001_400]), cfg) == [CRASH]
    assert na.forward_labels(ts, px, np.array([1_003_900]), cfg) == [None]      # forward window incomplete
    noisy = zig[:1500] + [12.0 if i % 2 else -12.0 for i in range(900)] + zig[:1600]
    ts, px = per_second(noisy, 1_000_000)
    labels = na.forward_labels(ts, px, np.arange(1_000_000, 1_003_000, 60), cfg)
    assert JUMPY in labels and SIDEWAYS in labels


def test_crash_latency_is_measured_from_the_visible_crossing():
    cfg = RegimeConfig()
    zig = [1.0 if i % 2 else -1.0 for i in range(3000)]
    for steps, detected in ((zig[:1500] + [-8.0] * 30 + zig[:1470], 1),          # violent: CRASH at the crossing
                            (zig[:1500] + [-200.0 / 290] * 290 + zig[:1210], 0)):  # orderly 200 bps: no vol, no tail
        ts, px = per_second(steps, 1_000_000)
        _, _, _, states = na.classifier_states(ts, px, ts[::60], cfg)
        ev = na.crash_events(ts, px, states, cfg)
        assert ev['realized_crash_starts'] == 1 and ev['detected'] == detected and ev['false_episodes'] == 0
        assert ev['latency_s'] == ([0] if detected else [None])


def write_ticks(base: Path, symbol: str, day_start: float, steps_bps, every_s: int = 5):
    """Public-aggTrade layout <base>/<SYMBOL>/<YYYYMMDD>.jsonl.gz, one print every `every_s` seconds."""
    px = 100.0 * np.exp(np.cumsum(np.asarray(steps_bps, dtype=float)) / 1e4)
    by_day = {}
    for i, p in enumerate(px):
        t = day_start + i * every_s
        day = datetime.fromtimestamp(t, timezone.utc).strftime('%Y%m%d')
        by_day.setdefault(day, []).append(json.dumps({'source_timestamp': int(t * 1000), 'source_time_unit': 'ms',
                                                      'p': float(p), 'q': 1.0, 'm': bool(i % 2)}))
    (base / symbol).mkdir(parents=True, exist_ok=True)
    for day, lines in by_day.items():
        with gzip.open(base / symbol / f'{day}.jsonl.gz', 'wt', encoding='utf-8') as fh:
            fh.write('\n'.join(lines + ['not json']) + '\n')


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(na, 'RECORDINGS', tmp_path / 'no_recordings')
    monkeypatch.setattr(na, 'L2_DIR', tmp_path / 'no_l2')
    return tmp_path


def test_no_data_is_unknown_and_proposes_the_recorder(isolated):
    rep = na.build(None, None, isolated / 'missing_ledger.jsonl', now=NOW)
    assert rep['day'] == '2026-11-06' and rep['regime']['source'] is None
    assert rep['regime']['verdict'].startswith('UNKNOWN') and rep['slippage']['verdict'].startswith('UNKNOWN')
    assert any('GHI_BANG_L2' in p for p in rep['proposals'])


def test_end_to_end_report_scores_states_and_writes_only_two_files(isolated):
    start, end = na.window_for('2026-10-10')
    rng = np.random.default_rng(3)
    n = int((end + 1800 - (start - 3600)) // 5)
    steps = rng.normal(0, 1.2, n)
    steps[n // 2:n // 2 + 12] = -30.0                                  # one 360 bps drop inside the window
    ticks = isolated / 'ticks'
    write_ticks(ticks, 'XUSDT', start - 3600, steps)
    ledger = isolated / 'ledger.jsonl'
    ledger.write_text('\n'.join([json.dumps(row(start + 100)), '{bad json', json.dumps(row(end + 100))]) + '\n',
                      encoding='utf-8')
    rep = na.build('2026-10-10', ticks, ledger, now=NOW)
    sym = rep['regime']['symbols']['XUSDT']
    assert rep['regime']['source'] == 'ticks' and sym['price_field'] == 'aggTrade'
    assert sym['labelled'] > 1000 and sym['accuracy'] is not None and 0.0 <= sym['coverage'] <= 1.0
    assert sym['crash']['realized_crash_starts'] >= 1 and sym['crash']['detected'] >= 1
    assert 0.0 <= sym['guard']['protection_recall'] <= 1.0
    assert rep['slippage']['rows'] == 1
    out = isolated / 'reviews'
    before = sorted(p.relative_to(isolated) for p in isolated.rglob('*'))
    written = na.write_report(rep, out)
    assert sorted(p.name for p in written) == ['nightly_2026-10-09.json', 'nightly_2026-10-09.md']
    after = sorted(p.relative_to(isolated) for p in isolated.rglob('*'))
    assert set(after) - set(before) == {Path('reviews'), *(p.relative_to(isolated) for p in written)}
    assert json.loads(written[0].read_text(encoding='utf-8'))['schema'] == 'nightly_audit/1'
    assert '# Nightly audit 2026-10-09' in written[1].read_text(encoding='utf-8')


def test_dry_run_writes_nothing(isolated, capsys):
    out = isolated / 'reviews'
    assert na.main(['--end', '2026-10-10', '--ledger', str(isolated / 'none.jsonl'), '--out-dir', str(out),
                    '--dry-run']) == 0
    assert not out.exists() and '# Nightly audit 2026-10-09' in capsys.readouterr().out


def test_module_has_one_writer_and_never_touches_config():
    src = Path(na.__file__).read_text(encoding='utf-8')
    assert src.count('write_text(') == 1 and src.count('os.replace(') == 1
    assert 'live_tunables' not in src and 'config' + '/' not in src
    assert not re.search(r"open\([^)]*,\s*['\"][wax]", src)                # every open() call reads
    assert UNKNOWN in na.RESTRICTIVE
