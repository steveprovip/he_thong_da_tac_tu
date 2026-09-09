"""Offline TTL sweep for maker-exit grace (Astra Q2).

Pure analysis on journal + mission-report ground truth: for each candidate
grace/TTL, re-derive P2 placed/filled with the same heuristic as
phan_tich/pool_phien.py::p2_maker_fill and report Wilson 95%.
No execution change; output feeds the checkpoint decision only.
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from phan_tich.pool_phien import p2_maker_fill, load_events  # noqa: E402


CANDIDATE_TTLS = (0.5, 1.0, 2.0, 5.0, 8.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', type=float, default=0.0)
    ap.add_argument('--ttls', type=float, nargs='*', default=list(CANDIDATE_TTLS))
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1]
    journal = load_events(root / 'data' / 'testnet-journal.sqlite')
    out = {}
    for ttl in args.ttls:
        out[f'grace_{ttl:g}s'] = p2_maker_fill(journal, args.since, grace=ttl)
    mr_path = root / 'data' / 'testnet-mission-report.json'
    if mr_path.exists():
        mr = json.loads(mr_path.read_text(encoding='utf-8'))
        out['mission_report_ground_truth'] = mr.get('maker_exits')
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == '__main__':
    sys.exit(main())
