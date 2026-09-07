"""The signal lab must record faithfully and change nothing.

Its whole value is that it observes the trades the gates refused, so two
properties matter: probes resolve against later books only, and nothing the lab
does can reach the execution path.
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.barrier_planner import Barriers
from core.consensus_engine import Decision, Vote
from core.models import Book
from core.real_alpha_engine import Features
from core.signal_lab import MEASURED_ROUNDTRIP_BPS, SignalLab

SYMBOL = 'BTCUSDT'


def book(bid=100.0, ask=100.02, ts=0.0, seq=1):
    return Book(SYMBOL, ((bid, 5.),), ((ask, 5.),), ts, seq)


def features(**overrides):
    base = dict(obi=.2, ofi=0., cvd=0., flow=0., vpin=.30, vpin_saturation=.02, vwap_z=0.,
                momentum=.5, rsi=55., atr=1.0, sweep=0, spread_bps=2.0, ready=True)
    return Features(**{**base, **overrides})


def decision(side='BUY', score=.62, allowed=False, veto=True, reason='RUNE VETO'):
    return Decision(side, score, allowed, veto, reason)


def votes(blocked=('ZEPHR', 'RUNE')):
    out = [Vote(a, 'BUY', .6, 'alpha') for a in ('NORO', 'LUMEN', 'TIDAL')]
    out += [Vote(a, 'HOLD' if a in blocked else 'BUY', 0. if a in blocked else 1., 'gate', veto=a in blocked)
            for a in ('ZEPHR', 'RUNE', 'OKAPI', 'MARIN', 'VESKA')]
    return out


def open_probe(lab, side='BUY', now=0.0, stop=99.0, tp=103.0, **kw):
    return lab.observe(SYMBOL, features(**kw), book(), decision(side=side), votes(),
                       Barriers(stop, tp, now + lab.horizon), now, conviction=.6)


def test_probes_are_opened_for_signals_the_gates_blocked():
    lab = SignalLab()
    probe = open_probe(lab)
    assert probe is not None
    assert probe.gated is False and probe.blockers == ['ZEPHR', 'RUNE']
    assert probe.entry == 100.0, 'a long is quoted at the bid'


def test_probe_records_an_unblocked_signal_as_gate_clean():
    lab = SignalLab()
    probe = lab.observe(SYMBOL, features(), book(), decision(allowed=True, veto=False, reason='AUTHORIZED'),
                        votes(blocked=()), Barriers(99., 103., 300.), 0., conviction=.8)
    assert probe.gated is True and probe.blockers == []


def test_short_probe_quotes_the_ask_and_resolves_downward():
    lab = SignalLab()
    lab.observe(SYMBOL, features(), book(), decision(side='SELL'), votes(),
                Barriers(101.0, 97.0, 300.), 0., conviction=.6)
    probe = lab.open[SYMBOL][0]
    assert probe.entry == 100.02
    # A short exits at the ask; take-profit is below entry.
    assert lab.resolve(SYMBOL, book(96.8, 96.9, 5.), 5.)[0].outcome == 'TP'


def test_take_profit_and_stop_resolve_on_the_exit_side():
    lab = SignalLab()
    open_probe(lab)
    assert lab.resolve(SYMBOL, book(101.0, 101.02, 1.), 1.) == []
    closed = lab.resolve(SYMBOL, book(103.5, 103.52, 2.), 2.)
    assert len(closed) == 1 and closed[0].outcome == 'TP'
    assert closed[0].gross_bps == pytest.approx((103.5-100)/100*10000)
    assert closed[0].net_bps == pytest.approx(closed[0].gross_bps - closed[0].cost_bps)

    lab = SignalLab()
    open_probe(lab)
    assert lab.resolve(SYMBOL, book(98.5, 98.52, 2.), 2.)[0].outcome == 'SL'


def test_timeout_only_fires_at_the_deadline():
    lab = SignalLab(horizon=60.)
    open_probe(lab)
    assert lab.resolve(SYMBOL, book(100.5, 100.52, 30.), 30.) == []
    assert lab.resolve(SYMBOL, book(100.5, 100.52, 60.), 60.)[0].outcome == 'TIMEOUT'


def test_excursions_are_tracked_before_resolution():
    lab = SignalLab()
    open_probe(lab)
    lab.resolve(SYMBOL, book(102.0, 102.02, 1.), 1.)
    lab.resolve(SYMBOL, book(99.5, 99.52, 2.), 2.)
    probe = lab.open[SYMBOL][0]
    assert probe.mfe_bps == pytest.approx(200., abs=1)
    assert probe.mae_bps == pytest.approx(-50., abs=1)


def test_probe_rate_is_bounded_by_gap_and_capacity():
    lab = SignalLab(max_open=3, min_gap=10.)
    assert open_probe(lab, now=0.) is not None
    assert open_probe(lab, now=5.) is None, 'min_gap not respected'
    assert open_probe(lab, now=10.) is not None
    assert open_probe(lab, now=20.) is not None
    assert open_probe(lab, now=30.) is None, 'max_open not respected'
    assert len(lab.open[SYMBOL]) == 3


def test_warmup_and_unusable_plans_are_skipped():
    lab = SignalLab()
    assert lab.observe(SYMBOL, features(ready=False), book(), decision(), votes(),
                       Barriers(99., 103., 300.), 0., conviction=.1) is None
    assert open_probe(lab, atr=1.0) is not None
    assert lab.observe(SYMBOL, features(), book(), decision(), votes(), None, 100., conviction=.6) is None


def test_resolved_probes_are_appended_as_jsonl(tmp_path):
    path = tmp_path/'lab.jsonl'
    lab = SignalLab(path)
    open_probe(lab)
    lab.resolve(SYMBOL, book(103.5, 103.52, 2.), 2.)
    open_probe(lab, now=100.)
    lab.resolve(SYMBOL, book(98.0, 98.02, 101.), 101.)
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    assert [r['outcome'] for r in rows] == ['TP', 'SL']
    assert all({'conviction', 'edge_bps', 'cost_bps', 'gated', 'blockers'} <= set(r) for r in rows)
    assert lab.summary()['probes'] == 2 and lab.summary()['tp'] == .5


def test_lab_cannot_reach_the_execution_path():
    """A recorder that could place an order would be a second, unaudited gateway."""
    lab = SignalLab()
    forbidden = ('client', 'gateway', 'authority', 'submit', 'place_entry', 'protect')
    assert not any(hasattr(lab, name) for name in forbidden)
    probe = open_probe(lab)
    assert not any(hasattr(probe, name) for name in forbidden)


async def test_runtime_observation_leaves_the_gateway_untouched(engine_factory):
    engine = engine_factory()
    engine.gateway = SimpleNamespace(submit=AsyncMock(), ratchet=AsyncMock(), exit=AsyncMock(),
                                     cancel_entries=AsyncMock(), reconcile=AsyncMock())
    probe = engine.observe_signal(SYMBOL, features(), book(), decision(), votes(), .6, 0.)
    assert probe is not None and probe.gated is False
    for method in ('submit', 'ratchet', 'exit', 'cancel_entries', 'reconcile'):
        getattr(engine.gateway, method).assert_not_awaited()


# --------------------------------------------------------------- cost model
def test_cost_model_defaults_to_the_measured_roundtrip():
    """8.0 bps was a guess; 6.00 is what the venue actually charged.

    Reconciling 14 audited round trips against /fapi/v1/userTrades fits
    fee = 2bps*entry_notional + 4bps*exit_notional to a mean absolute error of
    5e-6 bps. The old constant biased every calibrate_edge verdict by 2 bps in
    the same direction, which is the direction that hides a working strategy.
    """
    assert MEASURED_ROUNDTRIP_BPS == 6.0
    lab = SignalLab()
    assert lab.cost_bps == 6.0
    probe = open_probe(lab)
    assert probe.cost_bps == 6.0
    closed = lab.resolve(SYMBOL, book(103.5, 103.52, 2.), 2.)[0]
    assert closed.net_bps == pytest.approx(closed.gross_bps - 6.0)


def test_cost_model_is_configurable_and_validated():
    assert open_probe(SignalLab(cost_bps=9.5)).cost_bps == 9.5
    for bad in (-1.0, float('nan'), float('inf')):
        with pytest.raises(ValueError):
            SignalLab(cost_bps=bad)
