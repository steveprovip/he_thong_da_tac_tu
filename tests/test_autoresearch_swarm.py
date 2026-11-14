"""Autoresearch loop: pinball math, survivor weights, causality, CRN, git gating, veto-only tunables proposal."""
import json
import subprocess
from dataclasses import dataclass, replace

import numpy as np
import pytest

import autoresearch_agent_swarm as ar
from core import tunables
from core.consensus_engine import WEIGHTS, ConsensusEngine, TokenAuthority, Vote
from core.invariants import WEIGHTS_SHA256, check_consensus, weights_sha256


def _gbm_segment(n=4200, sigma=2e-4, seed=0):
    rng = np.random.default_rng(seed)
    logmid = np.log(100.0) + np.concatenate(([0.0], np.cumsum(rng.normal(0.0, sigma, n - 1))))
    return ar.Segment(1.7e9, logmid, np.clip(rng.normal(0.0, 0.3, n), -1, 1), 'synthetic')


def _git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / 'repo'
    r.mkdir()
    _git(r, 'init', '-q')
    for k, v in (('user.name', 'test'), ('user.email', 'test@example.invalid'), ('core.autocrlf', 'true')):
        _git(r, 'config', k, v)
    (r / 'other.txt').write_text('base\n')
    (r / 'staged.txt').write_text('s0\n')
    _git(r, 'add', '.')
    _git(r, 'commit', '-q', '-m', 'base')
    _git(r, 'switch', '-q', '-c', 'autoresearch/test')
    (r / 'other.txt').write_text('owner work in progress\n')            # unrelated, unstaged
    (r / 'staged.txt').write_text('s1\n')
    _git(r, 'add', 'staged.txt')                                        # unrelated, staged
    return r


def test_pinball_matches_definition():
    q = np.array([-1.0, -0.5, 0.0, 0.1, 0.2, 0.5, 1.0])
    y = 0.15
    expected = np.mean([max(t * (y - v), (t - 1) * (y - v)) for t, v in zip(ar.QUANTILES, q)])
    assert ar.pinball(y, q) == pytest.approx(expected)
    assert ar.pinball(np.array([0.15, 0.15]), np.vstack([q, q])) == pytest.approx([expected, expected])


def test_survivor_weights_quartiles_floor_cap_and_ties():
    losses = {f'A{i}': float(i) for i in range(8)}                    # A0 best ... A7 worst
    w = ar.survivor_update({n: 1.0 for n in losses}, losses)
    assert w['A0'] == w['A1'] == 1.05 and w['A6'] == w['A7'] == 0.95
    assert all(w[f'A{i}'] == 1.0 for i in range(2, 6))
    w = ar.survivor_update({**{n: 1.0 for n in losses}, 'A0': 2.45, 'A7': 0.31}, losses)
    assert w['A0'] == 2.5 and w['A7'] == 0.3
    tie = ar.survivor_update({n: 1.0 for n in 'abcdefgh'}, {n: 1.0 for n in 'abcdefgh'})
    assert [tie[n] for n in 'abcdefgh'] == [1.05, 1.05, 1.0, 1.0, 1.0, 1.0, 0.95, 0.95]


def test_forecast_uses_only_bars_up_to_t():
    seg, i = _gbm_segment(), 2500
    lm, ob = seg.logmid.copy(), seg.obi.copy()
    lm[i + 1:] += 0.05
    ob[i + 1:] = -ob[i + 1:]
    for cfg in ar.default_agents():
        u = ar.crn_uniforms(7, 0, i, -(-ar.HORIZON_S // cfg.block_s))
        assert np.array_equal(ar.forecast(cfg, seg.logmid, seg.obi, i, u), ar.forecast(cfg, lm, ob, i, u))


def test_score_is_deterministic_and_crn_prefix_is_shared_across_block_sizes():
    segs = [_gbm_segment()]
    pts = ar.eval_points(segs, 120)
    cfg = ar.default_agents()[3]
    assert pts and ar.score(cfg, segs, pts, 7) == ar.score(cfg, segs, pts, 7)
    assert np.array_equal(ar.crn_uniforms(7, 0, 1900, 4), ar.crn_uniforms(7, 0, 1900, 180)[:, :4])


def test_loss_prefers_calibrated_dispersion_on_a_random_walk():
    segs = [_gbm_segment(n=9000, seed=s) for s in range(2)]
    pts = ar.eval_points(segs, 60)
    base = ar.AgentConfig('B', 'TIDAL', 1800, 60, 60, 0.0)
    good = ar.score(base, segs, pts, 1)
    assert good < ar.score(replace(base, vol_mult=2.0), segs, pts, 1)
    assert good < ar.score(replace(base, vol_mult=0.5), segs, pts, 1)


def test_mutation_changes_one_parameter_within_bounds():
    cfg = ar.default_agents()[0]
    for r in range(60):
        cand, name = ar.mutate(cfg, np.random.default_rng([1, r]))
        diff = [k for k in ar.BOUNDS if getattr(cand, k) != getattr(cfg, k)]
        assert name in diff or cand == cfg
        assert set(diff) <= {name, 'block_s', 'signal_s'}
        assert all(lo <= getattr(cand, k) <= hi for k, (lo, hi, _) in ar.BOUNDS.items())
        assert cand.block_s <= cand.lookback_s // 2 and cand.signal_s <= cand.lookback_s


def test_load_segments_keeps_last_quote_per_second_and_splits_gaps(tmp_path):
    def bt(lt, b, a, bq, aq):
        return json.dumps({'lt': lt, 'ch': 'bookTicker', 'b': str(b), 'a': str(a), 'B': str(bq), 'A': str(aq)})
    lines = [bt(100.1, 10, 11, 1, 3), bt(100.9, 10, 12, 3, 1), json.dumps({'lt': 101.5, 'ch': 'depth', 'b': []}),
             bt(102.2, 11, 13, 1, 1), bt(200.0, 20, 21, 2, 2), '{"lt": 201, "ch": "bookTicker", "b": "x"']
    p = tmp_path / 'x.jsonl'
    p.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    segs = ar.load_segments(p, max_gap_s=30)
    assert [s.logmid.size for s in segs] == [3, 1]
    assert np.allclose(np.exp(segs[0].logmid), [11.0, 11.0, 12.0])      # last quote wins, then forward fill
    assert np.allclose(segs[0].obi, [0.5, 0.5, 0.0])


def test_git_gate_commits_only_the_experiment_file_and_rolls_back_bit_exact(repo):
    state = repo / 'config' / 'agents.json'
    gate = ar.GitGate(state, True)
    gate.preflight()
    agents = ar.default_agents()
    ar.atomic_write(state, ar.state_bytes(agents))
    assert gate.accept('autoresearch: init')
    assert _git(repo, 'show', '--name-only', '--format=', 'HEAD').split() == ['config/agents.json']
    good = state.read_bytes()
    ar.atomic_write(state, ar.state_bytes([replace(agents[0], coef=0.9)] + agents[1:]))
    gate.rollback(good)
    assert state.read_bytes() == good
    _git(repo, 'diff', '--quiet', 'HEAD', '--', 'config/agents.json')
    assert (repo / 'other.txt').read_text() == 'owner work in progress\n'
    st = _git(repo, 'status', '--porcelain')
    assert ' M other.txt' in st and 'M  staged.txt' in st                # never committed, never unstaged


def test_git_gate_refuses_a_dirty_experiment_file_and_a_non_autoresearch_branch(repo):
    state = repo / 'agents.json'
    ar.atomic_write(state, b'{}\n')
    _git(repo, 'add', 'agents.json')
    _git(repo, 'commit', '-q', '-m', 'x', '--', 'agents.json')
    state.write_bytes(b'{"dirty": 1}\n')
    with pytest.raises(SystemExit, match='uncommitted'):
        ar.GitGate(state, True).preflight()
    ar.atomic_write(state, b'{}\n')
    _git(repo, 'switch', '-q', '-c', 'feature/x')
    with pytest.raises(SystemExit, match='autoresearch/'):
        ar.GitGate(state, True).preflight()


def test_end_to_end_rounds_commit_kept_mutations_and_emit_a_valid_proposal(repo, tmp_path):
    mid = 100 * np.exp(np.cumsum(np.random.default_rng(3).normal(0.0, 2e-4, 3600)))
    data = tmp_path / 'syn.jsonl'
    data.write_text('\n'.join(json.dumps({'lt': 1.7e9 + t + 0.5, 'ch': 'bookTicker', 'b': f'{m - 0.01:.4f}',
                                          'a': f'{m + 0.01:.4f}', 'B': '2', 'A': '1'}) for t, m in enumerate(mid)),
                    encoding='utf-8')
    state, out = repo / 'config' / 'autoresearch_agents.json', tmp_path / 'out'
    n0 = int(_git(repo, 'rev-list', '--count', 'HEAD'))
    rep = ar.run(ar.parse_args(['--data', str(data), '--rounds', '4', '--git', '--step-s', '60',
                                '--state', str(state), '--ledger', str(out / 'ledger.json'),
                                '--proposal', str(out / 'prop.json'), '--report', str(out / 'rep.json'),
                                '--cache-dir', '']))
    assert int(_git(repo, 'rev-list', '--count', 'HEAD')) - n0 == 1 + rep['accepted']
    assert rep['eval_points']['train'] > 0 and rep['eval_points']['holdout'] > 0
    assert all(ar.W_FLOOR <= w <= ar.W_CAP for w in rep['weights'].values())
    _git(repo, 'diff', '--quiet', 'HEAD', '--', 'config/autoresearch_agents.json')
    changes, observe, sw, rejected = tunables.validate(json.loads((out / 'prop.json').read_text()), None)
    assert rejected == [] and changes == {} and observe is False and set(sw) == set(ar.FAMILIES)


def test_proposal_maps_families_mean_normalised_clipped_and_never_targets_live_tunables(tmp_path):
    agents = ar.default_agents()
    w = {a.name: (2.5 if a.family == 'NORO' else 0.3) for a in agents}
    sw = ar.tunables_proposal(agents, w, now=0.0)['swarm_weights']
    assert sw == {'LUMEN': round(0.3 / 1.125, 4), 'NORO': 1.8, 'TIDAL': round(0.3 / 1.125, 4)}
    with pytest.raises(ValueError):
        ar.write_proposal(tmp_path / 'config' / 'live_tunables.json', {'swarm_weights': sw})


def test_consensus_invariants_rune_veto_and_tokens_are_untouched():
    @dataclass(frozen=True)
    class Intent:
        side: str
        symbol: str
    assert weights_sha256(WEIGHTS) == WEIGHTS_SHA256
    assert check_consensus(ConsensusEngine(trend_floor=.768)) == []                 # runtime policy (I8)
    votes = [Vote(n, 'BUY', 1.0, 'x') for n in WEIGHTS if n != 'RUNE'] + [Vote('RUNE', 'HOLD', 0.0, 'risk', veto=True)]
    d = ConsensusEngine().evaluate(votes)
    assert d.veto and not d.allowed
    with pytest.raises(PermissionError):
        TokenAuthority().issue(Intent('BUY', 'BTCUSDT'), d)


# ------------------------------------------------------------------ trade objective on 1m klines
def _bars(ret_bps, sigma_bps=10.0, n=4000, seed=0, symbol='X', t0=1_700_000_000_000, taker=0.6):
    rng = np.random.default_rng(seed)
    r = (ret_bps + rng.normal(0.0, sigma_bps, n)) * 1e-4
    c = 100 * np.exp(np.cumsum(r))
    o = np.r_[c[0], c[:-1]]
    wick = rng.integers(0, 2, n)                                         # half the bars have no wick
    hi, lo = np.maximum(o, c) * (1 + 2e-4 * wick), np.minimum(o, c) * (1 - 2e-4 * wick)
    t = t0 + ar.BAR_MS * np.arange(n)
    vol = np.full(n, 10.0)
    return t, hi, lo, c, vol, vol * taker


def test_make_bars_splits_gaps_and_keeps_prefix_sums_exact():
    t, hi, lo, c, v, tb = _bars(0.0, n=3000)
    t = t.copy()
    t[1500:] += 10 * ar.BAR_MS                                          # 10-minute hole
    segs = ar.make_bars('X', t, hi, lo, c, v, tb)
    assert [s.t_ms.size for s in segs] == [1500, 1500]
    s = segs[0]
    assert s.cs_r[-1] == pytest.approx(s.logc[-1] - s.logc[0])
    assert s.cs_imb[-1] / s.t_ms.size == pytest.approx(0.2)            # (2 * 0.6 - 1)


def test_trade_direction_costs_and_no_lookahead():
    b = ar.make_bars('X', *_bars(20.0, n=4000))[0]
    up = ar.TradeAgent('T', 'TIDAL', 600, 60, 1.0, 1.0, 15)
    idx, side, net = ar.trade_eval(up, b, 0, 10 ** 15, 'ideal')
    assert idx.size and (side > 0).all() and net.mean() > 200            # momentum rides the planted trend
    _, side_rev, net_rev = ar.trade_eval(replace(up, family='NORO', coef=-1.0), b, 0, 10 ** 15, 'ideal')
    assert net_rev.mean() < 0
    flat = ar.make_bars('X', *_bars(0.0, n=4000, seed=5))[0]
    i, s, n_ideal = ar.trade_eval(up, flat, 0, 10 ** 15, 'ideal')
    _, _, n_ioc = ar.trade_eval(up, flat, 0, 10 ** 15, 'ioc')
    fwd = s * (flat.logc[i + 15] - flat.logc[i]) * 1e4
    assert np.allclose(fwd - n_ideal, ar.fill_costs()['maker'])        # 8.5 bps
    assert ar.fill_costs()['ioc'] == 13.0 and n_ioc.size <= n_ideal.size
    t, hi, lo, c, v, tb = _bars(20.0, n=4000)
    c2 = c.copy()
    c2[3001:] *= 0.5                                                    # rewrite the future after bar 3000
    b2 = ar.make_bars('X', t, np.maximum(hi, c2), np.minimum(lo, c2), c2, v, tb)[0]
    i1, s1, _ = ar.trade_eval(up, b, 0, int(t[3000]) + 1, 'ideal')
    i2, s2, _ = ar.trade_eval(up, b2, 0, int(t[3000]) + 1, 'ideal')
    assert np.array_equal(i1, i2) and np.array_equal(s1, s2)           # decisions use bars <= t only


def test_maker_fill_needs_a_trade_through():
    b = ar.make_bars('X', *_bars(20.0, n=4000))[0]
    up = ar.TradeAgent('T', 'TIDAL', 600, 60, 1.0, 1.0, 15)
    ideal, _, _ = ar.trade_eval(up, b, 0, 10 ** 15, 'ideal')
    maker, side, _ = ar.trade_eval(up, b, 0, 10 ** 15, 'maker')
    assert set(maker) <= set(ideal) and maker.size < ideal.size
    assert (b.low[maker + 1] < b.close[maker]).all()


def test_perf_sharpe_and_psr_on_known_series():
    rng = np.random.default_rng(1)
    daily = rng.normal(10.0, 20.0, 400)
    p = ar.perf(daily, daily)
    assert p['sr_day'] == pytest.approx(daily.mean() / daily.std(ddof=1))
    assert p['sharpe_ann'] == pytest.approx(p['sr_day'] * np.sqrt(365)) and p['psr0'] > 0.99
    assert ar.perf(np.zeros(30), np.zeros(0))['psr0'] is None


def test_trade_loop_scores_holdout_once_and_emits_valid_proposal(tmp_path):
    import io
    import zipfile
    kdir = tmp_path / 'k'
    kdir.mkdir()
    for sym, seed in (('AAAUSDT', 1), ('BBBUSDT', 2)):
        t, hi, lo, c, v, tb = _bars(0.0, n=14400, seed=seed)
        buf = io.StringIO()
        buf.write('open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,'
                  'taker_buy_quote_volume,ignore\n')
        for k in range(t.size):
            buf.write(f'{t[k]},{c[k]},{hi[k]},{lo[k]},{c[k]},{v[k]},{t[k] + 59999},0,1,{tb[k]},0,0\n')
        with zipfile.ZipFile(kdir / f'{sym}-1m-2026-01.zip', 'w') as z:
            z.writestr(f'{sym}-1m-2026-01.csv', buf.getvalue())
    out = tmp_path / 'o'
    args = ar.parse_args(['--source', 'klines', '--symbols', 'AAAUSDT', 'BBBUSDT', '--klines-dir', str(kdir),
                          '--rounds', '6', '--min-trades', '5', '--state', str(out / 's.json'),
                          '--report', str(out / 'r.json'), '--proposal', str(out / 'p.json')])
    rep = ar.run_trade(args)
    assert rep['trials'] >= 8 and len(rep['history']) == 6
    assert rep['any_meets_criteria'] is False                            # random walk: no edge after costs
    for v in rep['agents'].values():
        assert v['holdout']['days'] > 0 and set(v['holdout_mean_net_by_fill']) == set(ar.FILLS)
    changes, observe, sw, rejected = tunables.validate(json.loads((out / 'p.json').read_text()), None)
    assert rejected == [] and set(sw) <= set(ar.FAMILIES)
