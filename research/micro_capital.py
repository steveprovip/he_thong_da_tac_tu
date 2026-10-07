"""Micro-capital channel calculator (3.85 USDT ~ 100k VND). Pure math, no network.

    python research/micro_capital.py [--capital 3.85] [--months 12] [--json]

For each channel:
    gross(H)   = K x APR x H / 12
    cost(H)    = K x trade_cost_bps / 1e4  +  F_in + F_out          (one round trip)
    net/month  = (gross(H) - cost(H)) / H
    breakeven  H* = (K x trade_cost_bps/1e4 + F_in + F_out) / (K x APR / 12)
A channel is ADMISSIBLE only if: legal for a Vietnamese resident (as far as
public sources show), K >= the venue's minimum to open the position,
F_in + F_out <= 0.2 USDT (directive rule), and net/month > 0.

Every rate below is an INPUT, dated and sourced in docs/VON_NHO_100K_2026.md;
APRs and withdrawal fees change -- re-check them in the app before acting.
"""
from dataclasses import dataclass, replace
import argparse
import json
import math

MAX_TRANSFER_OVERHEAD_USDT = 0.2
VND_PER_USDT = 100_000 / 3.85


@dataclass(frozen=True)
class Channel:
    key: str
    name: str
    apr: float                 # gross annual yield on deployed capital
    trade_cost_bps: float      # round-trip trading cost on deployed capital
    fee_in_usdt: float         # withdraw/gas to get the capital into the channel
    fee_out_usdt: float        # ... and back out
    min_capital_usdt: float    # smallest position the venue(s) accept
    legal_vn: bool             # False = public authority action against it in Vietnam
    risk: str


CHANNELS = (
    Channel('POLYMARKET_NEGRISK', 'Polymarket negRisk Dutch book (Polygon)', apr=0.0, trade_cost_bps=0.0,
            fee_in_usdt=0.01, fee_out_usdt=0.01, min_capital_usdt=3.0, legal_vn=False,
            risk='Bo Cong an (Cuc A05) xac dinh la nen tang co bac, truy cap bi chan tai VN; '
                 'co hoi sum(ask)<0.98 bi bot an trong vai giay; tien khoa toi khi resolve'),
    Channel('BYBIT_CARRY', 'Bybit spot long + USDT perp short (funding carry)', apr=1.0 * 3 * 365 / 1e4,
            trade_cost_bps=2 * 10 + 2 + 5.5 + 2, fee_in_usdt=0.1, fee_out_usdt=0.1,
            min_capital_usdt=5.0 + 5.0 / 2, legal_vn=True,   # spot leg 5 + perp margin 2.5 at 2x
            risk='Perp co min notional theo hop dong (vi du BTCUSDT 100 USDT; nhieu altcoin >= 5 USDT); '
                 'funding co the dao dau; hai chan tren mot san moi (rui ro san)'),
    Channel('DEFI_SOLANA_LEND', 'Cho vay USDC tren Solana (Kamino/marginfi)', apr=0.05, trade_cost_bps=0.0,
            fee_in_usdt=0.01 + 0.25, fee_out_usdt=0.05, min_capital_usdt=0.0, legal_vn=True,
            risk='Rui ro smart contract/oracle; can SOL lam phi gas + rent tai khoan token (~0.002 SOL)'),
    Channel('BINANCE_SIMPLE_EARN', 'Binance Simple Earn USDT Flexible (tien da o Binance)', apr=0.02,
            trade_cost_bps=0.0, fee_in_usdt=0.0, fee_out_usdt=0.0, min_capital_usdt=0.1, legal_vn=True,
            risk='Rui ro doi tac (san); APR bien dong hang ngay; khong phai tien gui ngan hang'),
)


def evaluate(channel, capital, months=12.0):
    if not all(map(math.isfinite, (capital, months))) or capital < 0 or months <= 0:
        raise ValueError('capital >= 0 and months > 0 required')
    gross = capital * channel.apr * months / 12
    fixed = capital * channel.trade_cost_bps / 1e4 + channel.fee_in_usdt + channel.fee_out_usdt
    net_month = (gross - fixed) / months
    monthly_gross = capital * channel.apr / 12
    breakeven = fixed / monthly_gross if monthly_gross > 0 else math.inf
    reasons = []
    if not channel.legal_vn:
        reasons.append('ILLEGAL_IN_VN')
    if capital < channel.min_capital_usdt:
        reasons.append(f'CAPITAL {capital:.2f} < MIN {channel.min_capital_usdt:.2f}')
    overhead = channel.fee_in_usdt + channel.fee_out_usdt
    if overhead > MAX_TRANSFER_OVERHEAD_USDT + 1e-12:
        reasons.append(f'TRANSFER {overhead:.2f} > {MAX_TRANSFER_OVERHEAD_USDT}')
    if net_month <= 0:
        reasons.append('NET<=0')
    return {'key': channel.key, 'name': channel.name, 'capital': capital, 'months': months,
            'gross_usdt': gross, 'cost_usdt': fixed, 'net_per_month_usdt': net_month,
            'net_per_month_vnd': net_month * VND_PER_USDT, 'breakeven_months': breakeven,
            'admissible': not reasons, 'reasons': reasons, 'risk': channel.risk}


def rank(capital, months=12.0, channels=CHANNELS):
    rows = [evaluate(c, capital, months) for c in channels]
    rows.sort(key=lambda r: (not r['admissible'], -r['net_per_month_usdt']))
    return rows


def winner(capital, months=12.0, channels=CHANNELS):
    best = rank(capital, months, channels)[0]
    return best if best['admissible'] else None


def capital_needed(channel, target_net_month, months=12.0):
    """Smallest K with net/month >= target (inf if the channel can never get there)."""
    per_k = channel.apr / 12 - channel.trade_cost_bps / 1e4 / months
    if per_k <= 0:
        return math.inf
    k = (target_net_month + (channel.fee_in_usdt + channel.fee_out_usdt) / months) / per_k
    return max(k, channel.min_capital_usdt)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--capital', type=float, default=3.85)
    ap.add_argument('--months', type=float, default=12.0)
    ap.add_argument('--earn-apr', type=float, help='override Simple Earn APR (e.g. 0.02)')
    ap.add_argument('--defi-apr', type=float, help='override Solana lending APR')
    ap.add_argument('--funding-bps-8h', type=float, help='override Bybit carry funding (bps per 8h)')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    chans = []
    for c in CHANNELS:
        if c.key == 'BINANCE_SIMPLE_EARN' and a.earn_apr is not None:
            c = replace(c, apr=a.earn_apr)
        if c.key == 'DEFI_SOLANA_LEND' and a.defi_apr is not None:
            c = replace(c, apr=a.defi_apr)
        if c.key == 'BYBIT_CARRY' and a.funding_bps_8h is not None:
            c = replace(c, apr=a.funding_bps_8h * 3 * 365 / 1e4)
        chans.append(c)
    rows = rank(a.capital, a.months, chans)
    if a.json:
        print(json.dumps(rows, indent=1, default=str))
        return 0
    print(f'Von {a.capital:.2f} USDT (~{a.capital * VND_PER_USDT:,.0f} VND), giu {a.months:g} thang')
    for r in rows:
        be = 'khong bao gio' if math.isinf(r['breakeven_months']) else f'{r["breakeven_months"]:.1f} thang'
        print(f'  {"OK " if r["admissible"] else "-- "} {r["key"]:20} net {r["net_per_month_usdt"]:+.4f} USDT/thang '
              f'(~{r["net_per_month_vnd"]:+,.0f} VND) · hoa von {be} · {", ".join(r["reasons"]) or "hop le"}')
    w = rows[0] if rows[0]['admissible'] else None
    print('KENH CHON: ' + (w['name'] if w else 'KHONG CO kenh nao hop le voi so von nay'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
