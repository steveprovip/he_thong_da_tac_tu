"""DeFi: CEX-DEX and DEX-DEX price gaps on Uniswap v3 style pools.

Correction to the usual pitch: a CEX-DEX trade is NOT atomic. The CEX leg is
off-chain, so between the two fills you carry inventory, latency and
inclusion risk. Only DEX-DEX (both legs in one transaction, optionally funded
by a flash loan) is atomic -- and that space is competed for by MEV searchers
who pay most of the profit to block builders.

This module is pure math: single-tick Uniswap v3 swap output, and a profit
calculator that charges LP fee, CEX taker fee, gas, flash-loan premium and a
builder tip. No contract deployment, no signing, no RPC.
"""
import math

Q96 = 2 ** 96


def v3_amount_out(amount_in, sqrt_price_x96, liquidity, fee_tier, zero_for_one):
    """Output of a swap that stays inside the current tick (exact v3 math).

    Returns (amount_out, new_sqrt_price_x96). Raises if the trade would move
    the price by more than 10% (a tick crossing is then almost certain and
    this single-range formula no longer applies)."""
    if min(amount_in, sqrt_price_x96, liquidity) <= 0 or not 0 <= fee_tier < 1:
        raise ValueError('invalid pool state')
    sp = sqrt_price_x96 / Q96
    L = float(liquidity)
    dx = amount_in * (1 - fee_tier)
    if zero_for_one:
        new_sp = L * sp / (L + dx * sp)
        out = L * (sp - new_sp)
    else:
        new_sp = sp + dx / L
        out = L * (1 / sp - 1 / new_sp)
    if abs(new_sp / sp - 1) > .05:           # sqrt moves 5% ~ price moves ~10%
        raise ValueError('trade crosses ticks; needs tick-level liquidity data')
    return out, new_sp * Q96


def gap_trade(dex_price, cex_price, notional_usd, lp_fee=.0005, cex_taker=.0005, gas_usd=2.0,
              builder_tip_share=.0, flash_premium=.0, atomic=False):
    """Profit of buying on the cheap venue and selling on the rich one."""
    if min(dex_price, cex_price, notional_usd) <= 0:
        raise ValueError('invalid prices')
    gross_bps = abs(dex_price - cex_price) / min(dex_price, cex_price) * 1e4
    cost_bps = (lp_fee + (0 if atomic else cex_taker) + flash_premium) * 1e4 + gas_usd / notional_usd * 1e4
    net_before_tip = gross_bps - cost_bps
    tip_bps = max(0.0, net_before_tip) * builder_tip_share
    cost_bps += tip_bps
    ratio = gross_bps / cost_bps if cost_bps > 0 else math.inf
    return {'gross_bps': gross_bps, 'cost_bps': cost_bps, 'net_bps': gross_bps - cost_bps,
            'ratio': ratio, 'atomic': atomic,
            'executable': gross_bps - cost_bps > 0 and ratio > 3,
            'risk': 'none beyond revert gas' if atomic else 'inventory + latency between legs'}


def breakeven_notional(gross_bps, lp_fee=.0005, cex_taker=.0005, gas_usd=2.0, atomic=False):
    """Smallest notional at which gas stops eating the gap (G = 3C)."""
    fee_bps = (lp_fee + (0 if atomic else cex_taker)) * 1e4
    budget = gross_bps / 3 - fee_bps
    if budget <= 0:
        return math.inf
    return gas_usd / budget * 1e4
