"""Latency of System-1 gates vs the full 8-agent swarm evaluate(). Offline, no network.

    python tools/bench_system1.py [--n 20000]
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.groktopus_swarm import GroktopusSwarm  # noqa: E402
from core.jev_gatekeeper import JevGatekeeper  # noqa: E402
from core.models import Book  # noqa: E402
from core.real_alpha_engine import Features  # noqa: E402


def pct(samples, q):
    s = sorted(samples)
    return s[min(len(s) - 1, int(q * len(s)))] / 1e3


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=20000)
    n = ap.parse_args(argv).n
    f = Features(obi=.4, ofi=0., cvd=0., flow=.1, vpin=.3, vpin_saturation=.05, vwap_z=-1., momentum=.5,
                 rsi=45., atr=5., sweep=0, spread_bps=2.0, ready=True)
    book = Book('X', ((100., 5.), (99.99, 3.), (99.98, 2.)), ((100.01, 4.), (100.02, 2.), (100.03, 1.)), 0.)
    swarm, gk = GroktopusSwarm(), JevGatekeeper()
    sw = []
    for _ in range(n):
        t = time.perf_counter_ns()
        swarm.evaluate(f, 100.0, True, True, True, True)
        sw.append(time.perf_counter_ns() - t)
    for _ in range(n):
        gk.adverse(book, [1., -2., .5], 'BUY')
        gk.score(2.0, 8.0, funding_bps_8h=1.0)
    out = {'n': n,
           'swarm_evaluate_us': {'p50': round(pct(sw, .5), 2), 'p99': round(pct(sw, .99), 2),
                                 'mean': round(statistics.fmean(sw) / 1e3, 2)},
           'system1': gk.latency(), 'llm_tokens_per_decision': 0}
    print(json.dumps(out, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
