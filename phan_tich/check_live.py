"""Kiem tra suc khoe bot DANG CHAY -- chi doc, an toan khi bot con song.

Cach dung:  python phan_tich\check_live.py [--since <epoch>] [--alert -2.0] [--rollback -4.0]

Nhan dang:
  * last-state ghi ~2 lan/giay -> file cu hon 60 giay = bot co the da dung.
  * Equity drift = gia tri moi nhat - gia tri dau tien trong trace (USDT).
  * Journal counts tu moc --since de thay nhip giao dich.
"""
from __future__ import annotations
import argparse, json, sqlite3, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', type=float, default=time.time() - 12*3600)
    ap.add_argument('--alert', type=float, default=-2.0)
    ap.add_argument('--rollback', type=float, default=-4.0)
    a = ap.parse_args()
    now = time.time()
    problems = []

    ls_path = ROOT/'data/testnet-last-state.json'
    age = now - ls_path.stat().st_mtime if ls_path.exists() else 1e9
    state = json.loads(ls_path.read_text(encoding='utf-8')) if ls_path.exists() else {}

    equity = []
    def walk(node):
        if isinstance(node, list) and node and isinstance(node[0], dict) and 'value' in node[0]:
            if len(node) > len(equity):
                equity.clear(); equity.extend(node)
            return
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(state)
    first, last = (equity[0], equity[-1]) if equity else (None, None)
    drift = round(last['value'] - first['value'], 4) if equity else None

    con = sqlite3.connect(f'file:{ROOT/"data/testnet-journal.sqlite"}?mode=ro', uri=True)
    counts = dict(con.execute(
        "select kind, count(*) from events where timestamp >= ? group by kind", (a.since,)))
    con.close()

    ledger = json.loads((ROOT/'data/testnet-settled-v2.json').read_text(encoding='utf-8'))
    records = ledger['records'] if isinstance(ledger, dict) else ledger

    print(f'=== CHECK LIVE  ({time.strftime("%H:%M:%S")}) ===')
    print(f'bot ghi last-state cach day: {age:5.0f}s  ->  {"ALIVE" if age < 60 else "NGHI NGO DUNG (" + str(round(age)) + "s)"}')
    if equity:
        print(f'equity: {last["value"]:.4f} USDT | drift phien: {drift:+.4f} USDT | mau trace: {len(equity)}')
        if drift is not None and drift <= a.rollback:
            problems.append(f'DRIFT {drift} <= {a.rollback} -> ROLLBACK (kill switch, khoi phuc backup)')
        elif drift is not None and drift <= a.alert:
            problems.append(f'DRIFT {drift} <= {a.alert} -> CANH BAO, xem dashboard')
    else:
        print('equity trace: khong doc duoc')
    print('journal tu moc:', {k: v for k, v in sorted(counts.items())})
    if counts.get('HALT_CLEARED', 0) == 0 and counts.get('HALT', 0) > 0:
        problems.append('HALT chua duoc xa -> xem journal')
    print(f'ledger: n={len(records)} | phan loai da settlement cuoi: {time.strftime("%d/%m %H:%M", time.localtime(records[-1]["closed_at"])) if records else "-"}')
    if problems:
        print('\nVAN DE:')
        for p in problems:
            print('  -', p)
    else:
        print('\nON TAT CA CAC NGUONG.')
    print('goi y: chay them `python phan_tich\\pool_phien.py --since %d` khi phien da ket thuc.' % a.since)


if __name__ == '__main__':
    main()
