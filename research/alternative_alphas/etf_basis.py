r"""TradFi: S&P 500 ETF (SPY/VOO) vs E-mini / Micro E-mini futures basis.

Cost-of-carry fair value:  F* = S * exp((r - q) * T)
Cash-and-carry (F > F*): short future, long ETF basket, hold to expiry.
Reverse (F < F*): long future, short ETF (borrow fee applies).

Why this is institutional, not retail:
  * The basis is arbitraged by index desks with sub-bp financing; the
    mispricing a retail account sees is usually inside its own financing +
    commission + ETF tracking band.
  * Size: ES = 50 x index, MES = 5 x index. At an index of ~6000 one MES is
    ~30k USD of notional per leg; this book holds 3.85 USDT.
ai-hedge-fund integration: its agents emit {'signal': bullish|bearish|neutral,
'confidence': 0..100} (F:\JEV_AI\ai-hedge-fund\hedge_fund\llm\contract.py, which
also maps Jev direction+strength answers onto that payload);
`verdict_from_agent_signals` turns them into a
block-only System-2 verdict (phan_tich.slow_brain_contract schema v2).
"""
import math

CONTRACT_MULTIPLIER = {'ES': 50.0, 'MES': 5.0}


def fair_future(spot, rate, dividend_yield, years):
    if not all(map(math.isfinite, (spot, rate, dividend_yield, years))) or spot <= 0 or years < 0:
        raise ValueError('invalid carry inputs')
    return spot * math.exp((rate - dividend_yield) * years)


def basis_trade(spot, future, rate, dividend_yield, years, contract='MES', contracts=1,
                future_fee_usd=1.5, etf_fee_bps=1.0, spread_bps=1.0, borrow_bps_year=30.0,
                capital_usd=0.0):
    """Price a one-contract basis trade; `executable` only when gross > 3 x cost."""
    fair = fair_future(spot, rate, dividend_yield, years)
    mult = CONTRACT_MULTIPLIER[contract]
    notional = spot * mult * contracts
    mis_bps = (future - fair) / spot * 1e4
    direction = 'CASH_AND_CARRY' if mis_bps > 0 else 'REVERSE'
    cost_bps = (2 * future_fee_usd * contracts / notional * 1e4 + 2 * etf_fee_bps + 2 * spread_bps
                + (borrow_bps_year * years if direction == 'REVERSE' else 0.0))
    gross = abs(mis_bps)
    reasons = []
    if capital_usd < notional:
        reasons.append(f'CAPITAL {capital_usd:.2f} < leg notional {notional:.0f} USD')
    if gross <= 3 * cost_bps:
        reasons.append(f'G/C {gross / cost_bps:.2f} <= 3')
    return {'fair': fair, 'mispricing_bps': mis_bps, 'direction': direction, 'notional_usd': notional,
            'gross_bps': gross, 'cost_bps': cost_bps, 'net_bps': gross - cost_bps,
            'ratio': gross / cost_bps, 'executable': not reasons, 'reason': '; '.join(reasons) or 'ELIGIBLE'}


def verdict_from_agent_signals(signals, symbol, as_of_ts, generated_at, ttl_s=3600.0, min_agreement=.6):
    """ai-hedge-fund agent signals -> schema-v2 verdict (can only BLOCK downstream)."""
    score, weight = 0.0, 0.0
    for s in signals.values():
        conf = float(s.get('confidence', 0)) / 100.0
        sig = str(s.get('signal', 'neutral')).lower()
        if not 0 <= conf <= 1:
            continue
        score += conf * (1 if sig == 'bullish' else -1 if sig == 'bearish' else 0)
        weight += conf
    agreement = abs(score) / weight if weight else 0.0
    regime = 'TREND' if agreement >= min_agreement else 'CHOP'
    as_of, gen = float(as_of_ts), float(generated_at)
    return {'schema_version': 2, 'symbol': symbol, 'regime': regime, 'veto': weight == 0,
            'conviction': round(agreement, 4), 'bias': 'LONG' if score > 0 else 'SHORT' if score < 0 else 'FLAT',
            'as_of_ts': as_of, 'generated_at': gen, 'expires_at': gen + ttl_s, 'data_cutoff': as_of,
            'model_version': 'ai-hedge-fund', 'available_at': gen, 'published_at': gen,
            'reason': f'{len(signals)} agents, agreement {agreement:.2f}', 'timeframe': '1d',
            'max_source_age_s': ttl_s}
