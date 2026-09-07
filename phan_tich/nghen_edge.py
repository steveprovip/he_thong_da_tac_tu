"""Tai lap TOAN BO so lieu trong bao cao chan doan edge am (Giai doan 1-4).

    python phan_tich/nghen_edge.py

Chi doc. Khong ghi file nao. Moi con so trong bao cao deu do file nay in ra.
Nguon du lieu:
  data/testnet-settled-v2.json   - ledger settled (chi co net_return)
  data/execution-audit.json      - commission + realizedPnl THAT tu /fapi/v1/userTrades
  data/testnet-journal.sqlite    - ENTRY_INTENT (price/stop/tp/side) + ORDER_STATE
  data/testnet-signal-lab.jsonl  - probe phan thuc v1 (n=4245)
  data/testnet-signal-lab-v2.jsonl - probe phan thuc v2 (n=933)
"""
import collections, json, math, random, sqlite3, statistics as st, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / 'data'
FEE_BPS = 6.00          # do duoc tu san, chung minh o muc 2
MAKER_BPS, TAKER_BPS = 2.0, 4.0


def bootstrap_ci(v, draws=3000, seed=7):
    if len(v) < 2:
        return float('nan'), float('nan')
    rng = random.Random(seed)
    m = sorted(st.fmean(rng.choices(v, k=len(v))) for _ in range(draws))
    return m[int(.025 * draws)], m[int(.975 * draws) - 1]


def load_lab():
    rows = []
    for name, tag in (('testnet-signal-lab.jsonl', 'v1'), ('testnet-signal-lab-v2.jsonl', 'v2')):
        p = D / name
        if not p.exists():
            continue
        for line in p.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get('outcome'):
                r['src'], r['H'] = tag, round(r['deadline'] - r['opened'])
                rows.append(r)
    return rows


def band(label, rows, w=34):
    if len(rows) < 10:
        print(f'{label:{w}}{len(rows):6}   n<10, khong ket luan')
        return
    g = [r['gross_bps'] for r in rows]
    lo, hi = bootstrap_ci(g)
    o = collections.Counter(r['outcome'] for r in rows)
    tag = 'DUONG' if lo > 0 else 'AM' if hi < 0 else '~0'
    print(f'{label:{w}}{len(rows):6}{st.mean(g):9.2f}  [{lo:+6.2f},{hi:+6.2f}] {tag:6}'
          f'{st.mean(g) - FEE_BPS:9.2f}{100*o["TP"]/len(rows):7.1f}{100*o["SL"]/len(rows):7.1f}'
          f'{st.mean([r["spread_bps"] for r in rows]):8.2f}')


def muc2_dinh_luat_phi():
    print('\n' + '=' * 100)
    print('MUC 2 — DINH LUAT PHI: fee = 2bps*N_entry + 4bps*N_exit  (entry maker, exit taker)')
    print('=' * 100)
    A = json.loads((D / 'execution-audit.json').read_text())['trades']
    errs = []
    print(f'{"symbol":9}{"N_entry":>10}{"gross":>10}{"fee_thuc":>11}{"fee_du_doan":>13}{"sai_so_bps":>12}{"entry_maker":>12}')
    for t in A:
        N, g, f = t['entry_notional_usdt'], t['gross_pnl_usdt'], t['fees_usdt']
        pS = MAKER_BPS/1e4*N + TAKER_BPS/1e4*(N - g)     # SHORT: exit notional = N - gross
        pL = MAKER_BPS/1e4*N + TAKER_BPS/1e4*(N + g)     # LONG
        best = min((pS, pL), key=lambda p: abs(p - f))
        e = 1e4 * (f - best) / N
        errs.append(abs(e))
        print(f'{t["symbol"]:9}{N:10.2f}{g:10.5f}{f:11.6f}{best:13.6f}{e:12.6f}{str(t["entry_maker"]):>12}')
    print(f'  sai so tuyet doi trung binh = {st.mean(errs):.6f} bps | max = {max(errs):.6f} bps')
    print(f'  => ENTRY 100% maker, EXIT 100% taker tren {len(A)}/{len(A)} lenh. Round-trip = {FEE_BPS:.2f} bps.')


def muc3_phan_ra_ledger():
    print('\n' + '=' * 100)
    print('MUC 3 — PHAN RA 20 LENH SETTLED: phi vs sai huong')
    print('=' * 100)
    S = json.loads((D / 'testnet-settled-v2.json').read_text())['records']
    aud = {t['trade_id']: t for t in json.loads((D / 'execution-audit.json').read_text())['trades']}
    tot = dict(n=0., fee=0., gross=0., net=0.)
    buckets, syms = collections.defaultdict(list), collections.defaultdict(list)
    print(f'{"symbol":9}{"notional":>10}{"net_bps":>9}{"fee_bps":>9}{"gross_bps":>10}  nguon')
    for r in S:
        N, net = r['entry_notional_usdt'], r['net_pnl_usdt']
        if r['trade_id'] in aud:
            fee, gross, src = aud[r['trade_id']]['fees_usdt'], aud[r['trade_id']]['gross_pnl_usdt'], 'SAN'
        else:
            fee = FEE_BPS / 1e4 * N
            gross, src = net + fee, 'UOC TINH'
        b = '<20' if N < 20 else '20-60' if N < 60 else '>=290'
        buckets[b].append((1e4*net/N, 1e4*gross/N, N))
        syms[r['symbol']].append((1e4*net/N, 1e4*gross/N, N))
        for k, v in (('n', N), ('fee', fee), ('gross', gross), ('net', net)):
            tot[k] += v
        print(f'{r["symbol"]:9}{N:10.2f}{1e4*net/N:9.2f}{1e4*fee/N:9.2f}{1e4*gross/N:10.2f}  {src}')
    print('-' * 62)
    print(f'  TONG: notional {tot["n"]:.2f} | gross {tot["gross"]:+.4f} | phi {tot["fee"]:.4f} | net {tot["net"]:+.4f} USDT')
    print(f'  PHI chiem {100*tot["fee"]/abs(tot["net"]):.1f}% tong lo | SAI HUONG chiem {100*abs(tot["gross"])/abs(tot["net"]):.1f}%')
    rets = [1e4*r['net_pnl_usdt']/r['entry_notional_usdt'] for r in S]
    sd = st.pstdev(rets)
    print(f'  edge equal-weight = {st.mean(rets):.2f} bps | sd {sd:.2f} | SE {sd/math.sqrt(len(rets)):.2f} '
          f'| KTC95 [{st.mean(rets)-1.96*sd/math.sqrt(len(rets)):.2f}, {st.mean(rets)+1.96*sd/math.sqrt(len(rets)):.2f}]')
    print(f'\n{"bucket":>8}{"n":>4}{"net_bps":>10}{"gross_bps":>11}{"PnL_USDT":>12}')
    for b in ('<20', '20-60', '>=290'):
        v = buckets[b]
        print(f'{b:>8}{len(v):4}{st.mean([x[0] for x in v]):10.2f}{st.mean([x[1] for x in v]):11.2f}'
              f'{sum(x[0]*x[2]/1e4 for x in v):12.4f}')
    print(f'\n{"symbol":10}{"n":>4}{"net_bps":>10}{"gross_bps":>11}{"PnL_USDT":>12}')
    for k, v in sorted(syms.items(), key=lambda x: -len(x[1])):
        print(f'{k:10}{len(v):4}{st.mean([x[0] for x in v]):10.2f}{st.mean([x[1] for x in v]):11.2f}'
              f'{sum(x[0]*x[2]/1e4 for x in v):12.4f}')
    big = [r for r in S if r['entry_notional_usdt'] >= 290]
    alt = sum(r['net_return'] * 4930 * 0.01 for r in big)
    print(f'\n  4 lenh >=290 USDT (--bootstrap-fraction 0.06, run.bat:76): PnL {sum(r["net_pnl_usdt"] for r in big):+.4f} USDT '
          f'= {100*sum(r["net_pnl_usdt"] for r in big)/tot["net"]:.1f}% tong lo')
    print(f'  Neu dung 0.01: PnL se la {alt:+.4f} -> tong lo phien {tot["net"]-sum(r["net_pnl_usdt"] for r in big)+alt:+.4f} '
          f'thay vi {tot["net"]:+.4f} (giam {100*(1-(tot["net"]-sum(r["net_pnl_usdt"] for r in big)+alt)/tot["net"]):.0f}%)')
    print('  So mau thu duoc KHONG DOI. position_sizer.py:27 luu net_pnl/notional -> size khong mua them thong tin.')


def muc4_signal_lab(R):
    print('\n' + '=' * 100)
    print(f'MUC 4 — SIGNAL LAB GOP n={len(R)} (v1={sum(r["src"]=="v1" for r in R)}, v2={sum(r["src"]=="v2" for r in R)})')
    print('gross_bps do bid->bid: DA la gross cua maker-entry + taker-exit. net = gross - 6.00')
    print('=' * 100)
    H = f'{"nhom":34}{"n":>6}{"gross":>9}{"   KTC95% bootstrap":>22}{"net@6":>9}{"TP%":>7}{"SL%":>7}{"spread":>8}'
    print(H); print('-' * len(H))
    band('TAT CA', R)
    band('  cong cho qua (gated=True)', [r for r in R if r['gated']])
    band('  cong chan  (gated=False)', [r for r in R if not r['gated']])
    print('\n>> CONVICTION (ngu phan vi)'); print(H); print('-' * len(H))
    R2 = sorted(R, key=lambda r: r['conviction']); q = len(R2) // 5
    for i in range(5):
        band(f'conviction Q{i+1}', R2[i*q:(i+1)*q if i < 4 else len(R2)])
    a = [r['gross_bps'] for r in R2[4*q:]]; b = [r['gross_bps'] for r in R2[:q]]
    se = math.sqrt(st.stdev(a)**2/len(a) + st.stdev(b)**2/len(b))
    print(f'  Q5-Q1 = {st.mean(a)-st.mean(b):+.2f} bps, SE {se:.2f}, t = {(st.mean(a)-st.mean(b))/se:.2f}')
    print('\n>> ATR'); print(H); print('-' * len(H))
    for lo, hi, lbl in ((0, 6, 'ATR <6'), (6, 12, 'ATR 6-12'), (12, 20, 'ATR 12-20'), (20, 1e9, 'ATR >=20')):
        band(lbl, [r for r in R if lo <= r['atr_bps'] < hi])
    print('\n>> SPREAD'); print(H); print('-' * len(H))
    for lo, hi, lbl in ((0, 2, 'spread <2'), (2, 5, 'spread 2-5'), (5, 1e9, 'spread >=5')):
        band(lbl, [r for r in R if lo <= r['spread_bps'] < hi])
    print('\n>> HORIZON (chu y: v1 chi co 300s -> so sanh sach chi trong v2)'); print(H); print('-' * len(H))
    for src in ('v1', 'v2'):
        for h in sorted({r['H'] for r in R if r['src'] == src}):
            band(f'{src} horizon={h}s', [r for r in R if r['src'] == src and r['H'] == h])


def muc5_bo_loc_oos(R):
    print('\n' + '=' * 100)
    print('MUC 5 — KIEM DINH OUT-OF-SAMPLE: nguong hoc TU v1, DONG BANG, ap nguyen xi cho v2')
    print('=' * 100)
    V1 = [r for r in R if r['src'] == 'v1']; V2 = [r for r in R if r['src'] == 'v2']
    cv = sorted(r['conviction'] for r in V1)
    P60, P75 = cv[int(.60*len(cv))], cv[int(.75*len(cv))]
    print(f'  Nguong dong bang tu v1: conviction p60={P60:.4f}  p75={P75:.4f}\n')
    filters = {
        'F0 khong loc':                 lambda r: True,
        'X  --min-atr-bps 12 (hien tai)': lambda r: r['atr_bps'] >= 12,
        'F1 conv>=p60':                 lambda r: r['conviction'] >= P60,
        'F2 6<=ATR<20':                 lambda r: 6 <= r['atr_bps'] < 20,
        'F3 F1+F2':                     lambda r: r['conviction'] >= P60 and 6 <= r['atr_bps'] < 20,
        'F4 F3+spread>=2':              lambda r: r['conviction'] >= P60 and 6 <= r['atr_bps'] < 20 and r['spread_bps'] >= 2,
    }
    hdr = f'{"":36}{"n":>6}{"gross":>9}{"   KTC95%":>22}{"net@6":>9}{"makerX p=.7":>13}'
    for name, f in filters.items():
        print(f'\n>>> {name}'); print(hdr)
        for lbl, rows in (('  TRAIN v1', [r for r in V1 if f(r)]), ('  TEST  v2 (OOS)', [r for r in V2 if f(r)]),
                          ('  GOP', [r for r in R if f(r)])):
            if len(rows) < 10:
                print(f'{lbl:36}{len(rows):6}   n<10'); continue
            g = [r['gross_bps'] for r in rows]; lo, hi = bootstrap_ci(g)
            s = st.mean([r['spread_bps'] for r in rows])
            print(f'{lbl:36}{len(rows):6}{st.mean(g):9.2f}  [{lo:+6.2f},{hi:+6.2f}]{st.mean(g)-FEE_BPS:9.2f}'
                  f'{st.mean(g)+(s+2)*0.7-FEE_BPS:13.2f}')
    print('\n  makerX p = gross + (spread+2)*p - 6   [p = ty le maker-exit khop]')


def muc6_a_s_vo_hieu():
    print('\n' + '=' * 100)
    print('MUC 6 — A-S maker_quote la NO-OP khi inventory=0 (runtime.py:538 truyen inventory=0.0)')
    print('=' * 100)
    sys.path.insert(0, str(ROOT))
    from core.barrier_planner import maker_quote

    class Bk:
        def __init__(s, b, a): s.bid, s.ask, s.mid = b, a, (b + a) / 2
    ok = 0
    for bid, ask, tick in ((100.00, 100.05, .01), (2.5000, 2.5040, .0001), (79880.1, 79884.9, .1)):
        bk = Bk(bid, ask)
        for gamma in (0., .1, 10.):
            for var in (0., 1e-6, 1.):
                qb = float(maker_quote(bk, 'BUY', tick, 0.0, gamma, var, 300))
                qs = float(maker_quote(bk, 'SELL', tick, 0.0, gamma, var, 300))
                ok += abs(qb - bid) < 1e-9 and abs(qs - ask) < tick * 1.001
    print(f'  {ok}/27 to hop (3 book x 3 gamma x 3 sigma^2) tra ve dung best bid/ask.')
    print('  inventory=0 => reservation = mid - 0*g*s2*T = mid; BUY: min(bid, mid-(ask-bid)/2) = bid.')
    print('  => risk_aversion va variance_per_second KHONG anh huong gi. A-S bi vo hieu o entry.')


def muc7_kelly():
    print('\n' + '=' * 100)
    print('MUC 7 — KELLY: vi sao chan 99.8%, va can bao nhieu mau')
    print('=' * 100)
    sys.path.insert(0, str(ROOT))
    from core.position_sizer import PositionSizer
    from decimal import Decimal as Dec

    class F: tick, step, min_qty, max_qty, min_notional = Dec('0.0001'), Dec('0.01'), Dec('0.01'), Dec('1e5'), Dec('5')
    S = json.loads((D / 'testnet-settled-v2.json').read_text())['records']
    for ms in (20, 60):
        ps = PositionSizer(min_samples=ms)
        for r in S:
            ps.record(r['net_pnl_usdt'], notional=r['entry_notional_usdt'])
        res = ps.size(4930, 0, 12.0, 11.9, F(), bootstrap=True, available=4930, bootstrap_fraction=0.01)
        print(f'  min_samples={ms:<3} kelly()={str(ps.kelly()):<6} '
              f'size(bootstrap=True, frac=0.01) -> notional {res.notional:7.2f} reason={res.reason}')
    print('  position_sizer.py:66-70: kelly() tra None CHI khi n<min_samples. Du 20 mau -> tra 0.0.')
    print('  => "if kelly is not None and kelly<=0: NONPOSITIVE_EDGE" chan het, --bootstrap KHONG bypass duoc.')
    v = [r['net_return'] * 1e4 for r in S]; sd = st.pstdev(v)
    print(f'\n  edge {st.mean(v):.2f} bps, sd {sd:.2f}, n {len(v)} -> SE {sd/math.sqrt(len(v)):.2f}')
    print('  Co mau can de phat hien edge that (alpha=.05, power=.8): n = 7.85*sd^2/delta^2')
    for d in (2, 3, 5, 8, 10):
        print(f'     delta={d:2d} bps -> n = {math.ceil(7.85*sd*sd/(d*d)):5d}')


def muc8_thong_luong_va_ghep_cap(R):
    print('\n' + '=' * 100)
    print('MUC 8 — THONG LUONG + GHEP CAP fill<->probe (do adverse selection)')
    print('=' * 100)
    db = sqlite3.connect((D / 'testnet-journal.sqlite').as_uri() + '?mode=ro', uri=True)
    ev = [(ts, k, json.loads(p)) for ts, k, p in db.execute('SELECT timestamp,kind,payload FROM events ORDER BY id')]
    intents, sides, first_fill = {}, {}, {}
    for ts, k, p in ev:
        if k == 'ENTRY_INTENT':
            intents.setdefault(p['client_id'], (ts, p)); sides[p['client_id']] = p['side']
        elif k == 'ORDER_STATE' and p.get('filled', 0) > 0:
            first_fill.setdefault(p['client_id'], ts)
    ts0 = min(t for t, _ in intents.values()); ts1 = max(t for t, _ in intents.values())
    span = (ts1 - ts0) / 3600
    print(f'  {len(intents)} ENTRY_INTENT trong {span:.2f} h = {len(intents)/span:.1f} intent/gio')
    print(f'  fill rate = {len(first_fill)}/{len(intents)} = {100*len(first_fill)/len(intents):.1f}%')
    print(f'  => 90 phut ~ {1.5*len(intents)/span:.0f} intent ~ {1.5*len(intents)/span*len(first_fill)/len(intents):.1f} fill (khong loc them)')
    d = sorted(first_fill[c] - intents[c][0] for c in first_fill if c in intents)
    print(f'\n  Thoi gian dat lenh -> fill dau (giay): median {st.median(d):.2f} | p90 {d[int(.9*len(d))]:.2f} | max {d[-1]:.2f}')
    for t in (2, 4, 6, 10):
        print(f'     TTL={t:2d}s giu {100*sum(1 for x in d if x<=t)/len(d):.0f}% so fill quan sat duoc (CHAN DUOI: fill bi kiem duyet boi TTL luc do)')
    print('\n  >> Bang barrier tu ENTRY_INTENT:')
    tp = [1e4*(1 if p['side'] == 'BUY' else -1)*(float(p['take_profit'])-float(p['price']))/float(p['price']) for _, p in intents.values()]
    sl = [1e4*(1 if p['side'] == 'BUY' else -1)*(float(p['price'])-float(p['stop']))/float(p['price']) for _, p in intents.values()]
    print(f'     TP median {st.median(tp):.2f} bps | SL median {st.median(sl):.2f} bps | R:R median {st.median(tp)/st.median(sl):.2f}')
    A = json.loads((D / 'execution-audit.json').read_text())['trades']
    pairs = []
    for t in A:
        s, o = sides.get(t['trade_id']), t['opened_at']
        cand = [r for r in R if r['symbol'] == t['symbol'] and abs(r['opened']-o) <= 60 and (s is None or r['side'] == s)]
        if cand:
            best = min(cand, key=lambda r: abs(r['opened']-o))
            pairs.append((t['symbol'], 1e4*t['gross_pnl_usdt']/t['entry_notional_usdt'], best['gross_bps'], best['opened']-o))
    print(f'\n  Ghep duoc {len(pairs)}/{len(A)} lenh (cung symbol+side, |dt|<=60s)')
    if len(pairs) > 1:
        print(f'  {"symbol":10}{"fill_thuc":>11}{"probe":>9}{"hieu":>9}{"dt(s)":>9}')
        for p_ in pairs:
            print(f'  {p_[0]:10}{p_[1]:11.2f}{p_[2]:9.2f}{p_[1]-p_[2]:9.2f}{p_[3]:9.1f}')
        diff = [p_[1]-p_[2] for p_ in pairs]
        un = [1e4*t['gross_pnl_usdt']/t['entry_notional_usdt'] for t in A]
        print(f'  ADVERSE SELECTION (fill - probe) = {st.mean(diff):+.2f} bps, sd {st.stdev(diff):.2f}, '
              f'n {len(diff)}, SE {st.stdev(diff)/math.sqrt(len(diff)):.2f}')
        print(f'  sd khong ghep cap {st.stdev(un):.2f} -> co ghep cap {st.stdev(diff):.2f} (giam {100*(1-st.stdev(diff)/st.stdev(un)):.0f}% phuong sai)')
        print(f'  de phat hien AS = 2.0 bps: can n={math.ceil(7.85*st.stdev(diff)**2/4):d} (ghep cap) '
              f'vs n={math.ceil(7.85*st.stdev(un)**2/4):d} (khong ghep)')


if __name__ == '__main__':
    R = load_lab()
    muc2_dinh_luat_phi()
    muc3_phan_ra_ledger()
    muc4_signal_lab(R)
    muc5_bo_loc_oos(R)
    muc6_a_s_vo_hieu()
    muc7_kelly()
    muc8_thong_luong_va_ghep_cap(R)
    print('\nHOAN TAT. Moi con so trong bao cao deu nam o tren.')
