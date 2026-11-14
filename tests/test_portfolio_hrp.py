"""2026-11-07: ported HRP (alpha-agent wrapper + general sleeves) and the pure parts of research/hrp_alpha_sleeves."""
import numpy as np
import pytest

import autoresearch_agent_swarm as ar
from core.consensus_engine import WEIGHTS
from core.portfolio_hrp import ALPHA_AGENTS, ALPHA_BUDGET, hrp_allocate, hrp_proposal
from research import hrp_alpha_sleeves as hs
from tools import funding_spread_replay as fsr

DAY = 86_400_000


# ------------------------------------------------------------------ core.portfolio_hrp
def test_hrp_proposal_is_bounded_to_the_fixed_alpha_budget():
    rng = np.random.default_rng(14)
    common = rng.normal(0.0, 1.0, 90)
    returns = {'NORO': common + rng.normal(0.0, 0.15, 90), 'LUMEN': common + rng.normal(0.0, 0.20, 90),
               'TIDAL': rng.normal(0.0, 1.0, 90)}
    proposal = hrp_proposal(returns)
    assert proposal is not None and set(proposal.weights) == set(ALPHA_AGENTS)
    assert sum(proposal.weights.values()) == pytest.approx(ALPHA_BUDGET)
    assert all(0.10 <= value <= 0.30 for value in proposal.weights.values())
    assert sum(WEIGHTS[a] for a in ('ZEPHR', 'RUNE', 'OKAPI', 'MARIN', 'VESKA')) == pytest.approx(0.40)
    assert hrp_proposal({name: values[:20] for name, values in returns.items()}) is None
    with pytest.raises(ValueError):
        hrp_proposal({'NORO': common, 'LUMEN': common})


def test_two_sleeve_hrp_is_inverse_variance_and_deterministic():
    rng = np.random.default_rng(1)
    x, y = rng.normal(0, 1, 200), rng.normal(0, 3, 200)
    p = hrp_allocate({'A': x, 'B': y})
    iv = np.array([1 / np.var(x, ddof=1), 1 / np.var(y, ddof=1)])
    assert p.weights['A'] == pytest.approx(iv[0] / iv.sum(), rel=1e-12)
    assert sum(p.weights.values()) == pytest.approx(1.0)
    assert hrp_allocate({'B': y, 'A': x}) == p
    capped = hrp_allocate({'A': x, 'B': y}, upper=0.7, lower=0.3)
    assert capped.weights['A'] == pytest.approx(0.7) and capped.weights['B'] == pytest.approx(0.3)


def test_hrp_allocate_drops_non_finite_rows_jointly_and_validates():
    rng = np.random.default_rng(2)
    x, y = rng.normal(0, 1, 40), rng.normal(0, 2, 40)
    y[5] = np.nan
    assert hrp_allocate({'A': x, 'B': y}).observations == 39
    assert hrp_allocate({'A': x[:20], 'B': y[:20]}) is None
    for bad in ({'A': x}, {'A': x, 'B': y[:10]}):
        with pytest.raises(ValueError):
            hrp_allocate(bad)
    with pytest.raises(ValueError):
        hrp_allocate({'A': x, 'B': y}, lower=0.6)                 # 2 x 0.6 > budget
    with pytest.raises(ValueError):
        hrp_allocate({'A': x, 'B': y}, names=('A', 'C'))


# ------------------------------------------------------------------ research/hrp_alpha_sleeves pure parts
def synthetic_xv(drop_midnight=None, seed=0):
    rng = np.random.default_rng(seed)
    t4 = np.arange(5 * DAY, 60 * DAY, 4 * 3600_000, dtype=np.int64)
    pb = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, t4.size)))
    po = pb * (1 + rng.normal(0, 0.0005, t4.size))
    if drop_midnight is not None:
        keep = t4 != drop_midnight
        t4, pb, po = t4[keep], pb[keep], po[keep]
    bt = np.arange(5 * DAY, 60 * DAY, 8 * 3600_000, dtype=np.int64)
    ht = np.arange(5 * DAY, 60 * DAY, 3600_000, dtype=np.int64)
    return {'bn': (bt, rng.normal(1.0, 0.5, bt.size)), 'hl': (ht, rng.normal(0.2, 0.1, ht.size)),
            'px_bn': (t4, pb), 'px_other': (t4, po)}


def replay_trade(s, T, days, short_bn, cost=15.0):
    """The tools/funding_spread_replay.simulate P&L for one position, written out independently."""
    E = T + days * DAY
    r_bn, r_ot = fsr.window_sum(*s['bn'], T, E), fsr.window_sum(*s['hl'], T, E)
    pb0, pb1 = fsr.price_at(*s['px_bn'], T), fsr.price_at(*s['px_bn'], E)
    po0, po1 = fsr.price_at(*s['px_other'], T), fsr.price_at(*s['px_other'], E)
    if short_bn:
        fund, basis, legs = r_bn - r_ot, ((pb0 - pb1) / pb0 + (po1 - po0) / po0) * 1e4, ('bn_perp', 'hl_perp')
    else:
        fund, basis, legs = r_ot - r_bn, ((po0 - po1) / po0 + (pb1 - pb0) / pb0) * 1e4, ('hl_perp', 'bn_perp')
    return {'coin': 'X', 't': T / 1000, 'days': days, 'legs': legs, 'funding_bps': fund, 'basis_bps': basis,
            'cost_bps': cost, 'net_bps': fund + basis - cost}


@pytest.mark.parametrize('short_bn', [True, False])
@pytest.mark.parametrize('drop', [None, 14 * DAY])
def test_xv_daily_marks_sum_to_the_replay_net(short_bn, drop):
    s = synthetic_xv(drop)
    T = 10 * DAY + 8 * 3600_000
    tr = replay_trade(s, T, 7, short_bn)
    marks = hs.xv_daily_mtm(tr, s)
    assert sum(marks.values()) == pytest.approx(tr['net_bps'], abs=1e-9)
    assert min(marks) == T // DAY and max(marks) == (T + 7 * DAY) // DAY
    if drop is not None:                                   # a missing 00:00 mark moves its change to the next day
        full = hs.xv_daily_mtm(replay_trade(synthetic_xv(None), T, 7, short_bn), synthetic_xv(None))
        d = drop // DAY
        assert marks[d - 1] != pytest.approx(full[d - 1])
        assert marks[d - 1] + marks[d] == pytest.approx(full[d - 1] + full[d], abs=1e-9)
        assert all(marks[k] == pytest.approx(full[k]) for k in full if k not in (d - 1, d))


def test_xv_daily_sleeve_divides_by_slots_and_clips_the_window():
    s = synthetic_xv()
    T = 10 * DAY + 8 * 3600_000
    tr = dict(replay_trade(s, T, 7, True), series='X')
    full = hs.xv_daily([tr], {'X': s}, T // DAY, T // DAY + 7, slots=2)
    assert full.sum() == pytest.approx(tr['net_bps'] / 2, abs=1e-9)
    part = hs.xv_daily([tr], {'X': s}, T // DAY + 2, T // DAY + 3, slots=2)
    assert part.size == 2 and part.sum() == pytest.approx(full[2:4].sum())


def synthetic_bars(days=6, seed=4):
    rng = np.random.default_rng(seed)
    n = days * 1440
    t = (np.arange(n, dtype=np.int64) * ar.BAR_MS) + 20 * DAY
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.0008, n)))
    high, low = close * 1.0004, close * 0.9996
    vol = np.full(n, 10.0)
    return ar.make_bars('XUSDT', t, high, low, close, vol, vol * rng.uniform(0.3, 0.7, n))


def test_tidal_daily_never_reads_past_its_window():
    bars = synthetic_bars()
    agent = ar.TradeAgent('T', 'TIDAL', 120, 30, 1.0, 1.0, 30)
    d_lo, d_hi = 20 + 3, 20 + 4
    base = hs.tidal_daily(agent, bars, d_lo, d_hi)
    assert base.size == 2 and np.count_nonzero(base) > 0
    later = synthetic_bars()
    cut = int(np.searchsorted(later[0].t_ms, (d_hi + 1) * DAY))
    for arr in (later[0].close, later[0].high, later[0].low):
        arr[cut:] *= 1.5
    later = ar.make_bars('XUSDT', later[0].t_ms, later[0].high, later[0].low, later[0].close,
                         np.full(later[0].t_ms.size, 10.0), np.full(later[0].t_ms.size, 5.0))
    again = ar.make_bars('XUSDT', bars[0].t_ms, bars[0].high, bars[0].low, bars[0].close,
                         np.full(bars[0].t_ms.size, 10.0), np.full(bars[0].t_ms.size, 5.0))
    assert np.array_equal(hs.tidal_daily(agent, later, d_lo, d_hi), hs.tidal_daily(agent, again, d_lo, d_hi))


def test_stats_drawdown_and_walk_forward_use_only_the_past():
    s = hs.stats(np.array([1.0, -2.0, 3.0]))
    assert s['total_bps'] == 2.0 and s['max_drawdown_bps'] == 2.0 and s['worst_day_bps'] == -2.0
    rng = np.random.default_rng(9)
    est = {'A': rng.normal(0, 1, 60), 'B': rng.normal(0, 4, 60)}
    days = np.arange(hs.day_of('2026-03-30'), hs.day_of('2026-04-29') + 1)
    ev = {'A': rng.normal(0, 1, days.size), 'B': rng.normal(0, 4, days.size)}
    daily, months = hs.walk_forward(est, ev, days)
    assert months['2026-03'] == hrp_allocate(est).weights
    april = {k: v.copy() for k, v in ev.items()}
    april['B'][2:] *= 10.0                                     # days[2:] are April: no month may see them early
    assert hs.walk_forward(est, april, days)[1] == months
    march = {k: v.copy() for k, v in ev.items()}
    march['B'][:2] *= 10.0                                     # March data moves April's weights only
    _, months3 = hs.walk_forward(est, march, days)
    assert months3['2026-03'] == months['2026-03'] and months3['2026-04'] != months['2026-04']
    assert daily.size == days.size
