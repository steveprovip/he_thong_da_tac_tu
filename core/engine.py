"""GROKTOPUS 2-mode engine - the single trading entry point.

    python -m core.engine --mode demo     Binance DEMO futures (demo-fapi) with BINANCE_TESTNET_* keys;
                                          no keys -> MOCK broker filled on live Binance prices.
    python -m core.engine --mode live     Binance MAINNET (fapi). Refuses to start unless the
                                          operator typed the arm phrase (run.bat live) AND the
                                          micro-capital gate passes: margin <= 3.85 U, loss at stop
                                          incl. fees <= 0.05 U, Post-Only GTX entries.

Loop: public mainnet WS (aggTrade, depth5@100ms, markPrice@1s, forceOrder) -> core.micro_signals
-> core.micro_swarm 8-agent vote (76.8 % / JEV >= 8.0) -> TokenAuthority (HMAC, 5 s, one use)
-> VESKA routes entry -> exchange STOP_MARKET + reduce-only GTX TP placed from the actual fill
-> MARIN time stop -> ledger data/engine_trades.jsonl. Telemetry + controls on :8765.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from urllib.parse import urlsplit

from config.settings import Settings, read_env
from core import micro_signals as msig
from core import exec_funnel as xf
from core import micro_swarm as swarm
from core.consensus_engine import ConsensusEngine, Decision, TokenAuthority
from live.radar_auto_trader import DemoVenue, ShadowVenue, fmt, floor_step, round_tick

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / 'data' / 'engine_trades.jsonl'
LEDGER_SCHEMA = 2                      # 2026-10-07q: + policy/code, basis, stage latency, markouts (additive)
MARKOUT_S = (1.0, 5.0, 30.0)           # post-fill markouts of the venue mid, signed with the trade (bps)


def code_version(root: Path = ROOT) -> Optional[str]:
    """Short commit of the checkout (read from .git, no subprocess); None when unknown. Dirty state not tracked."""
    try:
        head = (root / '.git' / 'HEAD').read_text(encoding='utf-8').strip()
        if not head.startswith('ref:'):
            return head[:10]
        ref = head.split(' ', 1)[1].strip()
        f = root / '.git' / ref
        if f.exists():
            return f.read_text(encoding='utf-8').strip()[:10]
        for line in (root / '.git' / 'packed-refs').read_text(encoding='utf-8').splitlines():
            if line.endswith(' ' + ref):
                return line.split(' ', 1)[0][:10]
    except OSError:
        pass
    return None


CODE_VERSION = code_version()


def policy_id(p) -> str:
    """Stable 10-hex id of the exact profile (every threshold/flag): rows of different configs never mix."""
    import hashlib
    return hashlib.sha1(json.dumps(asdict(p), sort_keys=True, default=str).encode()).hexdigest()[:10]


def basis_bps(signal_mid: Optional[float], venue_mid: Optional[float]) -> Optional[float]:
    """Execution venue mid vs signal feed mid (bps). Demo venue = USD-M testnet book, independent of mainnet."""
    if not signal_mid or not venue_mid:
        return None
    return round((venue_mid / signal_mid - 1.0) * 1e4, 3)


def entry_start_from_fills(fills, amt: float, step: float) -> Optional[int]:
    """fills = [(t_ms, signed_qty)] oldest first. Walk back from the newest fill until the signed sum equals the
    open position: -> time (ms) of the first fill that built it, None if the history does not reconcile."""
    cum, tol = 0.0, max(step, 1e-12) / 2
    for t, q in reversed(list(fills)):
        cum += q
        if abs(cum - amt) <= tol:
            return int(t)
        if (amt > 0 and cum > amt + tol) or (amt < 0 and cum < amt - tol):
            return None
    return None


# Canonical texts of Binance USD-M codes seen around orders/stops. Raw exchange messages are NOT logged (the
# client strips them on purpose); the meaning comes from the public error-code page (checked 2026-10-07).
BINANCE_CODES = {-2021: 'order would immediately trigger', -4130: 'closePosition stop already open in this direction',
                 -5022: 'post-only would take liquidity', -2011: 'cancel rejected / unknown order',
                 -2013: 'order does not exist', -4045: 'max stop order limit', -2022: 'reduce-only conflicts',
                 -4137: 'quantity must be 0 with closePosition', -4138: 'reduceOnly must be true with closePosition',
                 -1111: 'precision over the maximum', -4014: 'price not a multiple of tick', -1021: 'timestamp outside recvWindow',
                 'TRANSPORT': 'network/timeout (execution uncertain)', 'NON_JSON': 'non-JSON response'}


def err_txt(exc: BaseException) -> str:
    """Exception -> 'Type code=.. (meaning)'; no raw exchange text, request params or keys."""
    code = getattr(exc, 'code', None)
    if code is None:
        return f'{type(exc).__name__}: {str(exc)[:120]}'
    out = f'{type(exc).__name__} code={code}'
    if code in BINANCE_CODES:
        out += f' ({BINANCE_CODES[code]})'
    if getattr(exc, 'uncertain', False):
        out += ' uncertain'
    return out


async def kill_switch(engines) -> list:
    """Dashboard KILL (idempotent): tracked positions close through Engine.close('KILL') so the ledger records
    them; untracked venue positions are flattened; then every symbol is re-read and swept once more if not flat.
    -> [{'symbol', 'flat', 'amt'}] (also an event per symbol)."""
    report = []
    for x in engines:
        try:
            if x.pos:
                async with x.lock:
                    await x.close('KILL')
            else:
                await x.venue.flatten(x.p.symbol)                # also cancels stray entry / stop orders
        except Exception as exc:
            x.event(f'flatten error {err_txt(exc)}')
    for x in engines:
        amt = None
        try:
            amt = (await x.venue.position_view(x.p.symbol)).get('amt') or 0.0
            if amt:
                await x.venue.flatten(x.p.symbol)
                amt = (await x.venue.position_view(x.p.symbol)).get('amt') or 0.0
        except Exception as exc:
            x.event(f'KILL reconcile error {err_txt(exc)}')
        report.append({'symbol': x.p.symbol, 'flat': amt == 0.0, 'amt': amt})
        x.event(f'KILL RECONCILE {x.p.symbol}: ' + ('flat' if amt == 0.0 else f'NOT FLAT amt={amt}'))
    return report
SHUTDOWN_FILE = ROOT / 'data' / 'shutdown_request.json'
MODES = ('demo', 'live')
SYMBOLS = ('BTCUSDT', 'DOGEUSDT', 'SOLUSDT')
SYMBOL_QTY = {'BTCUSDT': 0.002}                 # others: smallest tradable size (min notional x 1.02)
SCAN_SYMBOLS = ('BTCUSDT', 'SOLUSDT', 'DOGEUSDT')   # demo multi-asset scanner: one slot, first setup wins
ASSET_BRACKETS = {'BTCUSDT': (0.15, 0.38), 'SOLUSDT': (0.35, 0.85), 'DOGEUSDT': (0.50, 1.25)}  # (SL min %, TP1 min %)
LIVE_CONFIRM = 'LIVE'
FALLBACK_SPECS = {
    'BTCUSDT': {'step': 0.001, 'min_qty': 0.001, 'min_notional': 100.0, 'tick': 0.1},
    'ETHUSDT': {'step': 0.001, 'min_qty': 0.001, 'min_notional': 20.0, 'tick': 0.01},
    'SOLUSDT': {'step': 0.01, 'min_qty': 0.01, 'min_notional': 5.0, 'tick': 0.01},
    'DOGEUSDT': {'step': 1.0, 'min_qty': 1.0, 'min_notional': 5.0, 'tick': 0.00001},
    'XRPUSDT': {'step': 0.1, 'min_qty': 0.1, 'min_notional': 5.0, 'tick': 0.0001},
}


@dataclass(frozen=True)
class Profile:
    mode: str
    symbol: str
    leverage: int = 5
    margin_type: str = 'CROSSED'
    entry: str = 'gtx'                  # Post-Only GTX only; MARKET is kept for emergency reduce-only exits
    gtx_ttl_s: float = 3.0              # pegged maker window tau <= 3 s; unfilled -> cancel, recompute, never taker
    qty: float = 0.0                    # 0 -> smallest tradable size (min_qty / min_notional x 1.02)
    tp_pct: float = 0.50                # static TP1 (ATR unknown): 2.5 x SL 0.20 -> RR 2.5 (was 0.45)
    tp1_frac: float = 0.5
    sl_pct: float = 0.20
    microbe_trigger_pct: float = 0.10   # MARIN phase 0: MFE +0.10 % -> stop to entry +0.04 % (= maker round trip)
    microbe_lock_pct: float = 0.04
    be_trigger_pct: float = 0.12        # MARIN phase 1: +0.12 % in favour -> stop to fee-true breakeven
    flow_exit: bool = True              # adverse CVD >= $2M (x symbol scale) in 15 s since entry -> exit
    flow_breath_s: float = 30.0         # no adverse-flow exit in the first 30 s (exchange hard SL still live)
    flow_exit_trigger_mult: float = 2.5 # flow limit = max($2M, 2.5 x the 15 s CVD that triggered the entry)
    flow_exit_cap_mult: float = 3.0     # ... capped at 3 x the $2M floor (never 'no exit at all')
    regime_flip_exit: bool = True       # SHORT vs CVD 60 s >= +$1M / LONG vs <= -$1M -> soft maker exit
    regime_flip_usd: float = 1_000_000.0
    regime_flip_min_hold_s: float = 60.0  # no regime-flip exit in the first 60 s (hard SL still live)
    regime_flip_debounce_s: float = 20.0  # the flip must hold continuously 20 s before MARIN exits
    ker_gate: bool = True               # CHOP LOCK: no AUTO entry while KER(2 min) < 0.40 (unlock >= 0.45)
    flow_exit_ttl_s: float = 1.0        # (hard mode only) maker attempt before market
    soft_exits: bool = True             # BE / trail / flow exits = post-only maker queue; only hard SL is taker
    exit_repeg_s: float = 0.5           # re-peg the maker exit when the touch moves away
    peg: bool = True                    # pegged maker entry when consensus >= peg_min_score
    peg_min_score: float = 0.768
    peg_interval_s: float = 0.5
    peg_max_drift_pct: float = 0.15     # never chase further than 0.15 % from the first quote
    aggressive_quote: bool = True       # wide spread: BUY at ask - 1 tick / SELL at bid + 1 tick (still post-only)
    unfilled_cooldown_s: float = 1.0
    super_edge: bool = False            # IOC taker entry on super-momentum (OFF since 2026-10-07p: pure maker)
    super_consensus: float = 0.85       # raw swarm consensus (not the JEV-lifted score)
    super_jev: float = 9.2
    super_cvd_usd: float = 3_000_000.0  # CVD 10 s IN the trade direction, x symbol usd_scale
    super_ker: float = 0.60             # IOC also needs KER(2 min) >= 0.60 (straight, steep tape)
    ioc_max_slip_pct: float = 0.03      # IOC limit = touch +/- 0.03 %: bounded taker, never a blind MARKET
    be_offset_pct: float = 0.02         # breakeven stop = entry + all fees + slip + 0.02 %
    fee_shield_pct: float = 0.04        # T2 at +0.12 %: exit level >= entry + 0.04 % (fee-true BE when higher)
    lock_trigger_pct: float = 0.25      # T2b PROFIT LOCK: MFE >= +0.25 % ...
    lock_pct: float = 0.15              # ... -> exit level entry + 0.15 %
    absorb_gate: bool = True            # block entries into an ABSORPTION TRAP (flow without price)
    concordance_gate: bool = True       # TREND entries need CVD 15 s + CVD 60 s + OFI L10 all with the trade
    concordance_mr: bool = False        # also on the MR lane (would disable it: MR buys against the last minute)
    drift_lock: bool = True             # regime DRIFT (60 s / 3 m not aligned): no AUTO entry at all
    slot_yield: bool = True             # scanner: a better-qualified symbol waiting for the slot goes first
    hurdle_concordance_override: bool = False   # REGIME/FLOW exit skips the fee hurdle when all 3 flows oppose
    multi_slot: bool = True             # scanner: one position PER SYMBOL (not one for the whole desk) ...
    max_positions: int = 3              # ... at most 3 at once ...
    portfolio_risk_usdt: float = 1.0    # ... money at all stops (fees included) <= 1.0 U ...
    portfolio_margin_frac: float = 0.20  # ... and margin in use <= 20 % of the wallet
    convex_sizing: bool = False         # score >= 0.90 + OFI >= 0.65 + CVD 60 s >= $1M -> 1.5x (OFF 10-07q: the
                                        # swarm score is not a calibrated probability; no out-of-sample evidence)
    convex_score: float = 0.90
    convex_ofi: float = 0.65
    convex_cvd60_usd: float = 1_000_000.0
    convex_mult: float = 1.5
    convex_top_score: float = 0.98      # ... >= 0.98 -> 2.0x
    convex_top_mult: float = 2.0
    ratchet: bool = True                # MFE >= +0.15 % -> exchange stop moves to entry +/- 0.08 %
    ratchet_trigger_pct: float = 0.15
    ratchet_lock_pct: float = 0.08
    adopt_external: bool = True         # demo: a position left on the venue (restart) is re-adopted by MARIN
    asset_brackets: bool = True         # per-coin SL / TP1 floors (ASSET_BRACKETS) under the ATR scaler
    risk_parity: bool = True            # coins without a fixed size: qty = risk_target / (price x (SL + fees))
    risk_target_usdt: float = 0.15      # -> ~0.30 SOL at SL 0.35 % (BTC keeps its exchange minimum 0.002)
    ratchet_retry_s: float = 5.0        # a failed exchange-stop move is retried after 5 s, not every tick
    eject: bool = True                  # time-decay: open >= eject_s with MFE < eject_mfe_pct -> soft exit at BE
    eject_s: float = 180.0
    eject_mfe_pct: float = 0.06
    eject_hard_s: float = 360.0         # BE order still unfilled at 360 s -> re-peg at the touch (thesis is dead)
    alt_boost: bool = True              # SOL/DOGE at swarm vote >= 0.90 -> size base..max of ALT_SIZE (Kelly-scaled)
    alt_boost_score: float = 0.90
    alt_size_boost: bool = False        # the vote >= 90 % SIZE lift itself (OFF 10-07q, same reason as convex);
                                        # alt_boost keeps the BTC 15 % risk share and the BTC probe rule
    order_log: bool = True              # every entry attempt -> engine_orders.jsonl (fill / cancel / reject stats)
    soft_exit_escalate: bool = False    # bounded chase for protective maker exits (SOFT-* / FLOW / REGIME):
    soft_exit_deadline_s: float = 5.0   # ... unfilled 5 s while price is at/past the exit reference,
    soft_exit_max_slip_pct: float = 0.03  # ... or 0.03 % past it -> reduce-only MARKET exit. OFF = SHADOW: the
                                        # trigger is only recorded (replay 10-08: every bound cost 0.1-1.3 bps/exit)
    ratchet_max_fail: int = 3           # exchange-stop moves: back-off doubles per failure, stop after 3 per trade
    mr_flow_gate: bool = True           # 10-08s (#39): no MR BUY while OFI L10 < -0.30 or CVD 60 s < -$100k x scale,
    mr_ofi_max: float = 0.30            # no MR SELL while OFI > +0.30 or CVD 60 s > +$100k x scale (no fading flow)
    mr_cvd60_usd: float = 100_000.0
    net_rr_min: float = 1.30            # fee-true reward:risk of the bracket (bracket_plan rr_net) must be >= 1.30
    super_size: bool = True             # score >= 0.95 AND OFI >= 0.80 with the trade -> SUPER_SIZE (SOL/DOGE, demo)
    super_score: float = 0.95
    super_ofi: float = 0.80
    chop_scalper: str = 'shadow'        # 10-08t chop micro-scalper: 'off' | 'shadow' (log would-be scalps, no
                                        # orders) | 'on'. Replay: -6.3 to -7.5 bps net per scalp (in-sample and
                                        # holdout, CI < 0) -> shadow by default
    scalp_ker_max: float = 0.40         # scalp only while KER(2 min) < 0.40 ...
    scalp_spread_max_bps: float = 1.5   # ... and the signal-feed spread <= 1.5 bps
    scalp_ofi_min: float = 0.30         # side = sign of OFI L10 (|OFI| >= 0.30), no OFI -> no scalp
    scalp_tp_pct: float = 0.08          # resting maker TP (2 x maker round trip)
    scalp_sl_pct: float = 0.12          # exchange stop
    scalp_be_s: float = 90.0            # TP not filled after 90 s -> maker exit at entry + fees (EJECT-BE)
    scalp_size_mult: float = 0.5
    scalp_every_s: float = 20.0         # at most one scalp attempt / shadow record per symbol per 20 s
    drift_accel: bool = True            # DRIFT lock lifted for the CVD 60 s side when |CVD60| >= 3 x |CVD3m| and
    drift_accel_ratio: float = 3.0      # |CVD60| >= $2M x scale (fresh flow overrides the stale 3 m history) ...
    drift_accel_min_usd: float = 2_000_000.0
    drift_probe_mult: float = 0.5       # ... at half size (probe)
    btc_probe_min_tradable: bool = True  # BTC probe = smallest size the venue accepts if it fits the 15 % budget
    observe_only: tuple = ('BTCUSDT',)  # 10-08u: feed + telemetry only, no entries (BTC 28 trades, AUTO net -1.43 U)
    alt_size_floor: bool = True         # SOL >= 0.40 / DOGE >= 300 on normal trend entries (ALT_SIZE_FLOOR)
    eject_fast: bool = True             # >= 120 s, MFE < 0.08 % and |15 s velocity| < 0.025 % -> maker exit at touch
    eject_fast_s: float = 120.0
    eject_fast_mfe_pct: float = 0.08
    eject_fast_vel_pct: float = 0.025
    alt_risk_share: float = 0.85        # 85 % of portfolio_risk_usdt for SOL/DOGE: BTC may use only 15 % (0.15 U)
    alt_kelly_window: int = 15          # rolling Kelly over this coin's last 15 settled trades ...
    alt_kelly_min_n: int = 20           # ... needs >= 20 of them (was 5: 7 SOL trades sized 0.45 -> 1.06 SOL)
    alt_kelly_full: float = 0.10        # quarter-Kelly f >= 0.10 -> the boost reaches ALT_SIZE max
    btc_probe_qty: float = 0.0001       # BTC outside a clean high-VFR tape, or over its 15 % budget: probe size only
    probe_refuse_cooldown_s: float = 30.0   # probe below the venue minimum -> BTC rests 30 s before re-asking
    vfr_gate: bool = True               # VFR = ATR 15 m (bps) / (maker round trip + spread) < vfr_min -> FROZEN
    vfr_min: float = 3.0
    vfr_full: float = 5.0               # BTC below VFR 5: micro-probe size only
    tp_fee_mult: float = 3.0            # gross TP1 >= 3 x round-trip fee (maker 2 + 2 bps -> >= 0.12 %)
    gtx_backoff_ticks: int = 2          # post-only reject (-5022): re-quote 1 tick further from the touch, <= 2 times
    fee_hurdle: bool = True             # FLOW / REGIME exits only outside -0.08 % < gross < +0.08 %
    hurdle_pct: float = 0.08
    hurdle_min_hold_s: float = 90.0     # and never in the first 90 s while gross >= 0
    min_velocity_pct: float = 0.025     # TREND entry needs price velocity 15 s >= +0.025 % with the trade (was 0.04)
    mr_tp_pct: float = 0.15             # MR lane (KER < 0.38): TP = distance back to VWAP 60 s ...
    mr_tp_min: float = 0.12             # ... clamped to [0.12 %, 0.18 %], whole size
    mr_tp_max: float = 0.18
    mr_sl_pct: float = 0.08             # ... exchange stop -0.08 % (was 0.10)
    mr_min_score: float = 0.80          # MR lane: NORO (MR) + TIDAL (L10 DEPTH) agree
    as_gamma: float = 0.10              # Avellaneda-Stoikov risk aversion (1/bps) for the MR maker quote
    as_k: float = 2.0                   # order-arrival decay (1/bps): liquidity term (2/g) ln(1 + g/k)
    as_max_offset_bps: float = 2.0      # MR quote never rests more than 2 bps from mid
    as_tau_s: float = 5.0               # quoting horizon tau (s) in gamma sigma^2 tau
    atr_scaler: bool = True             # TREND brackets from Wilder ATR(14) of 1 m bars when known
    atr_sl_mult: float = 1.0            # SL  = entry -/+ 1.0 x ATR (floored)
    atr_tp1_mult: float = 2.5           # TP1 = entry +/- max(2.5 x ATR, rr_min x SL), 50 % of the size
    atr_trail_mult: float = 1.2         # runner trails 1.2 x ATR behind the peak once TP1 filled
    rr_min: float = 2.5                 # gross TP1 / SL distance never below 2.5
    atr_sl_floor_pct: float = 0.15      # stop never tighter than 0.15 %: a stop-out also pays ~0.08 % in fees
    atr_trail_floor_pct: float = 0.10
    atr_trail_pct: Optional[float] = None   # per trade, set by Engine.bracket_plan()
    vpin_gate: bool = True              # VPIN > vpin_max: cancel resting maker entries + RUNE veto vpin_pause_s
    vpin_pause_s: float = 30.0
    kelly: bool = True                  # quarter-Kelly size per symbol once its ledger holds >= kelly_min_trades
    kelly_lambda: float = 0.25
    kelly_min_trades: int = 20
    kelly_max_risk_usdt: float = 0.50   # demo cap on money at the stop (fees included) per trade
    kelly_max_notional: float = 1000.0
    macro_gate: bool = True             # block entries against the 5 m VWAP + EMA-20 bias
    trail_activate_pct: float = 0.50    # MARIN phase 2: runner trails once +0.50 % was reached
    trail_gap_pct: float = 0.20         # trail = peak -/+ 0.20 %
    trail_tight_pct: float = 0.70       # tier 4b: from +0.70 % the runner trails 0.15 % behind the peak
    trail_tight_gap_pct: float = 0.15
    trail_min_step_pct: float = 0.02    # do not re-place the stop for < 0.02 % improvement
    max_loss_usdt: Optional[float] = None   # set -> stop derived from it (live)
    min_sl_pct: float = 0.10
    max_hold_s: Optional[float] = None  # no time stop (2026-10-07): exits are TP1 / BE / trail / SL
    equity_cap: Optional[float] = None      # margin cap (live micro-capital)
    daily_kill_usdt: float = 5.0
    cooldown_s: float = 30.0            # churn governor: 30 s flat after every close
    max_trades_hour: int = 8
    min_score: float = 0.82             # churn governor: auto entry needs swarm score >= 82 % ...
    min_edge_bps: float = 30.0          # ... AND expected edge >= 30 bps (>= 5x a 6 bps maker round trip)
    max_consec_losses: int = 3
    loss_pause_s: float = 300.0
    max_spread_bps: float = 3.0
    taker_bps: float = 5.0
    maker_bps: float = 2.0
    slip_bps: float = 1.0
    jev_min: Optional[float] = 8.0
    min_confluence: int = 2
    edge_ratio_min: float = 2.5
    mock_balance: float = 1000.0
    test_hold_s: Optional[float] = None
    # 10-08w Super-Alpha crossing: GTX unfilled after super_cross_after_s and the signal still qualifies (SOL/DOGE,
    # vote >= 0.95, OFI >= 0.85 with the trade, edge >= 30 bps, fee-true RR with a taker entry >= 1.50, VPIN not
    # toxic) -> cancel the GTX, take ONLY the remaining quantity with a LIMIT IOC capped at touch +/- 3 bps.
    # 'off' | 'shadow' (log SHADOW_CROSS with the would-be taker price, no order) | 'on'.  Shadow by default:
    # counterfactual of the 54 real attempts with vote >= 0.95 (10-08): taker entry -6.8 bps / opportunity on the
    # demo venue [CI -12.6, -1.7], -12.7 bps on mainnet [-18.3, -7.0]; no 5-min move reached 50 bps.
    super_cross: str = 'shadow'
    super_cross_after_s: float = 2.0
    super_cross_score: float = 0.95
    super_cross_ofi: float = 0.85
    super_cross_edge_bps: float = 30.0
    super_cross_slip_bps: float = 3.0
    super_cross_rr_min: float = 1.50
    super_cross_symbols: tuple = ('SOLUSDT', 'DOGEUSDT')
    # 10-08w dynamic TTL: past gtx_ttl_s keep the GTX resting (1 s at a time, at most dyn_ttl_max_s from the post)
    # while OFI L10 stays >= dyn_ttl_ofi with the trade, the touch has not left > dyn_ttl_away_pct and the mid has
    # not moved >= dyn_ttl_adverse_bps against the trade (then cancel at once).  'off' | 'shadow' | 'on'.  Shadow:
    # counterfactual 10/15/20 s TTL on the same attempts: more fills, lower net per opportunity.
    dyn_ttl: str = 'shadow'
    dyn_ttl_max_s: float = 15.0
    dyn_ttl_score: float = 0.90
    dyn_ttl_ofi: float = 0.50
    dyn_ttl_away_pct: float = 0.05
    dyn_ttl_adverse_bps: float = 3.0
    # 10-08w second exchange-stop ratchet: MFE >= +0.25 % -> stop at entry +/- (own fees + slip + 0.05 %) so a
    # stop-out at the stop price is net >= +0.05 % (gaps / stop slippage can still make it less).
    ratchet2: bool = True
    ratchet2_trigger_pct: float = 0.25
    ratchet2_buffer_pct: float = 0.05
    # 10-08w venue retries (Binance USD-M general info, 503 variants): -1008 / 'Service Unavailable' = failure ->
    # retry with backoff 0.2 / 0.4 / 0.8 s; unknown execution (timeout, transport) -> query by clientOrderId first,
    # re-send with the SAME clientOrderId only if the order does not exist.
    net_retries: int = 3
    net_retry_base_s: float = 0.2


# SOL/DOGE-first (2026-10-07o): max size per coin as (qty, notional USD). SOL 0.70 (~$80 at $115) unless the
# notional cap ($90) is smaller; every SOL/DOGE size (parity, Kelly, convex, boost) is clipped to it.
# 2026-10-07p: (base qty, max qty, max notional USD). Normal SOL/DOGE sizes stop at base; a swarm vote >= 90 %
# lifts the size from base toward max by the coin's rolling quarter-Kelly; the notional cap binds first if smaller.
ALT_SIZE = {'SOLUSDT': (0.70, 1.20, 140.0), 'DOGEUSDT': (300.0, 500.0, 140.0)}
ALT_SIZE_CAP = ALT_SIZE                 # name kept for 2026-10-07o callers
# 10-08s super-conviction size (vote >= 0.95 and OFI >= 0.80 with the trade): replaces the base cap for that trade,
# still under the $140 notional cap of ALT_SIZE and the portfolio risk / margin checks.
SUPER_SIZE = {'SOLUSDT': 0.80, 'DOGEUSDT': 500.0}       # 10-08u: 0.50 / 450 -> 0.80 / 500
# 10-08u minimum size of a normal (non-probe, non-MR, non-scalp) SOL/DOGE trend entry (was parity ~0.30 SOL / ~290 DOGE)
ALT_SIZE_FLOOR = {'SOLUSDT': 0.40, 'DOGEUSDT': 300.0}
# VFR horizon floor: BTC also needs ATR 15 m >= 0.10 % (VFR >= 3 already implies ~0.12 % at 4 bps of fees).
ATR15_FLOOR = {'BTCUSDT': 0.10}


PROFILES = {
    # 0.002 BTC so TP1 can take exactly half (step 0.001). A 0.05 U stop is impossible on BTC:
    # min notional ~$100 x (0.02 % maker + 0.05 % taker) = 0.07 U of fees alone -> demo uses SL 0.20 %.
    'demo': Profile('demo', 'BTCUSDT', qty=0.002, sl_pct=0.20, daily_kill_usdt=5.0),
    'live': Profile('live', 'DOGEUSDT', sl_pct=0.0, max_loss_usdt=0.05, equity_cap=3.85,
                    daily_kill_usdt=0.15, cooldown_s=60.0, max_trades_hour=6, max_spread_bps=8.0, mock_balance=3.85,
                    atr_scaler=False, kelly=False, convex_sizing=False, adopt_external=False, risk_parity=False,
                    asset_brackets=False, eject=False, alt_boost=False, super_size=False, chop_scalper='off',
                    drift_accel=False, alt_size_floor=False, eject_fast=False,
                    alt_risk_share=0.0, super_cross='off', dyn_ttl='off', ratchet2=False),  # live: max loss 0.05 U, minimum size, pure maker, VFR gate on
}


# ZEPHR bootstrap calibration: typical top-of-book spread (bps) per symbol, Binance VIP0 fees
# (maker 2 bps, taker 5 bps). Live depth5 overwrites the spread through an EMA from the first book.
SPREAD_BOOTSTRAP_BPS = {'BTCUSDT': 0.02, 'ETHUSDT': 0.05, 'SOLUSDT': 0.7, 'DOGEUSDT': 0.6, 'XRPUSDT': 0.4}


class CostModel:
    def __init__(self, p: Profile):
        self.maker, self.taker, self.slip = p.maker_bps, p.taker_bps, p.slip_bps
        self.spread = SPREAD_BOOTSTRAP_BPS.get(p.symbol, 1.0)
        self.samples = 0

    def observe(self, spread_bps: Optional[float]) -> None:
        if spread_bps is not None and math.isfinite(spread_bps) and spread_bps >= 0:
            self.spread = spread_bps if self.samples == 0 else 0.9 * self.spread + 0.1 * spread_bps
            self.samples += 1

    def round_trip_bps(self) -> float:
        """Planned path: GTX maker in + GTX maker TP out; the queue is joined, the spread is not paid."""
        return 2 * self.maker + 0.5 * self.spread

    def stop_out_pct(self) -> float:
        return (self.maker + self.taker + self.slip) / 100.0

    def view(self) -> dict:
        return {'maker_bps': self.maker, 'taker_bps': self.taker, 'spread_bps': self.spread,
                'round_trip_bps': self.round_trip_bps(), 'source': 'live' if self.samples else 'bootstrap',
                'samples': self.samples}


@dataclass(frozen=True)
class MicroIntent:
    symbol: str
    side: str
    quantity: str
    entry: str
    tag: str
    created_ms: int


def ceil_step(x: float, step: float) -> float:
    return round(math.ceil(round(x / step, 9)) * step, 12)


def plan(side: str, price: float, spec: dict, p: Profile, available: Optional[float] = None,
         qty: Optional[float] = None) -> dict:
    """Size + stop + target. -> {'ok': True, ...} or {'ok': False, 'why': ...} (OKAPI)."""
    if not price or price <= 0:
        return {'ok': False, 'why': 'no price'}
    step, min_qty, min_notional = spec['step'], spec['min_qty'], spec['min_notional']
    q = qty if qty else p.qty
    q = max(q or 0.0, min_qty, ceil_step(min_notional * 1.02 / price, step))
    q = ceil_step(q, step)
    notional = q * price
    entry_fee = p.maker_bps if p.entry == 'gtx' else p.taker_bps + p.slip_bps
    fees_pct = (entry_fee + p.taker_bps + p.slip_bps) / 100.0
    if p.max_loss_usdt is not None:
        sl_pct = p.max_loss_usdt / notional * 100.0 - fees_pct
        if sl_pct < p.min_sl_pct:
            return {'ok': False, 'why': f'CAPITAL GATE: {spec_name(spec)}min notional ${notional:,.2f} -> stop '
                                        f'{sl_pct:.3f}% < {p.min_sl_pct}% for max loss {p.max_loss_usdt} U'}
    else:
        sl_pct = p.sl_pct
    margin = notional / p.leverage
    if p.equity_cap is not None and margin > p.equity_cap + 1e-9:
        return {'ok': False, 'why': f'CAPITAL GATE: margin {margin:.2f} U > cap {p.equity_cap} U'}
    if available is not None and margin > available * 0.95:
        return {'ok': False, 'why': f'balance {available:.2f} U < margin {margin:.2f} U'}
    loss = notional * (sl_pct + fees_pct) / 100.0
    if p.max_loss_usdt is not None and loss > p.max_loss_usdt + 1e-9:
        return {'ok': False, 'why': f'loss at stop {loss:.4f} U > {p.max_loss_usdt} U'}
    return {'ok': True, 'qty': q, 'qty_str': fmt(q, step), 'notional': notional, 'margin': margin,
            'sl_pct': sl_pct, 'tp_pct': p.tp_pct, 'loss_at_sl': loss, 'fees_pct': fees_pct}


def spec_name(spec: dict) -> str:
    return f'{spec.get("symbol")} ' if spec.get('symbol') else ''


def brackets(side: str, fill: float, pl: dict, tick: float) -> tuple:
    d = 1 if side == 'BUY' else -1
    sl = round_tick(fill * (1 - d * pl['sl_pct'] / 100.0), tick, 'down' if d > 0 else 'up')
    tp = round_tick(fill * (1 + d * pl['tp_pct'] / 100.0), tick, 'up' if d > 0 else 'down')
    return sl, tp


def credentials(mode: str, env: dict) -> tuple:
    if mode == 'demo':
        key = env.get('BINANCE_TESTNET_API_KEY', '')
        sec = env.get('BINANCE_TESTNET_SECRET_KEY') or env.get('BINANCE_TESTNET_API_SECRET', '')
    else:
        key = env.get('BINANCE_MAINNET_API_KEY') or env.get('BINANCE_API_KEY', '')
        sec = env.get('BINANCE_MAINNET_SECRET_KEY') or env.get('BINANCE_API_SECRET', '')
    return key.strip(), sec.strip()


# ============================================================================ venues
class BinanceVenue(DemoVenue):
    """DemoVenue order primitives + cross margin + full position/order view for the UI."""
    label = 'BINANCE DEMO'

    def __init__(self, client, margin_type: str = 'CROSSED'):
        super().__init__(client)
        self.margin_type = margin_type

    async def prepare(self, symbol: str, leverage: int) -> None:
        from live.binance_futures_client import BinanceError
        ignorable = (-4046, -4047, -4067, -4028, -4059, -4061, -4068, -4131)
        try:
            await self.c.request('POST', '/fapi/v1/marginType', {'symbol': symbol, 'marginType': self.margin_type},
                                 signed=True)
        except BinanceError as e:
            if e.code not in ignorable:          # already set / has open orders / position exists
                raise
        try:
            await self.c.request('POST', '/fapi/v1/leverage', {'symbol': symbol, 'leverage': leverage}, signed=True)
        except BinanceError as e:
            if e.code not in ignorable:
                raise

    async def account_view(self) -> dict:
        a = await self.c.account()
        return {'wallet': float(a.get('totalWalletBalance') or 0), 'available': float(a.get('availableBalance') or 0),
                'upnl': float(a.get('totalUnrealizedProfit') or 0)}

    async def position_view(self, symbol: str) -> dict:
        rows = await self.c.positions(symbol)
        p = next((r for r in rows if float(r.get('positionAmt', 0))), rows[0] if rows else {})
        return {'amt': float(p.get('positionAmt') or 0), 'entry': float(p.get('entryPrice') or 0),
                'mark': float(p.get('markPrice') or 0), 'upnl': float(p.get('unRealizedProfit') or 0),
                'liq': float(p.get('liquidationPrice') or 0), 'notional': abs(float(p.get('notional') or 0)),
                'margin': float(p.get('initialMargin') or p.get('positionInitialMargin') or 0)}

    # ---------------------------------------------------------------- 10-08w transient venue errors
    retries, retry_base_s = 3, 0.2
    TRANSIENT = (-1008, -1001, -1006, -1007, 'TRANSPORT', 'NON_JSON', 'HTTP_ERROR')

    def transient(self, e) -> bool:
        """5xx / overload / transport: worth a retry (429 / 418 are left to the client's rate brake)."""
        st = getattr(e, 'status', 0) or 0
        return getattr(e, 'code', None) in self.TRANSIENT or (st >= 500 and st not in (429, 418))

    async def _sleep_retry(self, k: int) -> None:
        await asyncio.sleep(self.retry_base_s * 2 ** k)

    async def _query_raw(self, symbol: str, cid: str) -> Optional[dict]:
        """Raw order by clientOrderId; None only when the venue says it does not exist (-2013)."""
        from live.binance_futures_client import BinanceError
        for k in range(self.retries + 1):
            try:
                return await self.c.order(symbol, cid)
            except BinanceError as e:
                if e.code == -2013:
                    return None
                if not self.transient(e) or k == self.retries:
                    raise
                await self._sleep_retry(k)
        return None

    async def _post(self, symbol: str, cid: str, send, resend_unknown: bool):
        """POST with retries.  -1008 (throttled) = definite failure -> back off and re-send.  Unknown outcome
        (timeout / transport / other 5xx) -> query the clientOrderId first: found -> that order; not found ->
        re-send with the SAME clientOrderId only when resend_unknown (a resting GTX: a duplicate id is refused
        while the first is open), never for IOC (a filled IOC is no longer open, a re-send could fill twice)."""
        from live.binance_futures_client import BinanceError
        for k in range(self.retries + 1):
            try:
                return await send()
            except BinanceError as e:
                if not self.transient(e):
                    if k and e.code not in (-5022,):          # re-send refused (duplicate id?): look it up
                        r = await self._query_raw(symbol, cid)
                        if r is not None:
                            return r
                    raise
                if k == self.retries:
                    raise
                if e.code != -1008:
                    r = await self._query_raw(symbol, cid)
                    if r is not None:
                        return r
                    if not resend_unknown:
                        await asyncio.sleep(0.3)               # late landing: one more look, then give up
                        r = await self._query_raw(symbol, cid)
                        if r is not None:
                            return r
                        raise
                await self._sleep_retry(k)

    async def ioc(self, symbol: str, side: str, qty: str, price: str, cid: str) -> tuple:
        """LIMIT IOC: takes what is available up to `price`, cancels the rest. -> (avg_fill, filled_qty)."""
        send = lambda: self.c.request('POST', '/fapi/v1/order', {
            'symbol': symbol, 'side': side, 'type': 'LIMIT', 'timeInForce': 'IOC', 'quantity': qty, 'price': price,
            'positionSide': 'BOTH', 'newClientOrderId': cid, 'newOrderRespType': 'RESULT'}, signed=True)
        r = await self._post(symbol, cid, send, resend_unknown=False) or {}
        filled = float(r.get('executedQty') or 0)
        avg = float(r.get('avgPrice') or 0)
        if filled > 0 and avg <= 0:
            cq = float(r.get('cumQuote') or 0)
            avg = cq / filled if cq > 0 else float(price)
        return avg, filled

    async def cancel(self, symbol: str, cid: str) -> None:
        """Cancel is idempotent (unknown / done order = benign -2011 / -2013): retry transient failures."""
        from live.binance_futures_client import BinanceError
        for k in range(self.retries + 1):
            try:
                return await super().cancel(symbol, cid)
            except BinanceError as e:
                if not self.transient(e) or k == self.retries:
                    raise
                await self._sleep_retry(k)

    own_book = True                     # demo matching engine != mainnet: quote/manage on THIS venue's book

    async def book_ticker(self, symbol: str) -> dict:
        r = await self.c.request('GET', '/fapi/v1/ticker/bookTicker', {'symbol': symbol}, weight=2)
        return {'bid': float(r['bidPrice']), 'ask': float(r['askPrice']), 'bid_qty': float(r.get('bidQty') or 0),
                'ask_qty': float(r.get('askQty') or 0)}

    async def order(self, symbol: str, cid: str) -> dict:
        from live.binance_futures_client import BinanceError
        miss = 0
        for k in range(self.retries + 2):
            try:
                return await super().order(symbol, cid)
            except BinanceError as e:
                if e.code == -2013:                       # -2013: not visible yet / expired post-only
                    miss += 1
                    if miss >= 2:
                        break
                    await asyncio.sleep(0.3)
                    continue
                if not self.transient(e) or k >= self.retries:
                    raise
                await self._sleep_retry(k)
        return {'status': 'NOT_FOUND', 'filled': 0.0, 'avg': 0.0}

    async def entry_start_ms(self, symbol: str, amt: float, step: float, lookback_s: float = 12 * 3600) -> Optional[int]:
        """First fill (ms) of the fills that built the open position `amt` (ADOPT fee reconciliation)."""
        trades, complete = await self.c.history_since(symbol, int((time.time() - lookback_s) * 1000))
        fills = sorted((int(x['time']), float(x['qty']) * (1 if x.get('side') == 'BUY' else -1)) for x in trades)
        return entry_start_from_fills(fills, amt, step)

    async def realized_parts(self, symbol: str, start_ms: int) -> dict:
        trades, _ = await self.c.history_since(symbol, start_ms)
        gross = sum(float(x.get('realizedPnl', 0)) for x in trades)
        fee = sum(float(x.get('commission', 0)) for x in trades if x.get('commissionAsset', 'USDT') == 'USDT')
        return {'gross': gross, 'fee': fee, 'net': gross - fee,
                'maker_fills': sum(bool(x.get('maker')) for x in trades),
                'taker_fills': sum(not x.get('maker') for x in trades)}

    async def limit_gtx(self, symbol: str, side: str, qty: str, price: str, cid: str) -> None:
        from live.binance_futures_client import BinanceError
        send = lambda: DemoVenue.limit_gtx(self, symbol, side, qty, price, cid)
        try:
            r = await self._post(symbol, cid, send, resend_unknown=True)
        except BinanceError as e:
            if e.code == -5022:
                raise PostOnlyReject('-5022 post-only would take liquidity') from None
            raise
        if isinstance(r, dict) and r.get('status') == 'EXPIRED' and not float(r.get('executedQty') or 0):
            raise PostOnlyReject('GTX expired at entry (would take liquidity)')

    async def orders_view(self, symbol: str) -> list:
        out = []
        for o in await self.c.open_orders(symbol):
            out.append({'id': o.get('clientOrderId'), 'side': o.get('side'), 'type': o.get('type'),
                        'price': float(o.get('price') or 0), 'qty': float(o.get('origQty') or 0),
                        'reduce': bool(o.get('reduceOnly')), 'time': (o.get('time') or 0) / 1000})
        for a in await self.c.open_algos(symbol):
            out.append({'id': a.get('clientAlgoId'), 'side': a.get('side'), 'type': a.get('orderType') or 'STOP_MARKET',
                        'price': float(a.get('triggerPrice') or 0), 'qty': float(a.get('quantity') or 0),
                        'reduce': True, 'time': (a.get('createTime') or 0) / 1000})
        return out


class PostOnlyReject(RuntimeError):
    pass


class LiveVenue(BinanceVenue):
    label = 'BINANCE MAINNET'
    own_book = False                    # mainnet: the public WS book IS the venue book

    def __init__(self, client, margin_type: str = 'CROSSED'):
        host = urlsplit(client.settings.rest).hostname
        if client.settings.mode != 'mainnet' or host != 'fapi.binance.com' or not client.settings.armed:
            raise PermissionError(f'live venue needs armed mainnet settings, got {client.settings.mode}/{host}')
        self.c = client
        self.margin_type = margin_type


class MockVenue(ShadowVenue):
    """Paper broker on the engine's own live book (no key): taker+slip in, maker TP, taker stop."""
    label = 'MOCK · GIÁ LIVE BINANCE'

    def __init__(self, profile: Profile, quote):
        super().__init__(SimpleNamespace(slip_bps=profile.slip_bps, taker_bps=profile.taker_bps,
                                         maker_bps=profile.maker_bps), quote=quote)
        self.balance0 = profile.mock_balance
        self.leverage = profile.leverage

    async def specs(self) -> dict:
        try:
            return await super().specs()
        except Exception:
            return dict(FALLBACK_SPECS)

    def realized_total(self) -> float:
        return sum(p for _, _, p in self.pnl)

    def _fill(self, symbol: str, side: str, qty: float, price: float, fee_bps: float) -> None:
        super()._fill(symbol, side, qty, price, fee_bps)
        self.fills[-1]['fee'] = price * qty * fee_bps / 1e4
        self.fills[-1]['t'] = self.pnl[-1][1]
        self.fills[-1]['maker'] = fee_bps == self.cfg.maker_bps

    async def ioc(self, symbol: str, side: str, qty: str, price: str, cid: str) -> tuple:
        bid, ask = self.quote(symbol)
        lim, slip = float(price), self.cfg.slip_bps / 1e4
        if side == 'BUY':
            if ask > lim:
                return 0.0, 0.0
            px = min(ask * (1 + slip), lim)
        else:
            if bid < lim:
                return 0.0, 0.0
            px = max(bid * (1 - slip), lim)
        self._fill(symbol, side, float(qty), px, self.cfg.taker_bps)
        self.orders[cid] = {'symbol': symbol, 'side': side, 'qty': float(qty), 'price': px, 'kind': 'IOC',
                            'status': 'FILLED'}
        return px, float(qty)

    async def limit_gtx(self, symbol: str, side: str, qty: str, price: str, cid: str) -> None:
        bid, ask = self.quote(symbol)
        if (side == 'BUY' and float(price) >= ask) or (side == 'SELL' and float(price) <= bid):
            raise PostOnlyReject('-5022 post-only would take liquidity')
        await super().limit_gtx(symbol, side, qty, price, cid)

    async def entry_start_ms(self, symbol: str, amt: float, step: float) -> Optional[int]:
        fills = [(f['t'], f['qty'] * (1 if f['side'] == 'BUY' else -1)) for f in self.fills if f['symbol'] == symbol]
        return entry_start_from_fills(fills, amt, step)

    async def realized_parts(self, symbol: str, start_ms: int) -> dict:
        rows = [f for f in self.fills if f['symbol'] == symbol and f.get('t', 0) >= start_ms]
        fee = sum(f['fee'] for f in rows)
        net = sum(f['pnl'] for f in rows)
        return {'gross': net + fee, 'fee': fee, 'net': net, 'maker_fills': sum(f['maker'] for f in rows),
                'taker_fills': sum(not f['maker'] for f in rows)}

    async def account_view(self) -> dict:
        upnl, used = 0.0, 0.0
        for sym, amt in self.amt.items():
            if amt:
                bid, ask = self.quote(sym)
                mark = (bid + ask) / 2
                upnl += (mark - self.entry[sym]) * amt
                used += abs(amt) * mark / self.leverage
        wallet = self.balance0 + self.realized_total()
        return {'wallet': wallet, 'available': wallet + min(upnl, 0) - used, 'upnl': upnl}

    async def position_view(self, symbol: str) -> dict:
        p = await self.position(symbol)
        amt, e = p['amt'], p['entry']
        liq = 0.0
        if amt:
            mm = 0.004
            liq = e * (1 - 1 / self.leverage + mm) if amt > 0 else e * (1 + 1 / self.leverage - mm)
        return {**p, 'liq': liq, 'notional': abs(amt) * p['mark'], 'margin': abs(amt) * p['mark'] / self.leverage}

    async def orders_view(self, symbol: str) -> list:
        self._match(symbol)
        return [{'id': cid, 'side': o['side'], 'type': {'ENTRY': 'LIMIT GTX', 'TP': 'TP LIMIT', 'SL': 'STOP_MARKET'}
                 .get(o['kind'], o['kind']), 'price': o['price'], 'qty': o['qty'] or 0.0,
                 'reduce': o['kind'] in ('TP', 'SL'), 'time': 0}
                for cid, o in self.orders.items() if o['symbol'] == symbol and o['status'] == 'NEW']

    async def flatten(self, symbol: str) -> None:
        await super().flatten(symbol)


# ============================================================================ engine
class RiskBook:
    """Portfolio risk shared by every symbol engine of one Runner: one position slot, one governor, one ledger view."""

    def __init__(self):
        self.loaded = False
        self.engines: list = []
        self.history = deque(maxlen=60)
        self.all_rows: list = []
        self.cooldown_until = 0.0
        self.pause_until = 0.0
        self.trade_times = deque()
        self.consec_losses = 0
        self.day_pnl = 0.0


def _shared(name: str) -> property:
    return property(lambda self: getattr(self.risk, name), lambda self, v: setattr(self.risk, name, v))


class Engine:
    history = _shared('history')
    all_rows = _shared('all_rows')
    cooldown_until = _shared('cooldown_until')
    pause_until = _shared('pause_until')
    trade_times = _shared('trade_times')
    consec_losses = _shared('consec_losses')
    day_pnl = _shared('day_pnl')

    def __init__(self, profile: Profile, venue, feed: msig.MicroFeed, telemetry=None, ledger: Path = LEDGER,
                 allow_live: bool = False, sig_cfg: Optional[msig.SignalConfig] = None, clock=time.time,
                 risk: Optional[RiskBook] = None):
        self.p, self.venue, self.feed, self.tel = profile, venue, feed, telemetry
        self.ledger, self.allow_live, self.clock = ledger, allow_live, clock
        fresh = risk is None or not risk.loaded
        self.risk = risk if risk is not None else RiskBook()
        self.risk.engines.append(self)
        self.sig_cfg = sig_cfg or feed.cfg
        self.reg: dict = {}
        self.consensus = ConsensusEngine()
        self.cost = CostModel(profile)
        self.lock = asyncio.Lock()                  # one actor at a time: entry / MARIN / REST sync
        self.vbook: Optional[dict] = None           # venue's own top of book (demo), see refresh_book()
        self.vbook_at = 0.0
        if fresh:
            self.all_rows = self._load_all()
        self.authority = TokenAuthority()
        self.spec: Optional[dict] = None
        self.started = False
        self.busy = False
        self.halted = ''
        self.pos: Optional[dict] = None             # engine-owned position
        self.view = {'amt': 0.0}                     # venue position view
        self.account = {}
        self.orders: list = []
        if fresh:
            self.history = deque(maxlen=60)
        self.events = deque(maxlen=60)
        if fresh:
            self.cooldown_until = 0.0
        self.chop_locked = True                       # KER unknown at start -> locked
        self.flow = {'state': 'UNKNOWN', 'buy_ok': False, 'sell_ok': False, 'why': ''}
        self.candidate: Optional[tuple] = None        # (t, quality) of an entry this engine wants (slot routing)
        self.pending_risk = 0.0                       # loss at stop of an entry being worked (portfolio cap)
        self.drift_probe: Optional[str] = None        # set by churn_gate when a DRIFT lock is lifted (half size)
        self.blockers: list = []                      # 10-08v: every churn gate that blocked the last candidate
        self.funnel = xf.Funnel(clock=self.clock)    # 10-08v: why-no-trade counters (1 Hz time-share)
        self.orphans: dict = {}                       # 10-08w: entry cids whose cancel failed -> retried in sync()
        self.scalp_next = 0.0
        self.absorb = {'state': 'UNKNOWN', 'blocks': None}
        self.macro = {'name': 'WARMUP', 'allow': ('BUY', 'SELL')}
        self.tox = {'vpin': None, 'buckets': 0, 'toxic': False, 'state': 'WARMUP'}
        self.toxic_until = 0.0                        # VPIN pause (per symbol)
        self.toxic_side: Optional[str] = None         # side the toxic flow pushes (BUY = aggressive buying)
        self.toxic_vpin: Optional[float] = None
        if fresh:
            self.pause_until = 0.0
            self.trade_times = deque()
            self.consec_losses = 0
        self.authorized, self.auth_at = 0, None
        self.latency = deque(maxlen=50)
        self.decision: Optional[Decision] = None
        self.votes: list = []
        self.diag: dict = {}
        self.sig: dict = {}
        self.features: dict = {}
        self.phase = 'BOOT'
        self.last_error = ''
        if fresh:
            self.day_pnl = self._load_today()
            self.risk.loaded = True

    # ------------------------------------------------------------------ housekeeping
    def peer(self) -> Optional['Engine']:
        """Another symbol engine of the same Runner that holds the single position slot."""
        for o in self.risk.engines:
            if o is not self and (o.pos or o.busy or abs(o.view.get('amt') or 0) > 0):
                return o
        return None

    def risk_at_stop(self) -> float:
        """U lost if this symbol's stop fills now (stop distance + in/out fees); in-flight entries count too."""
        p = self.pos
        if p:
            fee = p['qty'] * p['entry'] * (p.get('entry_fee_bps', self.p.maker_bps) + self.p.taker_bps
                                          + self.p.slip_bps) / 1e4
            d = 1 if p['side'] == 'BUY' else -1
            return max(0.0, p['qty'] * d * (p['entry'] - p['sl']) + fee)
        amt = abs(self.view.get('amt') or 0.0)
        if amt:
            mark = self.view.get('mark') or self.feed.price() or 0.0
            return amt * mark * (max(self.p.sl_pct, self.p.min_sl_pct) + 0.08) / 100.0
        return self.pending_risk if self.busy else 0.0

    def margin_used(self) -> float:
        if self.pos:
            return self.pos['qty'] * self.pos['entry'] / self.p.leverage
        amt = abs(self.view.get('amt') or 0.0)
        return amt * (self.view.get('mark') or self.feed.price() or 0.0) / self.p.leverage

    def open_peers(self) -> list:
        return [o for o in self.risk.engines if o is not self and (o.pos or o.busy or abs(o.view.get('amt') or 0) > 0)]

    def slot_block(self) -> Optional[str]:
        """Why this symbol may not open now: single-slot owner, or portfolio count / risk budget used up."""
        if not self.p.multi_slot:
            o = self.peer()
            return None if o is None else (f'SCANNER slot: {o.p.symbol} '
                                           f'{"position" if o.pos or o.view.get("amt") else "order"} active')
        peers = self.open_peers()
        if len(peers) + 1 > self.p.max_positions:
            return f'PORTFOLIO FULL {len(peers)}/{self.p.max_positions}'
        risk, cap = sum(o.risk_at_stop() for o in peers), self.risk_cap()
        if risk >= cap:
            return f'PORTFOLIO RISK {risk:.2f} >= {cap:g} U' + (' (BTC: phần dành SOL/DOGE)' if cap <
                                                                 self.p.portfolio_risk_usdt else '')
        return None

    def is_alt(self) -> bool:
        return self.p.symbol in ALT_SIZE

    def risk_cap(self) -> float:
        """Money-at-stop budget for a new trade on this coin: BTC gets (1 - alt_risk_share) of the pool."""
        if self.is_alt() or not self.p.alt_boost:
            return self.p.portfolio_risk_usdt
        return max(0.0, self.p.portfolio_risk_usdt * (1.0 - self.p.alt_risk_share))

    def alt_kelly(self) -> dict:
        """Rolling Kelly (Thorp) on this coin's last alt_kelly_window settled trades:
        f* = max(0, (p (b + 1) - 1) / b), f = lambda f* (quarter), scale = min(1, f / alt_kelly_full)."""
        rows = [float(r['pnl']) for r in self.all_rows
                if r.get('symbol', self.p.symbol) == self.p.symbol and r.get('pnl') is not None]
        rows = rows[-self.p.alt_kelly_window:]
        n, wins, losses = len(rows), [x for x in rows if x > 0], [-x for x in rows if x <= 0]
        out = {'n': n, 'p': None, 'b': None, 'f_star': None, 'f': None, 'scale': 0.0}
        if n < self.p.alt_kelly_min_n or not wins:
            return out
        p = len(wins) / n
        b = (sum(wins) / len(wins)) / (sum(losses) / len(losses)) if losses and sum(losses) > 0 else None
        f_star = 1.0 if b is None else max(0.0, (p * (b + 1) - 1) / b)
        f = f_star * self.p.kelly_lambda
        out.update(p=p, b=b, f_star=f_star, f=f, scale=min(1.0, f / self.p.alt_kelly_full) if self.p.alt_kelly_full
                   else 1.0)
        return out

    def alt_cap(self, price: float, boosted: bool = False) -> Optional[float]:
        """SOL/DOGE size ceiling (qty): base normally; boosted -> base + (max - base) x Kelly scale;
        never above max notional / price; on the step grid."""
        if not self.is_alt() or not price or not self.spec:
            return None
        base, top, usd = ALT_SIZE[self.p.symbol]
        q = base + (top - base) * self.alt_kelly()['scale'] if boosted else base
        return floor_step(min(q, usd / price), self.spec['step'])

    def vfr_view(self, f: Optional[dict] = None) -> dict:
        """Volatility-to-Fee Ratio on ATR 15 m: frozen below vfr_min (and BTC below ATR 15 m 0.10 %)."""
        f = self.features if f is None else f
        f = f or {}
        # spread of the market the ATR is measured on (signal feed). 10-08t: the demo venue's own testnet book
        # (13 bps on SOL) froze SOL at VFR 1.9 while mainnet SOL spread was 0.86 bps.
        spread = f.get('spread_bps')
        if spread is None:
            b = self.book() if self.feed is not None else {}
            bid, ask = b.get('bid') or f.get('bid'), b.get('ask') or f.get('ask')
            spread = (ask - bid) / ((ask + bid) / 2) * 1e4 if bid and ask and ask > bid else 0.0
        fee = 2 * self.p.maker_bps
        a15, src = f.get('atr15_pct'), f.get('atr15_src')
        v = msig.vfr(a15, fee, spread)
        floor = ATR15_FLOOR.get(self.p.symbol)
        sym = self.p.symbol.replace('USDT', '')
        out = {'vfr': v, 'atr15_pct': a15, 'src': src, 'fee_bps': fee, 'spread_bps': spread, 'min': self.p.vfr_min,
               'full': self.p.vfr_full, 'gate': self.p.vfr_gate, 'frozen': False, 'why': ''}
        if v is None:
            out.update(frozen=True, why=f'VFR {sym} warming (no ATR 15 m yet)')
        elif v < self.p.vfr_min:
            out.update(frozen=True, why=f'VFR FROZEN {sym} {v:.2f} < {self.p.vfr_min:g} · ATR15m {a15:.3f}% vs '
                                        f'phí {fee:g} + spread {spread:.1f} bps')
        elif floor is not None and a15 < floor:
            out.update(frozen=True, why=f'VFR FROZEN {sym} ATR15m {a15:.3f}% < {floor}%')
        return out

    @property
    def observer(self) -> bool:
        """Observe-only symbol: features, swarm and telemetry run; no entry (an existing position is still managed)."""
        return self.p.symbol in (self.p.observe_only or ())

    def drift_acceleration(self, side: str) -> Optional[str]:
        """CVD 60 s >= ratio x |CVD 3 m| and >= min $ (x scale), on `side`: fresh flow overrides the stale 3 m
        window. -> reason text (probe allowed) or None."""
        if not self.p.drift_accel or not side:
            return None
        f = self.features or {}
        c60, c3 = f.get('cvd_60s') or 0.0, f.get('cvd_3m') or 0.0
        sgn = 1 if side == 'BUY' else -1
        need = self.p.drift_accel_min_usd * self.feed.cfg.usd_scale
        if sgn * c60 >= need and abs(c60) >= self.p.drift_accel_ratio * abs(c3):
            return f'DRIFT ACCEL {side}: CVD60 {c60 / 1e6:+.2f}M >= {self.p.drift_accel_ratio:g}x |CVD3m {c3 / 1e6:+.2f}M|'
        return None

    def scalp_candidate(self, f: dict) -> tuple:
        """Chop micro-scalp setup -> (side, why) or (None, why)."""
        k, spread, ofi = f.get('ker'), f.get('spread_bps'), f.get('ofi_10s')
        if k is None or k >= self.p.scalp_ker_max:
            return None, f'KER {k} >= {self.p.scalp_ker_max}'
        if spread is None or spread > self.p.scalp_spread_max_bps:
            return None, f'spread {spread} > {self.p.scalp_spread_max_bps} bps'
        if ofi is None or abs(ofi) < self.p.scalp_ofi_min:
            return None, f'OFI {ofi} below {self.p.scalp_ofi_min}'
        side = 'BUY' if ofi > 0 else 'SELL'
        if self.toxic_blocks(side):
            return None, 'VPIN toxic against the side'
        return side, f'CHOP SCALP {side}: KER {k:.2f} · spread {spread:.2f} bps · OFI {ofi:+.2f}'

    async def chop_scalp(self, d: Decision, f: dict) -> None:
        """Second engine for chop: maker scalp at the touch (on) or a logged would-be scalp (shadow)."""
        mode = self.p.chop_scalper
        if mode not in ('shadow', 'on') or self.busy or self.pos or self.clock() < self.scalp_next:
            return
        side, why = self.scalp_candidate(f)
        if side is None:
            return
        g = self.gates(f)
        if not all(ok for ok, _ in g.as_map().values()):
            return
        self.scalp_next = self.clock() + self.p.scalp_every_s
        if mode == 'shadow':
            b = self.book()
            self._shadow_row({'outcome': 'SHADOW_SCALP', 'side': side, 'path': 'SCALP', 'why': why,
                              'touch': b.get('bid') if side == 'BUY' else b.get('ask'), 'ker': f.get('ker'),
                              'spread_bps': f.get('spread_bps'), 'ofi': f.get('ofi_10s')})
            return
        self.event(why)
        sd = replace(d, side=side, allowed=True, veto=False, reason=why)
        await self.execute(side, sd, 'SCALP')

    def _shadow_row(self, extra: dict) -> None:
        row = {'t': self.clock(), 'schema': 1, 'mode': self.p.mode, 'venue': self.venue.label,
               'symbol': self.p.symbol, 'policy': policy_id(self.p), 'code': CODE_VERSION, **extra}
        if extra.get('outcome'):
            self.funnel.outcome(extra['outcome'])
        try:
            path = self.ledger.parent / 'engine_orders.jsonl'
            with open(path, 'a', encoding='utf-8') as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + '\n')
        except OSError:
            pass

    def plan_rr(self, pl: dict, route: str = 'gtx') -> Optional[float]:
        """Fee-true reward:risk of a sized plan: (TP - maker round trip) / (SL + in + taker out + slip)."""
        e_in = (self.p.taker_bps + self.p.slip_bps) if route == 'ioc' else self.p.maker_bps
        win, loss = pl['tp_pct'] - (e_in + self.p.maker_bps) / 100.0, pl['sl_pct'] + pl['fees_pct']
        return win / loss if loss > 0 else None

    def super_conviction(self, d: Decision, diag: dict, f: dict) -> bool:
        """Raw swarm vote >= 0.95 (no JEV lift) AND OFI L10 >= 0.80 with the trade, SOL/DOGE only."""
        if not self.p.super_size or self.p.symbol not in SUPER_SIZE or d is None or not d.side:
            return False
        vote = max(d.score, (diag or {}).get('consensus') or 0.0)
        ofi = (f.get('ofi_10s') or 0.0) * (1 if d.side == 'BUY' else -1)
        return vote >= self.p.super_score - 1e-9 and ofi >= self.p.super_ofi

    def alt_boosted(self, d: Decision, diag: dict) -> bool:
        """SOL/DOGE priority: raw swarm vote (not the JEV lift) >= 0.90."""
        if not (self.p.alt_boost and self.p.alt_size_boost) or not self.is_alt() or d is None or not d.side:
            return False
        return max(d.score, (diag or {}).get('consensus') or 0.0) >= self.p.alt_boost_score - 1e-9

    def portfolio_check(self, pl: dict) -> Optional[str]:
        """The new trade must fit: risk at all stops <= cap, margin <= frac x wallet."""
        if not self.p.multi_slot:
            return None
        peers = self.open_peers()
        risk, cap = sum(o.risk_at_stop() for o in peers) + pl['loss_at_sl'], self.risk_cap()
        if risk > cap + 1e-9:
            return f'PORTFOLIO RISK {risk:.3f} U > {cap:g} U (gồm lệnh mới {pl["loss_at_sl"]:.3f})'
        wallet = self.account.get('wallet') or self.account.get('available')
        margin = sum(o.margin_used() for o in peers) + pl['margin']
        if wallet and margin > self.p.portfolio_margin_frac * wallet + 1e-9:
            return f'PORTFOLIO MARGIN {margin:.2f} U > {self.p.portfolio_margin_frac * 100:.0f}% ví'
        return None

    def portfolio_view(self) -> dict:
        rows = [r for r in (o.position_row() for o in self.risk.engines) if r]
        return {'multi_slot': self.p.multi_slot, 'rows': rows, 'count': len(rows), 'max': self.p.max_positions,
                'upnl': sum(r.get('upnl') or 0.0 for r in rows),
                'risk': sum(o.risk_at_stop() for o in self.risk.engines), 'risk_cap': self.p.portfolio_risk_usdt}

    def size_mult(self, d: Decision, diag: dict, f: dict) -> float:
        """Convex sizing: 1.0x normally; 1.5x at score >= 0.90 with OFI and CVD 60 s strongly with the trade;
        2.0x at score >= 0.98."""
        if not self.p.convex_sizing or d is None or not d.side:
            return 1.0
        sgn = 1 if d.side == 'BUY' else -1
        ofi, cvd = (f.get('ofi_10s') or 0.0) * sgn, (f.get('cvd_60s') or 0.0) * sgn
        score = max(d.score, (diag or {}).get('consensus') or 0.0)      # swarm vote, not the JEV lift
        if score < self.p.convex_score or ofi < self.p.convex_ofi or \
                cvd < self.p.convex_cvd60_usd * self.feed.cfg.usd_scale:
            return 1.0
        return self.p.convex_top_mult if score >= self.p.convex_top_score else self.p.convex_mult

    def min_qty(self, price: float) -> float:
        return ceil_step(max(self.spec['min_qty'], self.spec['min_notional'] * 1.02 / price), self.spec['step'])

    def event(self, msg: str) -> None:
        if len(self.risk.engines) > 1:
            msg = f'{self.p.symbol.replace("USDT", "")} · {msg}'
        self.events.appendleft({'t': self.clock(), 'message': msg[:240]})
        if self.tel is not None:
            self.tel.event(msg)
        print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)

    def _load_today(self) -> float:
        today, pnl = datetime.now().strftime('%Y-%m-%d'), 0.0
        try:
            for line in self.ledger.read_text(encoding='utf-8').splitlines():
                r = json.loads(line)
                if r.get('mode') == self.p.mode and r.get('day') == today:
                    pnl += float(r.get('pnl') or 0)
                    self.history.appendleft(r)
        except (OSError, ValueError):
            pass
        return pnl

    def _load_all(self) -> list:
        rows = []
        try:
            for line in self.ledger.read_text(encoding='utf-8').splitlines():
                r = json.loads(line)
                if r.get('mode') == self.p.mode:
                    rows.append(r)
        except (OSError, ValueError):
            pass
        return rows

    def stats(self, rows: Optional[list] = None) -> dict:
        """Win-rate radar over settled trades: net PnL after fees."""
        rows = self.all_rows if rows is None else rows
        pnl = [float(r.get('pnl') or 0) for r in rows]
        wins, losses = [x for x in pnl if x > 0], [x for x in pnl if x <= 0]
        gw, gl = sum(wins), -sum(losses)
        n = len(pnl)
        known = [r for r in rows if r.get('gross') is not None and r.get('fee') is not None]
        return {'n': n, 'wins': len(wins), 'losses': len(losses), 'win_rate': len(wins) / n if n else None,
                'rows_unknown_cost': n - len(known), 'gross_known': sum(float(r['gross']) for r in known),
                'avg_win': gw / len(wins) if wins else None, 'avg_loss': -gl / len(losses) if losses else None,
                'profit_factor': gw / gl if gl > 0 else None, 'no_losses': gl == 0 and gw > 0,
                'expectancy': sum(pnl) / n if n else None, 'net': sum(pnl),
                'fees': sum(float(r['fee']) for r in known)}

    @property
    def own_book(self) -> bool:
        return bool(getattr(self.venue, 'own_book', False))

    async def refresh_book(self, max_age: float = 0.25) -> None:
        if self.own_book and time.monotonic() - self.vbook_at >= max_age:
            try:
                self.vbook = await self.venue.book_ticker(self.p.symbol)
                self.vbook_at = time.monotonic()
            except Exception as exc:
                self.last_error = f'bookTicker: {type(exc).__name__}: {exc}'[:160]

    def book(self) -> dict:
        """Execution book: the venue's own touch (demo) or the market feed (mainnet / mock)."""
        if self.own_book:
            b = self.vbook or {}
            stale = not b or time.monotonic() - self.vbook_at > 3.0
            bid, ask = b.get('bid'), b.get('ask')
            return {'bid': bid, 'ask': ask, 'bid_qty': b.get('bid_qty'), 'ask_qty': b.get('ask_qty'),
                    'price': (bid + ask) / 2 if bid and ask else None, 'stale': stale, 'source': 'venue'}
        f = self.feed.features(self.clock())
        return {'bid': f.get('bid'), 'ask': f.get('ask'), 'bid_qty': f.get('bid_qty'), 'ask_qty': f.get('ask_qty'),
                'price': f.get('price'), 'stale': f.get('stale'), 'source': 'feed'}

    def _write_ledger(self, row: dict) -> None:
        try:
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            with open(self.ledger, 'a', encoding='utf-8') as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + '\n')
        except OSError:
            pass

    async def start(self) -> None:
        await self.venue.start()
        specs = await self.venue.specs()
        self.spec = specs.get(self.p.symbol) or FALLBACK_SPECS.get(self.p.symbol)
        if not self.spec:
            raise RuntimeError(f'no exchange filters for {self.p.symbol}')
        await self.venue.prepare(self.p.symbol, self.p.leverage)
        self.started = True
        self.phase = 'SCANNING'
        self.event(f'{self.venue.label} · {self.p.symbol} · {self.p.margin_type} {self.p.leverage}x · entry '
                   f'{self.p.entry.upper()} · TP +{self.p.tp_pct}% · '
                   + (f'max loss {self.p.max_loss_usdt} U' if self.p.max_loss_usdt else f'SL -{self.p.sl_pct}%'))

    # ------------------------------------------------------------------ gates
    def gates(self, f: dict) -> swarm.Gates:
        now = self.clock()
        spread = f.get('spread_bps')
        self.cost.observe(spread)
        rt = self.cost.round_trip_bps()
        if f.get('stale'):
            z = (False, 'STALE book (/public depth5 silent)')
        elif f.get('trades_silent'):
            z = (False, 'aggTrade silent > 15 s (/market socket) - CVD/VWAP blind')
        elif spread is None or spread > self.p.max_spread_bps:
            z = (False, f'spread {spread if spread is None else round(spread, 2)} bps > {self.p.max_spread_bps}')
        else:
            z = (True, f'cost {rt:.2f} bps RT ({self.cost.view()["source"]}) · spread {spread:.2f} bps')
        while self.trade_times and now - self.trade_times[0] > 3600:
            self.trade_times.popleft()
        if self.halted:
            r = (False, f'HALT: {self.halted}')
        elif self.day_pnl <= -self.p.daily_kill_usdt:
            r = (False, f'daily loss {self.day_pnl:+.4f} <= -{self.p.daily_kill_usdt} U')
        elif self.pos or abs(self.view.get('amt') or 0) > 0:
            r = (False, 'position open' if self.pos else 'EXTERNAL position on venue')
        elif self.busy:
            r = (False, 'order in flight')
        elif (why := self.slot_block()) is not None:
            r = (False, why)
        elif now < self.pause_until:
            r = (False, f'{self.consec_losses} losses · pause {self.pause_until - now:.0f}s')
        elif now < self.cooldown_until:
            r = (False, f'cooldown {self.cooldown_until - now:.0f}s')
        elif len(self.trade_times) >= self.p.max_trades_hour:
            r = (False, f'{len(self.trade_times)} trades/h cap')
        else:
            r = (True, f'day {self.day_pnl:+.4f} U · {len(self.trade_times)}/{self.p.max_trades_hour} per h')
        pl = plan('BUY', f.get('price') or 0, self.spec, self.p, self.account.get('available')) if self.spec \
            else {'ok': False, 'why': 'filters not loaded'}
        o = (True, f'{pl["qty_str"]} · margin {pl["margin"]:.2f} U · risk {pl["loss_at_sl"]:.4f} U') if pl['ok'] \
            else (False, pl['why'])
        m = (bool(self.spec), 'SL + BE lock +0.12% + TP1 50% + trail' if self.spec else 'no filters')
        v = (self.started and not self.busy, f'{self.venue.label} ready' if self.started else 'venue not started')
        return swarm.Gates(z, r, o, m, v, cost_bps=rt)

    # ------------------------------------------------------------------ decide
    async def tick(self) -> Optional[Decision]:
        async with self.lock:
            return await self._tick()

    async def locked_sync(self) -> None:
        async with self.lock:
            await self.sync()

    async def _tick(self) -> Optional[Decision]:
        f = self.features = self.feed.features(self.clock())
        if self.pos:                                  # MARIN reacts on every WS tick, not every REST poll
            await self.manage()
        self.chop_locked = msig.chop_locked(f.get('ker'), self.chop_locked, self.sig_cfg)
        self.absorb = msig.absorption(f, self.sig_cfg)
        self.flow = msig.concordance(f, self.sig_cfg)
        self.tox = msig.toxicity(f, self.sig_cfg)
        if self.tox['toxic'] and self.p.vpin_gate:
            self.mark_toxic(self.tox['vpin'], self.tox.get('side'))
        self.macro = msig.macro_bias(f, self.sig_cfg)
        self.reg = msig.regime(f, self.sig_cfg)
        self.sig = msig.signals(f, self.sig_cfg, self.reg)
        d, votes, diag = swarm.evaluate(self.sig, self.gates(f), self.consensus, self.p.jev_min,
                                        self.p.min_confluence, self.p.edge_ratio_min, allow=self.reg['allow'])
        self.drift_probe = None
        self.blockers = []
        swarm_ok = d.allowed
        if d.allowed and not self.busy and not self.pos:
            ok, why = self.churn_gate(d, diag)
            if not ok:
                d = replace(d, allowed=False, reason=why)
        self.candidate = None
        stage = None
        if d.allowed and not self.busy and not self.pos:
            q = self.quality(d, diag)
            self.candidate = (self.clock(), q)
            rival = self.better_peer(q)
            if rival is not None:
                d = replace(d, allowed=False, reason=f'SCANNER YIELD -> {rival[0].p.symbol} '
                                                     f'({rival[1] * 100:.0f}% > {q * 100:.0f}%)')
                stage = 'YIELD'
        if self.observer and d.allowed:
            d = replace(d, allowed=False, reason=f'OBSERVER (no entries) · would be {d.side} {d.score * 100:.0f}%')
            self.candidate = None
            stage = 'OBSERVER'
        if not self.busy and not self.pos and self.funnel.due():
            self.funnel_sample(d, votes, diag, swarm_ok, stage)
        self.decision, self.votes, self.diag = d, votes, diag
        if d.allowed and not self.busy and not self.pos:
            await self.execute(d.side, d, 'MR' if self.lane(d) == 'MR' else (diag.get('path') or 'SWARM'))
        elif not d.allowed and not self.busy and not self.pos and self.spec and not self.observer:
            await self.chop_scalp(d, f)
        return d

    def funnel_sample(self, d: Decision, votes: list, diag: dict, swarm_ok: bool, stage: Optional[str]) -> None:
        """1 Hz funnel row: hard vetoes, swarm statistical veto and EVERY churn blocker of the candidate side
        (evaluated even when an earlier stage already said no), then yield / observer / submit."""
        side = d.side or (diag or {}).get('side')
        hard = [v.agent for v in votes if getattr(v, 'veto', False)]
        if not side:
            self.funnel.record('—', hard[0] if hard else 'NO_SIGNAL', hard)
            return
        cand = d if d.side == side else replace(d, side=side)
        lane = self.lane(cand)
        churn = self.blockers if swarm_ok else [k for k, _ in self.churn_checks(cand, diag or {})[0]]
        bl = hard + ([] if swarm_ok or hard else [xf.swarm_key(d.reason)]) + churn
        if stage:
            bl.append(stage)
        if hard:
            st = 'HARD'
        elif not swarm_ok:
            st = 'SWARM'
        elif churn:
            st = 'CHURN'
        else:
            st = stage or ('SUBMIT' if d.allowed else 'OTHER')
        self.funnel.record(lane, st, bl)

    def lane_feasibility(self, price: Optional[float] = None) -> dict:
        """Can any valid input pass every gate of a lane in this mode?  Fee-true RR range from the real config
        (no market data needed for demo; live needs a price + filters).  10-08v form §A."""
        p, gate = self.p, self.p.net_rr_min
        out = {}

        def row(state, rr_lo, rr_hi, why):
            feas = xf.rr_feasible(rr_hi, gate) if state in ('ENABLED', 'SHADOW') else None
            return {'state': state, 'rr_min': None if rr_lo is None else round(rr_lo, 3),
                    'rr_max': None if rr_hi is None else round(rr_hi, 3), 'gate': gate, 'feasible': feas,
                    'why': why}

        if p.max_loss_usdt is not None:                        # live: SL from the loss budget at min size
            px = price or self.feed.price() if self.feed is not None else price
            for lane, path in (('TREND', 'SWARM'), ('MR', 'MR')):
                if not px or not self.spec:
                    out[lane] = row('ENABLED', None, None, 'UNKNOWN: needs price + exchange filters')
                    continue
                pl = plan('BUY', px, self.spec, self.bracket_plan(path, {})['profile'], None)
                rr = self.plan_rr(pl) if pl['ok'] else None
                out[lane] = row('ENABLED', rr, rr, (f'loss budget {p.max_loss_usdt} U at {pl.get("qty_str")} '
                                                     f'-> SL {pl["sl_pct"]:.3f}%' if pl['ok'] else pl['why']))
        else:
            atr_lo = 0.01
            sl_min = self.asset_floors()[0]
            atr_hi = max(1.0, 4 * sl_min)
            tr = [self.bracket_plan('SWARM', f).get('rr_net') for f in
                  ({}, {'atr_pct': atr_lo}, {'atr_pct': atr_hi})]
            tr = [x for x in tr if x is not None]
            out['TREND'] = row('ENABLED', min(tr) if tr else None, max(tr) if tr else None,
                               'ATR brackets: TP >= rr_min x SL, SL >= asset floor')
            mr = [self.bracket_plan('MR', {'vwap_dev_pct': v}).get('rr_net') for v in (p.mr_tp_min, p.mr_tp_max, 5.0)]
            mr = [x for x in mr if x is not None]
            out['MR'] = row('ENABLED', min(mr) if mr else None, max(mr) if mr else None,
                            f'TP clamped [{p.mr_tp_min}, {p.mr_tp_max}] %, SL {p.mr_sl_pct} % + fees')
        sc = self.bracket_plan('SCALP', {}).get('rr_net')
        st = {'on': 'ENABLED', 'shadow': 'SHADOW'}.get(p.chop_scalper, 'OFF')
        out['SCALP'] = {**row(st, sc, sc, f'chop_scalper={p.chop_scalper}; exempt from net RR (break-even win rate '
                                          f'{1 / (1 + sc) * 100 if sc else 100:.0f}%), replay holdout < 0 bps'),
                        'gate': None, 'feasible': True if st == 'ENABLED' else None}
        if self.observer:
            for k in out:
                out[k] = {**out[k], 'state': 'OBSERVER', 'feasible': None,
                          'why': f'{p.symbol} observe_only (config) · ' + out[k]['why']}
        return out

    def quality(self, d: Decision, diag: dict) -> float:
        """Slot priority: conviction, +0.05 when the regime trend agrees, +0.05 when all three flows agree."""
        q = self.conviction(d, diag)
        if (self.reg or {}).get('name') == ('UP' if d.side == 'BUY' else 'DOWN'):
            q += 0.05
        if self.flow.get('buy_ok' if d.side == 'BUY' else 'sell_ok'):
            q += 0.05
        return q

    def better_peer(self, q: float, max_age: float = 1.0) -> Optional[tuple]:
        """A peer whose fresh candidate beats ours (ties: SCAN_SYMBOLS order) -> (engine, quality)."""
        if not self.p.slot_yield or self.p.multi_slot:            # one slot per symbol: nobody has to yield
            return None
        now, best = self.clock(), None
        order = {s: i for i, s in enumerate(SCAN_SYMBOLS)}
        for o in self.risk.engines:
            c = o.candidate
            if o is self or c is None or now - c[0] > max_age or o.pos or o.busy:
                continue
            ahead = c[1] > q + 1e-9 or (abs(c[1] - q) <= 1e-9 and order.get(o.p.symbol, 9) < order.get(self.p.symbol, 9))
            if ahead and (best is None or c[1] > best[1]):
                best = (o, c[1])
        return best

    def lane(self, d: Decision) -> str:
        """'MR' when the decision is the mean-reversion setup (MR signal on the decision side), else 'TREND'."""
        mr = (self.sig or {}).get('MR')
        return 'MR' if mr and mr['side'] == d.side else 'TREND'

    def mr_profile(self, f: Optional[dict] = None) -> Profile:
        return self.bracket_plan('MR', f)['profile']

    def bracket_plan(self, path: str = 'SWARM', f: Optional[dict] = None, route: str = 'gtx') -> dict:
        """TP / SL / trail for the next entry + gross and fee-true reward:risk.
        MR: TP = |VWAP dev| clamped [mr_tp_min, mr_tp_max], SL mr_sl_pct, whole size.
        TREND with ATR known: SL = max(floor, 1.0 ATR), TP1 = max(2.5 ATR, 2.5 SL), trail 1.2 ATR after TP1.
        Else the static profile. rr_net counts maker/taker fees and slip: win = TP - in - maker out,
        loss = SL + in + taker out + slip; be_wr = loss / (win + loss)."""
        f = self.features if f is None else f
        f, p = f or {}, self.p
        atr_pct, trail = f.get('atr_pct'), None
        if path == 'SCALP':
            tp, sl, src = p.scalp_tp_pct, p.scalp_sl_pct, 'SCALP'
            prof = replace(p, tp_pct=tp, tp1_frac=1.0, **({'sl_pct': sl} if p.max_loss_usdt is None else {}))
        elif path == 'MR':
            dev = abs(f.get('vwap_dev_pct') or 0.0) or p.mr_tp_pct
            tp, sl, src = min(p.mr_tp_max, max(p.mr_tp_min, dev)), p.mr_sl_pct, 'MR'
            prof = replace(p, tp_pct=tp, tp1_frac=1.0, **({'sl_pct': sl} if p.max_loss_usdt is None else {}))
        elif p.atr_scaler and p.max_loss_usdt is None and atr_pct:
            sl_min, tp_min = self.asset_floors()
            sl = max(sl_min, p.atr_sl_mult * atr_pct)
            tp = max(p.atr_tp1_mult * atr_pct, p.rr_min * sl, tp_min)
            trail, src = max(p.atr_trail_floor_pct, p.atr_trail_mult * atr_pct), 'ATR'
            prof = replace(p, sl_pct=sl, tp_pct=tp, atr_trail_pct=trail)
        elif p.max_loss_usdt is None and p.asset_brackets and self.p.symbol in ASSET_BRACKETS:
            sl_min, tp_min = self.asset_floors()
            sl = max(p.sl_pct, sl_min)
            tp, src = max(p.tp_pct, p.rr_min * sl, tp_min), 'STATIC'
            prof = replace(p, sl_pct=sl, tp_pct=tp)
        else:
            tp, sl, src, prof = p.tp_pct, p.sl_pct, 'STATIC', p
        e_in = (p.taker_bps + p.slip_bps) if route == 'ioc' else p.maker_bps
        tp_floor = p.tp_fee_mult * (e_in + p.maker_bps) / 100.0       # gross TP1 >= 3 x round-trip fee
        if tp < tp_floor - 1e-12 and path != 'SCALP':             # the scalper's 0.08 % TP is its spec
            tp = tp_floor
            prof = replace(prof, tp_pct=tp)
        win, loss = tp - (e_in + p.maker_bps) / 100.0, sl + (e_in + p.taker_bps + p.slip_bps) / 100.0
        ok = sl > 0 and loss > 0
        return {'profile': prof, 'src': src, 'atr_pct': atr_pct, 'sl_pct': sl, 'tp_pct': tp, 'trail_pct': trail,
                'rr_gross': tp / sl if sl > 0 else None, 'rr_net': win / loss if ok else None,
                'be_wr': loss / (win + loss) if ok and win > 0 else None}

    def asset_floors(self) -> tuple:
        """(SL min %, TP1 min %) for this coin; without the table: the generic ATR floor and no TP floor."""
        if self.p.asset_brackets and self.p.symbol in ASSET_BRACKETS:
            return ASSET_BRACKETS[self.p.symbol]
        return self.p.atr_sl_floor_pct, 0.0

    def parity_qty(self, price: float, prof: Profile, route: str = 'gtx') -> Optional[float]:
        """Equal-risk size for coins whose minimum is tiny: money at the stop ~ risk_target_usdt."""
        if not self.p.risk_parity or prof.qty or self.p.max_loss_usdt is not None or not price or not self.spec:
            return None
        e_in = (self.p.taker_bps + self.p.slip_bps) if route == 'ioc' else self.p.maker_bps
        risk_pct = (prof.sl_pct + (e_in + self.p.taker_bps + self.p.slip_bps) / 100.0) / 100.0
        step = self.spec['step']
        q = round(self.p.risk_target_usdt / (price * risk_pct) / step) * step
        return q if q > 0 else None

    def kelly(self) -> dict:
        """Kelly on this symbol's settled trades (net PnL): f* = p - (1 - p) / b, b = avg win / avg loss."""
        rows = [float(r['pnl']) for r in self.all_rows
                if r.get('symbol', self.p.symbol) == self.p.symbol and r.get('pnl') is not None]
        n, wins, losses = len(rows), [x for x in rows if x > 0], [-x for x in rows if x <= 0]
        out = {'n': n, 'min_n': self.p.kelly_min_trades, 'lambda': self.p.kelly_lambda, 'p': None, 'b': None,
               'f_star': None, 'f': None, 'state': 'SAMPLE'}
        if n < self.p.kelly_min_trades or not wins:
            out['state'] = 'SAMPLE' if n < self.p.kelly_min_trades else 'NO EDGE'
            if wins == [] and n >= self.p.kelly_min_trades:
                out.update(p=0.0, f_star=-1.0, f=-self.p.kelly_lambda)
            return out
        pw = len(wins) / n
        b = (sum(wins) / len(wins)) / (sum(losses) / len(losses)) if losses and sum(losses) > 0 else float('inf')
        f_star = pw - (1 - pw) / b if b != float('inf') else pw
        out.update(p=pw, b=None if b == float('inf') else b, f_star=f_star, f=self.p.kelly_lambda * f_star,
                   state='EDGE' if f_star > 0 else 'NO EDGE')
        return out

    def kelly_qty(self, price: float, prof: Profile, route: str = 'gtx') -> Optional[float]:
        """Quarter-Kelly size: money at the stop = min(cap, f x equity). None -> exchange minimum / profile qty."""
        if not self.p.kelly or self.p.max_loss_usdt is not None or not price or not self.spec:
            return None
        k = self.kelly()
        if not k.get('f') or k['f'] <= 0:
            return None
        equity = self.account.get('wallet') or self.account.get('available') or 0.0
        e_in = (self.p.taker_bps + self.p.slip_bps) if route == 'ioc' else self.p.maker_bps
        risk_pct = (prof.sl_pct + (e_in + self.p.taker_bps + self.p.slip_bps) / 100.0) / 100.0
        risk_usd = min(self.p.kelly_max_risk_usdt, k['f'] * equity)
        if risk_usd <= 0 or risk_pct <= 0:
            return None
        qty = min(risk_usd / (price * risk_pct), self.p.kelly_max_notional / price)
        qty = floor_step(qty, self.spec['step'])
        return qty if qty > (prof.qty or 0.0) else None

    def inventory(self) -> float:
        """Signed inventory for Avellaneda-Stoikov, normalised to the one-position slot: -1 / 0 / +1."""
        if self.pos:
            return 1.0 if self.pos['side'] == 'BUY' else -1.0
        amt = self.view.get('amt') or 0.0
        return (amt > 0) - (amt < 0)

    def mr_quote(self, side: str, f: dict, touch_px: float) -> float:
        """MR maker entry at the Avellaneda-Stoikov quote: never better than the touch (post-only), at most
        as_max_offset_bps behind the mid."""
        bid, ask, sig = f.get('bid'), f.get('ask'), f.get('sigma_bps')
        if not (bid and ask and sig):
            return touch_px
        qt = msig.as_quote((bid + ask) / 2.0, self.inventory(), sig, self.p.as_gamma, self.p.as_k,
                           self.p.as_tau_s, self.p.as_max_offset_bps)
        tick = self.spec['tick']
        if side == 'BUY':
            return min(touch_px, round_tick(qt['bid'], tick, 'down'))
        return max(touch_px, round_tick(qt['ask'], tick, 'up'))

    def mark_toxic(self, v: Optional[float], side: Optional[str] = None) -> None:
        """Asymmetric: remember WHICH side the toxic flow pushes; only entries against it are vetoed."""
        now = self.clock()
        side = side or self.feed.vpin_side()
        if now >= self.toxic_until or side != self.toxic_side:
            against = {'BUY': 'SELL', 'SELL': 'BUY'}.get(side)
            self.event(f'VPIN TOXIC {(v or 0):.2f} > {self.sig_cfg.vpin_max} ({side or "?"} flow): cấm {against or "—"} '
                       f'{self.p.vpin_pause_s:g}s, {side or "—"} vẫn được vào')
        self.toxic_until, self.toxic_side, self.toxic_vpin = now + self.p.vpin_pause_s, side, v

    def toxic_blocks(self, side: str) -> bool:
        return (self.p.vpin_gate and self.clock() < self.toxic_until and self.toxic_side is not None
                and side != self.toxic_side)

    def radar(self) -> dict:
        """One scanner row: BTC: CHOP | SOL: TREND BUY (84%) | DOGE: MR SCAN."""
        d, f, c = self.decision, self.features or {}, self.sig_cfg
        k, mr = f.get('ker'), (self.sig or {}).get('MR')
        score = self.conviction(d, self.diag or {}) if d else 0.0
        vv = self.vfr_view(f)
        if self.pos:
            label = f'{"LONG" if self.pos["side"] == "BUY" else "SHORT"} OPEN'
        elif self.busy:
            label = 'ORDER'
        elif self.observer:
            label = 'OBSERVER'
        elif self.p.vfr_gate and vv['frozen'] and vv['vfr'] is not None:
            label = f'FROZEN VFR {vv["vfr"]:.1f}'
        elif self.p.drift_lock and (self.reg or {}).get('name') == 'DRIFT':
            label = 'DRIFT LOCK'
        elif mr:
            label = f'MR {mr["side"]} ({score * 100:.0f}%)'
        elif k is None:
            label = 'WARMUP'
        elif self.chop_locked:
            label = 'MR SCAN' if k < min(c.ker_lock, c.mr_ker_max) else 'CHOP'   # BTC: MR off -> CHOP
        elif d is not None and d.side:
            label = f'TREND {d.side} ({score * 100:.0f}%)'
        else:
            label = 'TREND SCAN'
        return {'symbol': self.p.symbol, 'label': label, 'ker': k, 'regime': (self.reg or {}).get('name'),
                'flow': self.flow.get('state'),
                **{k: self.bracket_plan('SWARM', f).get(k) for k in ('sl_pct', 'tp_pct', 'src')},
                'score': score, 'allowed': bool(d and d.allowed), 'reason': (d.reason if d else '')[:140],
                'pos': bool(self.pos), 'price': f.get('price'), **self.coin_pnl(),
                'vfr': vv['vfr'], 'atr15_pct': vv['atr15_pct'], 'frozen': bool(self.p.vfr_gate and vv['frozen']),
                'blockers': list(self.funnel.last_blockers), 'observer': self.observer, **self.funnel.counters()}

    def coin_pnl(self) -> dict:
        """Realised net PnL of this coin over the whole ledger (radar: BTC -0.79 U | SOL +0.33 U ...)."""
        rows = [float(r['pnl']) for r in self.all_rows
                if r.get('symbol', self.p.symbol) == self.p.symbol and r.get('pnl') is not None]
        pnl = sum(rows)
        return {'pnl': round(pnl, 4), 'n': len(rows), 'wins': sum(1 for x in rows if x > 0),
                'gross_win': round(sum(x for x in rows if x > 0), 4),
                'tag': 'PROFIT MAKER' if pnl > 0 else ('BLEEDER' if pnl < 0 else ''),
                'alt': self.is_alt(), 'risk_cap': self.risk_cap()}

    def conviction(self, d: Decision, diag: dict) -> float:
        """Swarm score the governor reads: raw vote (60/80/100 % steps with 3 alpha agents) or the JEV lift."""
        jev = (diag.get('jev') or 0.0) / 10.0 if diag.get('gates_ok') else 0.0
        return max(d.score, diag.get('consensus') or 0.0, jev)

    def chop_reason(self) -> str:
        f, c = self.features or {}, self.sig_cfg
        k = f.get('ker')
        if k is None:
            return f'CHOP LOCKED KER warming {f.get("ker_span_s") or 0:.0f}/{c.ker_min_span_s:.0f}s'
        if k < c.ker_lock:
            return f'CHOP LOCKED KER {k:.2f} < {c.ker_lock:.2f}'
        return f'CHOP LOCKED KER {k:.2f} < {c.ker_unlock:.2f} (hysteresis)'

    def churn_checks(self, d: Decision, diag: dict) -> tuple:
        """Every evaluable churn gate for decision `d` (no early return) -> ([(gate, why), ...], drift_probe).
        Order = the hot-path priority; the first entry is what churn_gate returns.  Read-only (10-08v funnel)."""
        score, edge = self.conviction(d, diag), diag.get('edge_bps') or 0.0
        lane = self.lane(d)
        reg = self.reg or {}
        out, probe = [], None
        if self.toxic_blocks(d.side):
            out.append(('VPIN', f'VPIN TOXIC {(self.toxic_vpin or 0):.2f} {self.toxic_side} flow · cấm {d.side} '
                                f'{self.toxic_until - self.clock():.0f}s'))
        if self.p.vfr_gate:
            vv = self.vfr_view()
            if vv['frozen']:
                out.append(('VFR', vv['why']))
        if self.p.drift_lock and reg.get('name') == 'DRIFT':
            acc = self.drift_acceleration(d.side)
            if acc:
                probe = acc
            else:
                out.append(('DRIFT', f'DRIFT LOCK: {reg.get("label", "DRIFT")} · {reg.get("why", "")}'))
        if lane == 'TREND' and self.p.ker_gate and self.chop_locked:
            out.append(('KER', self.chop_reason()))
        if self.p.macro_gate and d.side not in self.macro.get('allow', ('BUY', 'SELL')):
            out.append(('MACRO', f'MACRO 5m {self.macro["name"]}: cấm {d.side} · {self.macro.get("why", "")}'))
        if self.p.absorb_gate and self.absorb.get('blocks') == d.side:
            out.append(('ABSORB', f'ABSORPTION TRAP {d.side} · {self.absorb.get("why", "")}'))
        if self.p.concordance_gate and (lane == 'TREND' or self.p.concordance_mr):
            if not self.flow.get('buy_ok' if d.side == 'BUY' else 'sell_ok'):
                out.append(('FLOW', f'FLOW CONFLICT {d.side} (LOCKED) · {self.flow.get("why", "")}'))
        if lane == 'MR' and self.p.mr_flow_gate:
            why = msig.mr_flow_veto(d.side, self.features or {}, self.p.mr_ofi_max,
                                    self.p.mr_cvd60_usd * self.feed.cfg.usd_scale)
            if why:
                out.append(('MR_FLOW', why))
        if self.p.net_rr_min and self.p.max_loss_usdt is None:      # live (loss-budget SL): checked in execute()
            bp = self.bracket_plan('MR' if lane == 'MR' else 'SWARM', self.features)
            rr = bp.get('rr_net')
            if rr is None or rr < self.p.net_rr_min:
                out.append(('NET_RR', f'NET RR {"—" if rr is None else f"{rr:.2f}"} < {self.p.net_rr_min:g} '
                                      f'({lane} TP {bp["tp_pct"]:.3f}% / SL {bp["sl_pct"]:.3f}% after fees, '
                                      f'break-even win rate {(bp.get("be_wr") or 1) * 100:.0f}%)'))
        if lane == 'MR':
            if score < self.p.mr_min_score:
                out.append(('MR_SCORE', f'MR score {score * 100:.1f}% < {self.p.mr_min_score * 100:.1f}%'))
            return out, probe
        if self.p.min_velocity_pct:
            vel = (self.features or {}).get('px_vel_15s')
            sv = None if vel is None else (vel if d.side == 'BUY' else -vel)
            if sv is None or sv < self.p.min_velocity_pct:
                out.append(('VELOCITY', f'VELOCITY {d.side} 15s {"—" if vel is None else f"{vel:+.3f}%"} '
                                        f'< {self.p.min_velocity_pct}% cùng chiều'))
        if score < self.p.min_score:
            out.append(('SCORE', f'CHURN score {score * 100:.1f}% < {self.p.min_score * 100:.1f}%'))
        if edge < self.p.min_edge_bps:
            out.append(('EDGE', f'CHURN edge {edge:.1f} bps < {self.p.min_edge_bps:g} bps '
                                f'(phí maker 2 chiều {2 * self.p.maker_bps:g} bps)'))
        return out, probe

    def churn_gate(self, d: Decision, diag: dict) -> tuple:
        """Anti-churn (2026-10-07f): auto entries need score >= min_score AND expected edge >= min_edge_bps.
        10-08v: every blocker is evaluated (self.blockers); the first one is returned, as before."""
        out, probe = self.churn_checks(d, diag)
        self.blockers = [k for k, _ in out]
        if out:
            return False, out[0][1]
        self.drift_probe = probe
        return True, ''

    # ------------------------------------------------------------------ VESKA (Post-Only GTX only)
    def gtx_price(self, side: str, f: dict) -> Optional[float]:
        """Join the touch; step 1 tick inside when the spread is >= 2 ticks (still post-only)."""
        tick, bid, ask = self.spec['tick'], f.get('bid'), f.get('ask')
        if not bid or not ask:
            return None
        bq, aq = f.get('bid_qty') or 0.0, f.get('ask_qty') or 0.0
        wide = ask - bid >= 2 * tick - 1e-12
        # micro-price: I = bid_qty / (bid_qty + ask_qty); P_micro = I*ask + (1-I)*bid (queue-weighted fair value)
        micro = (bq / (bq + aq)) * ask + (aq / (bq + aq)) * bid if bq + aq > 0 else (bid + ask) / 2
        if side == 'BUY':
            px = (ask - tick if self.p.aggressive_quote else max(bid + tick, micro - tick)) if wide else bid
            return round_tick(min(max(px, bid), ask - tick), tick, 'down')
        px = (bid + tick if self.p.aggressive_quote else min(ask - tick, micro + tick)) if wide else ask
        return round_tick(max(min(px, ask), bid + tick), tick, 'up')

    def route(self, decision: Decision, f: dict) -> str:
        """VESKA router: 'ioc' (bounded taker) on super-momentum, else 'gtx' (pegged maker)."""
        if not self.p.super_edge:
            return 'gtx'
        diag = self.diag or {}
        cons, jev = diag.get('consensus', 0.0), diag.get('jev', 0.0)
        cvd10 = (f.get('cvd_10s') or 0.0) * (1 if decision.side == 'BUY' else -1)   # flow WITH the trade
        need = self.p.super_cvd_usd * self.feed.cfg.usd_scale
        ker = f.get('ker') or 0.0
        if cvd10 >= need and ker >= self.p.super_ker and (cons >= self.p.super_consensus or jev >= self.p.super_jev):
            return 'ioc'
        return 'gtx'

    async def execute(self, side: str, decision: Decision, path: str, tag: str = 'AUTO',
                      entry: Optional[str] = None, hold_s: Optional[float] = None) -> dict:
        """Entry + one engine_orders.jsonl row per attempt that got past the slot check."""
        self._xm = None
        res = await self._execute(side, decision, path, tag, entry, hold_s)
        if self._xm is not None and self.p.order_log:
            self._log_order(res, self._xm, decision)
        return res

    def _log_order(self, res: dict, xm: dict, decision: Decision) -> None:
        err = str(res.get('error') or '')
        if res.get('ok'):
            q = float(res.get('qty') or 0)
            outcome = 'FILLED' if q >= (xm.get('qty_req') or 0) - 1e-12 else 'PARTIAL'
        elif 't_send' not in xm:
            outcome = 'REFUSED'
        elif 'post-only' in err:
            outcome = 'REJECTED'
        elif 'VPIN' in err:
            outcome = 'TOXIC'
        elif 'khớp' in err:
            outcome = 'UNFILLED'
        else:
            outcome = 'ERROR'
        ms = lambda a, b: round((xm[b] - xm[a]) * 1000, 1) if xm.get(a) and xm.get(b) else None
        row = {'t': self.clock(), 'schema': 1, 'mode': self.p.mode, 'venue': self.venue.label, 'symbol': self.p.symbol,
               'side': xm.get('side'), 'path': xm.get('path'), 'tag': xm.get('tag'), 'route': xm.get('route'),
               'outcome': outcome, 'qty_req': xm.get('qty_req'), 'qty_filled': float(res.get('qty') or 0) or None,
               'px_first': xm.get('px_first'), 'px_last': xm.get('px_last'), 'reprices': xm.get('reprices'),
               'touch_last': xm.get('touch_last'), 'touch_qty_last': xm.get('touch_qty_last'),
               'post_rejects': xm.get('post_rejects', 0), 'signal_mid': xm.get('signal_mid'),
               'venue_mid': xm.get('venue_mid'), 'basis_bps': basis_bps(xm.get('signal_mid'), xm.get('venue_mid')),
               'decide_to_send_ms': ms('t0', 't_send'), 'ack_ms': xm.get('ack_ms'),
               'fill_wait_ms': ms('t_ack', 't_fill'), 'total_ms': ms('t0', 't_done'),
               'score': decision.score, 'consensus': (self.diag or {}).get('consensus'),
               'edge_bps': (self.diag or {}).get('edge_bps'), 'policy': policy_id(self.p), 'code': CODE_VERSION,
               'crossed': xm.get('crossed'), 'cross_check': xm.get('cross_check'), 'dyn_ttl_s': xm.get('dyn_ttl_s'),
               'dyn_cut': xm.get('dyn_cut'), 'orphan': xm.get('orphan'), 'error': err[:160] or None}
        self.funnel.outcome(outcome)
        try:
            path = self.ledger.parent / 'engine_orders.jsonl'
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'a', encoding='utf-8') as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + '\n')
        except OSError:
            pass

    async def _execute(self, side: str, decision: Decision, path: str, tag: str = 'AUTO',
                       entry: Optional[str] = None, hold_s: Optional[float] = None) -> dict:
        if self.observer:
            return {'ok': False, 'error': f'OBSERVER: {self.p.symbol} is observe-only (no entries)'}
        if self.busy or self.pos or self.slot_block():
            why = self.slot_block()
            return {'ok': False, 'error': 'busy or position open' if not why else f'SCANNER: {why}'}
        self.busy, self.phase = True, 'EXECUTING'
        sym, tick, step = self.p.symbol, self.spec['tick'], self.spec['step']
        t0 = time.perf_counter()
        xm = self._xm = {'t0': t0, 'side': side, 'path': path, 'tag': tag}
        try:
            await self.refresh_book(0.0)
            f = {**self.feed.features(self.clock()), **{k: v for k, v in self.book().items() if v is not None}}
            xm.update(signal_mid=self.feed.price(), venue_mid=self.book().get('price'))
            px = self.gtx_price(side, f)
            if px is None:
                return {'ok': False, 'error': 'no book'}
            route = self.route(decision, f) if tag == 'AUTO' and path != 'MR' else 'gtx'
            bp = self.bracket_plan(path, f, route)
            base = bp['profile']
            touch = f.get('ask') if side == 'BUY' else f.get('bid')
            if path == 'MR':
                px = self.mr_quote(side, f, px)
            kq = self.kelly_qty(px if route == 'gtx' else touch, base, route)
            pq = self.parity_qty(px if route == 'gtx' else touch, base, route)
            if pq and (kq is None or pq > kq):
                kq = pq
            mult = self.size_mult(decision, self.diag, f) if tag == 'AUTO' else 1.0
            ref = px if route == 'gtx' else touch
            floor_q = ALT_SIZE_FLOOR.get(sym) if (self.p.alt_size_floor and tag == 'AUTO'
                                                   and path not in ('MR', 'SCALP')) else None
            if floor_q and (kq or base.qty or 0.0) < floor_q:
                kq = floor_step(min(floor_q, ALT_SIZE[sym][2] / ref), step)
            if mult > 1.0:
                kq = ceil_step(max(kq or base.qty or 0.0, self.min_qty(ref)) * mult, step)
            boosted = tag == 'AUTO' and path != 'MR' and self.alt_boosted(decision, self.diag)
            cap = self.alt_cap(ref, boosted)
            if boosted and cap and cap > (kq or 0.0):
                kk = self.alt_kelly()
                self.event(f'ALPHA PRIORITY {sym.replace("USDT", "")} vote '
                           f'{max(decision.score, (self.diag or {}).get("consensus") or 0.0) * 100:.0f}% >= '
                           f'{self.p.alt_boost_score * 100:.0f}% -> size {fmt(kq or 0.0, step)} -> {fmt(cap, step)}'
                           f' (Kelly {kk["n"]} lệnh, f {0 if kk["f"] is None else kk["f"]:.3f}, '
                           f'scale {kk["scale"]:.2f})')
                kq = cap
            superc = tag == 'AUTO' and path != 'MR' and self.super_conviction(decision, self.diag, f)
            if superc:
                tgt = floor_step(min(SUPER_SIZE[sym], ALT_SIZE[sym][2] / ref), step)
                if tgt > (kq or 0.0):
                    self.event(f'ALPHA CONCENTRATION {sym.replace("USDT", "")} vote '
                               f'{max(decision.score, (self.diag or {}).get("consensus") or 0.0) * 100:.1f}% · OFI '
                               f'{(f.get("ofi_10s") or 0):+.2f} -> size {fmt(kq or 0.0, step)} -> {fmt(tgt, step)}')
                    kq = tgt
                cap = max(cap or 0.0, tgt)
            if cap and kq and kq > cap:
                kq = cap                                    # every SOL/DOGE size path stops at the coin cap
            probe_mult = (self.p.scalp_size_mult if path == 'SCALP' else
                          self.p.drift_probe_mult if (tag == 'AUTO' and self.drift_probe) else 1.0)
            if probe_mult < 1.0:
                kq = floor_step(max(kq or base.qty or 0.0, self.min_qty(ref)) * probe_mult, step)
                kq = max(kq, self.min_qty(ref))                    # never below what the venue accepts
            mkpl = lambda q: plan(side, px if route == 'gtx' else touch, self.spec,
                                  replace(base, entry='gtx' if route == 'gtx' else 'market'),
                                  self.account.get('available'), qty=q)
            pl = mkpl(kq)
            if not pl['ok']:
                self.event(f'OKAPI refused {side}: {pl["why"]}')
                return {'ok': False, 'error': pl['why']}
            rr = self.plan_rr(pl, route)
            if tag == 'AUTO' and path != 'SCALP' and self.p.net_rr_min and (rr is None or rr < self.p.net_rr_min):
                msg = (f'ZEPHR NET RR {"—" if rr is None else f"{rr:.2f}"} < {self.p.net_rr_min:g} '
                       f'(TP {pl["tp_pct"]:.3f}% / SL {pl["sl_pct"]:.3f}% + fees {pl["fees_pct"]:.3f}%)')
                self.event(f'OKAPI refused {side}: {msg}')
                return {'ok': False, 'error': msg}
            probe = None
            if tag == 'AUTO' and self.p.alt_boost and not self.is_alt() and self.p.btc_probe_qty:
                vv = self.vfr_view(f)
                why_probe = ('VFR ' + ('—' if vv['vfr'] is None else f'{vv["vfr"]:.2f}') + f' < {self.p.vfr_full:g}'
                             if vv['vfr'] is None or vv['vfr'] < self.p.vfr_full else
                             f'risk {pl["loss_at_sl"]:.3f} U > {self.risk_cap():g} U' if pl['loss_at_sl'] >
                             self.risk_cap() + 1e-9 else None)
                if why_probe:
                    pl2 = mkpl(self.p.btc_probe_qty)
                    fits = (self.p.btc_probe_min_tradable and pl2['ok']
                            and pl2['loss_at_sl'] <= self.risk_cap() + 1e-9)       # venue minimum, inside 15 %
                    if not fits and (not pl2['ok'] or pl2['qty'] > self.p.btc_probe_qty + 1e-12):
                        msg = (f'BTC MICRO-PROBE {self.p.btc_probe_qty:g} ({why_probe}) dưới sàn sàn giao dịch '
                               f'(min qty {self.spec["min_qty"]:g} / ${self.spec["min_notional"]:g})'
                               + (f'; size nhỏ nhất {pl2["qty_str"]} rủi ro {pl2["loss_at_sl"]:.3f} U > '
                                  f'{self.risk_cap():g} U' if pl2['ok'] else '') + ' -> bỏ lệnh')
                        self.event(f'OKAPI refused {side}: {msg}')
                        self.cooldown_until = self.clock() + self.p.probe_refuse_cooldown_s   # no event spam
                        return {'ok': False, 'error': msg}
                    pl, probe = pl2, why_probe
                    self.event(f'BTC MICRO-PROBE {pl["qty_str"]} ({why_probe}) · rủi ro {pl["loss_at_sl"]:.4f} U')
            why = self.portfolio_check(pl)
            if why:
                self.event(f'RUNE portfolio refused {side} {pl["qty_str"]}: {why}')
                return {'ok': False, 'error': why}
            self.pending_risk = pl['loss_at_sl']
            xm.update(qty_req=pl['qty'], route=route, px_first=px)
            intent = MicroIntent(sym, side, pl['qty_str'], route, tag, int(self.clock() * 1000))
            token = self.authority.issue(intent, decision)
            self.authorized += 1
            self.auth_at = self.clock()
            self.authority.consume(token, intent)      # VESKA spends the one-use token right before routing
            cid = f'gk{tag[0].lower()}-' + uuid.uuid4().hex[:26]
            posted_ms = int(self.clock() * 1000)       # entry fill can land any time in the GTX window
            px0, reprices, toxic_abort = px, 0, None
            if route == 'ioc':
                d = 1 if side == 'BUY' else -1
                lim = round_tick(touch * (1 + d * self.p.ioc_max_slip_pct / 100.0), tick, 'up' if d > 0 else 'down')
                xm['t_send'] = time.perf_counter()
                fill, filled = await self.venue.ioc(sym, side, pl['qty_str'], fmt(lim, tick), cid)
                xm['t_ack'] = xm['t_fill'] = time.perf_counter()
                xm['ack_ms'] = round((xm['t_ack'] - xm['t_send']) * 1000, 1)
                px = lim
            else:
                backoff = 0
                xm['t_send'] = time.perf_counter()
                while True:
                    try:
                        tc = time.perf_counter()
                        xm['cid_live'] = cid                 # 10-08v: known to the error path (orphan cancel)
                        await self.venue.limit_gtx(sym, side, pl['qty_str'], fmt(px, tick), cid)
                        xm['t_ack'] = time.perf_counter()
                        xm['ack_ms'] = round((xm['t_ack'] - tc) * 1000, 1)       # send -> REST ack (network)
                        break
                    except PostOnlyReject:
                        xm['post_rejects'] = backoff + 1
                        if backoff >= self.p.gtx_backoff_ticks:
                            self.cooldown_until = self.clock() + self.p.unfilled_cooldown_s
                            self.event(f'GTX {side} @ {fmt(px, tick)} rejected post-only x{backoff + 1} (book moved)'
                                       f' - no taker fallback, retry after cooldown')
                            return {'ok': False, 'error': 'post-only rejected (would take liquidity)'}
                        backoff += 1          # anti-taker guard: step 1 tick AWAY from the touch, never MARKET
                        px = round_tick(px - (tick if side == 'BUY' else -tick), tick,
                                        'down' if side == 'BUY' else 'up')
                        px0 = px
                        cid = f'gk{tag[0].lower()}-' + uuid.uuid4().hex[:26]
                        self.event(f'GTX {side} post-only reject -> back off 1 tick to {fmt(px, tick)} '
                                   f'({backoff}/{self.p.gtx_backoff_ticks})')
                st, deadline = {}, time.monotonic() + self.p.gtx_ttl_s
                peg = self.p.peg and decision.score >= self.p.peg_min_score and path != 'MR'
                last_post = t_post = time.monotonic()
                auto = tag == 'AUTO' and path not in ('MR', 'SCALP')
                cross_mode = self.p.super_cross if auto else 'off'
                dyn_mode = self.p.dyn_ttl if auto else 'off'
                cross_at = t_post + self.p.super_cross_after_s if cross_mode in ('shadow', 'on') else None
                do_cross, dyn_logged, mid0 = False, False, self.feed.price()
                while True:
                    now_m = time.monotonic()
                    if now_m >= deadline:
                        # 10-08w dynamic TTL: extend 1 s at a time while the signal holds, never past dyn_ttl_max_s
                        if dyn_mode not in ('shadow', 'on') or now_m - t_post >= self.p.dyn_ttl_max_s - 1e-9:
                            break
                        keep, why_keep = await self.dyn_keep(side, decision, px0, mid0)
                        if dyn_mode == 'shadow' or not keep:
                            if dyn_mode == 'shadow' and keep and not dyn_logged:
                                dyn_logged = True
                                self._shadow_row({'outcome': 'SHADOW_DYN', 'side': side, 'path': path, 'px': px,
                                                  'qty': pl['qty'], 'why': why_keep, 'score': decision.score,
                                                  'max_s': self.p.dyn_ttl_max_s})
                            if dyn_mode == 'on' and not keep:
                                xm['dyn_cut'] = why_keep
                            break
                        deadline = min(now_m + 1.0, t_post + self.p.dyn_ttl_max_s)
                        xm['dyn_ttl_s'] = round(deadline - t_post, 1)
                    elif xm.get('dyn_ttl_s') and dyn_mode == 'on':
                        keep, why_keep = await self.dyn_keep(side, decision, px0, mid0)
                        if not keep:                              # adverse / signal gone: pull it at once
                            xm['dyn_cut'] = why_keep
                            break
                    if cross_at is not None and now_m >= cross_at:
                        cross_at = None
                        why_x, info = await self.cross_check(decision, side, pl, base)
                        xm['cross_check'] = why_x or 'OK'
                        if why_x is None and cross_mode == 'on':
                            do_cross = True
                            break
                        if why_x is None:
                            self._shadow_row({'outcome': 'SHADOW_CROSS', 'side': side, 'path': path, 'qty': pl['qty'],
                                              'gtx_px': px, **info, 'score': decision.score,
                                              'after_s': self.p.super_cross_after_s})
                    st = await self.venue.order(sym, cid)
                    if st.get('status') == 'FILLED':
                        xm['t_fill'] = time.perf_counter()               # seen by polling (+<= 0.1 s + REST)
                        break
                    v = self.feed.vpin_now() if self.p.vpin_gate else None
                    vs = self.feed.vpin_side() if v is not None else None
                    if v is not None and v > self.sig_cfg.vpin_max and vs is not None and vs != side:
                        self.mark_toxic(v, vs)                       # toxic flow AGAINST our quote: pull it now
                        toxic_abort = v
                        break
                    if peg and time.monotonic() - last_post >= self.p.peg_interval_s:
                        # Dynamic pegged maker: follow the touch while the book walks away, never cross it,
                        # never further than peg_max_drift_pct from the first quote (bounded chase).
                        await self.refresh_book(0.0)
                        new = self.gtx_price(side, self.book())
                        d = 1 if side == 'BUY' else -1
                        if (new is not None and d * (new - px) > 0
                                and abs(new / px0 - 1) * 100 <= self.p.peg_max_drift_pct):
                            await self.venue.cancel(sym, cid)
                            st = await self.venue.order(sym, cid)
                            if float(st.get('filled') or 0) > 0:      # partial/full fill raced the cancel: keep it
                                break
                            cid = f'gk{tag[0].lower()}-' + uuid.uuid4().hex[:26]
                            xm['cid_live'] = cid
                            try:
                                await self.venue.limit_gtx(sym, side, pl['qty_str'], fmt(new, tick), cid)
                            except PostOnlyReject:
                                st = {}
                                break
                            px, last_post, reprices = new, time.monotonic(), reprices + 1
                        else:
                            last_post = time.monotonic()
                    await asyncio.sleep(0.1)
                if st.get('status') != 'FILLED':
                    await self.venue.cancel(sym, cid)
                    st = await self.venue.order(sym, cid)
                filled, fill = float(st.get('filled') or 0), float(st.get('avg') or px)
                if do_cross:
                    filled, fill = await self.cross_remaining(side, pl, filled, fill, xm, tag)
            if reprices:
                self.event(f'PEG {side} repriced x{reprices} {fmt(px0, tick)} -> {fmt(px, tick)} '
                           f'({"filled" if filled > 0 else "unfilled"})')
            ms = (time.perf_counter() - t0) * 1000
            bb = self.book()
            xm.update(t_done=time.perf_counter(), reprices=reprices, px_last=px,
                      touch_last=bb.get('bid') if side == 'BUY' else bb.get('ask'),
                      touch_qty_last=bb.get('bid_qty') if side == 'BUY' else bb.get('ask_qty'))
            if filled > 0 and not xm.get('t_fill'):
                xm['t_fill'] = xm['t_done']                       # partial fill found at cancel time
            self.latency.append(ms)
            if filled <= 0:
                self.cooldown_until = self.clock() + self.p.unfilled_cooldown_s
                self.phase = 'SCANNING'
                if route == 'ioc':
                    self.event(f'IOC {side} {pl["qty_str"]} limit {fmt(px, tick)} found no liquidity inside '
                               f'{self.p.ioc_max_slip_pct}% -> expired, rescan in {self.p.unfilled_cooldown_s:g}s')
                    return {'ok': False, 'error': f'IOC không khớp trong biên trượt {self.p.ioc_max_slip_pct}%',
                            'latency_ms': ms, 'route': 'IOC'}
                if toxic_abort is not None:
                    self.event(f'GTX {side} {pl["qty_str"]} @ {fmt(px, tick)} cancelled: VPIN {toxic_abort:.2f} > '
                               f'{self.sig_cfg.vpin_max} (toxic flow)')
                    return {'ok': False, 'error': f'VPIN {toxic_abort:.2f} > {self.sig_cfg.vpin_max}: huỷ lệnh maker '
                                                  f'chờ khớp (dòng tiền độc)', 'latency_ms': ms, 'route': 'GTX'}
                self.event(f'GTX {side} {pl["qty_str"]} @ {fmt(px, tick)} not filled in {self.p.gtx_ttl_s:g}s '
                           f'-> cancelled (peg drift cap {self.p.peg_max_drift_pct}%), rescan in {self.p.unfilled_cooldown_s:g}s')
                return {'ok': False, 'error': f'GTX không khớp trong {self.p.gtx_ttl_s:g}s (peg tối đa '
                                              f'{self.p.peg_max_drift_pct}%) - đã huỷ, quét lại sau {self.p.unfilled_cooldown_s:g}s',
                        'latency_ms': ms, 'route': 'GTX'}
            qty = floor_step(filled, step)
            qty_str = fmt(qty, step)
            sl, tp = brackets(side, fill, pl, tick)
            half = floor_step(qty * base.tp1_frac, step)
            split = half >= self.spec['min_qty'] and qty - half >= self.spec['min_qty'] - 1e-12
            opp = 'SELL' if side == 'BUY' else 'BUY'
            self.pos = {'symbol': sym, 'side': side, 'qty': qty, 'qty_str': qty_str, 'entry': fill,
                        'opened': self.clock(), 'opened_ms': posted_ms - 1000, 'sl': sl, 'tp': tp,
                        'sl0': sl, 'sl_cid': 'gs-' + uuid.uuid4().hex[:28], 'tp_cid': 'gt-' + uuid.uuid4().hex[:28],
                        'stop_kind': 'SL', 'tp_qty': half if split else qty, 'split': split, 'tp1_filled': False,
                        'be_locked': False, 'mbe_locked': False, 'trail_on': False, 'peak': fill, 'mfe_pct': 0.0,
                        'adverse_usd': 0.0, 'regime': (self.reg or {}).get('name'),
                        'trigger_usd': abs(f.get('cvd_exit_win') or 0.0), 'flow_limit': self.flow_limit(f),
                        'edge_bps': (self.diag or {}).get('edge_bps'), 'ker': f.get('ker'),
                        'atr_pct': bp['atr_pct'], 'atr_trail_pct': bp['trail_pct'], 'bracket_src': bp['src'],
                        'rr_gross': bp['rr_gross'], 'rr_net': bp['rr_net'], 'be_wr': bp['be_wr'],
                        'vpin': f.get('vpin'), 'ofi': f.get('ofi_10s'), 'kelly_f': self.kelly().get('f'),
                        'quote_px': px, 'size_mult': mult, 'alt_boost': boosted, 'probe': probe, 'super_size': superc,
                        'drift_probe': self.drift_probe if tag == 'AUTO' else None,
                        **({'eject_s': self.p.scalp_be_s, 'eject_mfe': 1e9} if path == 'SCALP' else {}),
                        'px_vel_15s': f.get('px_vel_15s'), 'macro': self.macro.get('name'),
                        'tag': tag, 'path': path, 'score': decision.score, 'reason': decision.reason,
                        'hold_s': hold_s or self.p.max_hold_s, 'latency_ms': ms,
                        'tp_pct': pl['tp_pct'], 'sl_pct': pl['sl_pct'],
                        'entry_type': xm.get('entry_type') or route.upper(),
                        'entry_fee_bps': xm.get('entry_fee_bps') or (self.p.taker_bps if route == 'ioc' else
                                                                      self.p.maker_bps),
                        'crossed': xm.get('crossed'), 'dyn_ttl_s': xm.get('dyn_ttl_s'),
                        'signal_mid': xm.get('signal_mid'), 'venue_mid': xm.get('venue_mid'),
                        'basis_bps': basis_bps(xm.get('signal_mid'), xm.get('venue_mid')),
                        'ack_ms': xm.get('ack_ms'),
                        'fill_wait_ms': round((xm['t_fill'] - xm['t_ack']) * 1000, 1)
                        if xm.get('t_fill') and xm.get('t_ack') else None,
                        'markout_bps': {}}
            self.trade_times.append(self.clock())
            how = (f'IOC TAKER super-edge, cons {(self.diag or {}).get("consensus", 0) * 100:.1f}% JEV '
                   f'{(self.diag or {}).get("jev", 0):.1f}' if route == 'ioc' else 'POST-ONLY GTX maker')
            self.event(f'VESKA {tag} {side} {qty_str} {sym} @ {fill:g} ({how}, {ms:.0f} ms) · {path} '
                       f'{decision.score * 100:.1f}% · TP1 {tp:g} x{fmt(self.pos["tp_qty"], step)} · SL {sl:g}')
            try:
                await self.place_entry_stop(sym, opp, fmt(sl, tick), self.pos['sl_cid'])
            except Exception as exc:
                self.event(f'MARIN stop rejected ({type(exc).__name__}: {exc}) -> closing at market')
                await self.close('NO_STOP')
                return {'ok': False, 'error': 'stop rejected, closed'}
            if not await self.venue.take_profit(sym, opp, fmt(self.pos['tp_qty'], step), fmt(tp, tick),
                                                self.pos['tp_cid']):
                await self.close('TP')
            self.phase = 'POSITION'
            return {'ok': True, 'side': side, 'qty': qty_str, 'entry': fill, 'tp': tp, 'sl': sl, 'latency_ms': ms,
                    'entry_type': route.upper()}
        except Exception as exc:
            self.event(f'VESKA error {type(exc).__name__}: {exc}')
            self.cooldown_until = self.clock() + 10
            await self.cancel_orphan_entry(xm)
            return {'ok': False, 'error': f'{type(exc).__name__}: {exc}'[:200]}
        finally:
            self.busy = False
            self.pending_risk = 0.0
            if not self.pos:
                self.phase = 'SCANNING'

    async def cross_check(self, decision: Decision, side: str, pl: dict, base: Profile) -> tuple:
        """10-08w Super-Alpha crossing gate, evaluated on FRESH features at the fallback time.
        -> (None, info) when every condition holds, else (reason, info)."""
        p, sym, d = self.p, self.p.symbol, 1 if side == 'BUY' else -1
        await self.refresh_book(0.25)
        b, f = self.book(), self.feed.features(self.clock())
        touch = b.get('ask') if side == 'BUY' else b.get('bid')
        info = {'touch': touch, 'ofi': f.get('ofi_10s'), 'edge_bps': (self.diag or {}).get('edge_bps')}
        if sym not in (p.super_cross_symbols or ()) or self.observer:
            return f'{sym} not a crossing symbol', info
        vote = max(decision.score, (self.diag or {}).get('consensus') or 0.0)
        if vote < p.super_cross_score - 1e-9:
            return f'vote {vote:.3f} < {p.super_cross_score}', info
        ofi = (f.get('ofi_10s') or 0.0) * d
        if ofi < p.super_cross_ofi - 1e-9:
            return f'OFI {ofi:+.2f} < {p.super_cross_ofi} with the trade', info
        if (info['edge_bps'] or 0.0) < p.super_cross_edge_bps:
            return f'edge {info["edge_bps"]} < {p.super_cross_edge_bps} bps', info
        if self.toxic_blocks(side):
            return 'VPIN toxic against the side', info
        if not touch or b.get('stale'):
            return 'no fresh venue touch', info
        lim = round_tick(touch * (1 + d * p.super_cross_slip_bps / 1e4), self.spec['tick'], 'down' if d > 0 else 'up')
        pi = plan(side, lim, self.spec, replace(base, entry='market'), self.account.get('available'), qty=pl['qty'])
        rr = self.plan_rr(pi, 'ioc') if pi['ok'] else None
        info.update(limit=lim, rr_ioc=None if rr is None else round(rr, 3))
        if rr is None or rr < p.super_cross_rr_min:
            return f'taker RR {"—" if rr is None else f"{rr:.2f}"} < {p.super_cross_rr_min}', info
        return None, info

    async def cross_remaining(self, side: str, pl: dict, filled: float, fill: float, xm: dict, tag: str) -> tuple:
        """Take ONLY the unfilled remainder with one LIMIT IOC at touch +/- slip cap (never a MARKET, never more than
        the planned quantity). -> (cumulative filled, volume-weighted average price)."""
        p, sym, step, tick = self.p, self.p.symbol, self.spec['step'], self.spec['tick']
        d = 1 if side == 'BUY' else -1
        rem = floor_step(pl['qty'] - filled, step)
        await self.refresh_book(0.0)
        b = self.book()
        touch = b.get('ask') if side == 'BUY' else b.get('bid')
        if rem < self.spec['min_qty'] - 1e-12 or not touch or rem * touch < self.spec.get('min_notional', 0.0):
            xm['cross_check'] = f'remainder {fmt(rem, step)} below the venue minimum'
            return filled, fill
        lim = round_tick(touch * (1 + d * p.super_cross_slip_bps / 1e4), tick, 'down' if d > 0 else 'up')
        cid = f'gx{tag[0].lower()}-' + uuid.uuid4().hex[:26]
        xm['cid_live'] = cid
        avg2, got = await self.venue.ioc(sym, side, fmt(rem, step), fmt(lim, tick), cid)
        xm['crossed'] = round(got, 12)
        self.event(f'SUPER-ALPHA CROSS {side} {fmt(rem, step)} IOC limit {fmt(lim, tick)} (touch {touch:g} '
                   f'+/- {p.super_cross_slip_bps:g} bps) -> filled {fmt(got, step)}'
                   + (f' @ {avg2:g}' if got else ''))
        if got <= 0:
            return filled, fill
        tot = filled + got
        xm['entry_fee_bps'] = (filled * p.maker_bps + got * (p.taker_bps + p.slip_bps)) / tot
        xm['entry_type'] = 'GTX+IOC' if filled > 0 else 'IOC'
        xm['t_fill'] = time.perf_counter()
        return tot, ((fill * filled if filled else 0.0) + avg2 * got) / tot

    async def dyn_keep(self, side: str, decision: Decision, px0: float, mid0: Optional[float]) -> tuple:
        """10-08w dynamic TTL: keep resting while the score >= dyn_ttl_score, OFI L10 >= dyn_ttl_ofi with the trade,
        the touch has not left > dyn_ttl_away_pct from the first quote and the mid has not moved >= adverse bps
        against the trade.  The swarm is not re-run inside the order loop (the engine lock is held), so the vote
        is the decision's.  -> (keep, reason)."""
        p, d = self.p, 1 if side == 'BUY' else -1
        f = self.feed.features(self.clock())
        if decision.score < p.dyn_ttl_score - 1e-9:
            return False, f'score {decision.score:.2f} < {p.dyn_ttl_score}'
        ofi = (f.get('ofi_10s') or 0.0) * d
        if ofi < p.dyn_ttl_ofi - 1e-9:
            return False, f'OFI {ofi:+.2f} < {p.dyn_ttl_ofi}'
        mid = self.feed.price()
        if mid and mid0:
            mv = d * (mid / mid0 - 1) * 1e4
            if mv <= -p.dyn_ttl_adverse_bps:
                return False, f'ADVERSE {mv:+.1f} bps'
        await self.refresh_book(0.5)
        b = self.book()
        touch = b.get('bid') if side == 'BUY' else b.get('ask')
        if touch and px0 and d * (touch - px0) > 0 and abs(touch / px0 - 1) * 100 > p.dyn_ttl_away_pct:
            return False, f'touch left {abs(touch / px0 - 1) * 100:.3f}% > {p.dyn_ttl_away_pct}%'
        return True, f'OFI {ofi:+.2f} · score {decision.score:.2f}'

    async def cancel_orphan_entry(self, xm: dict) -> None:
        """10-08v: an exception between posting the GTX entry and settling it (uncertain POST, REST error while
        polling) used to leave the maker order resting on the venue with no owner: a late fill then appeared as
        an EXTERNAL position.  Cancel it (benign if unknown/filled) and read the cumulative fill; a filled
        quantity is left to sync() -> ADOPT, which places the protective stop.  Never re-posts anything."""
        cid = xm.get('cid_live')
        if not cid or self.pos:
            return
        sym = self.p.symbol
        try:
            await self.venue.cancel(sym, cid)
            st = await self.venue.order(sym, cid)
        except Exception as exc:
            xm['orphan'] = 'UNKNOWN'
            self.orphans[cid] = {'t': self.clock(), 'tries': 0}
            self.event(f'VESKA orphan entry {cid[:10]}… cancel/query failed ({type(exc).__name__}) -> '
                       f'reconcile on next sync')
            return
        filled = float(st.get('filled') or 0)
        xm['orphan'] = filled
        if filled > 0:
            self.event(f'VESKA errored entry {cid[:10]}… had filled {filled:g} before cancel -> sync ADOPT '
                       f'will protect it')

    # ------------------------------------------------------------------ MARIN
    def be_price(self, p: dict) -> float:
        d = 1 if p['side'] == 'BUY' else -1
        off = self.cost.stop_out_pct() + self.p.be_offset_pct      # maker in + taker stop + slip + 0.02 %
        return round_tick(p['entry'] * (1 + d * off / 100.0), self.spec['tick'], 'up' if d > 0 else 'down')

    async def move_stop(self, price: float, kind: str) -> bool:
        """Place the new stop FIRST, then cancel the old one: never a moment unprotected."""
        p, sym, tick = self.pos, self.p.symbol, self.spec['tick']
        opp = 'SELL' if p['side'] == 'BUY' else 'BUY'
        cid = 'gs-' + uuid.uuid4().hex[:28]
        try:
            await self.venue.stop(sym, opp, fmt(price, tick), cid)
        except Exception as exc:                            # e.g. -2021 would trigger immediately
            if getattr(exc, 'code', None) == -4130:          # Binance: ONE closePosition stop per direction
                return await self.swap_stop(price, kind, cid)
            self.note_stop(kind, 'place', price, exc)
            b = self.book()                                  # live touch, not the last REST view (stale)
            mark = (b.get('price') if not b.get('stale') else None) or self.view.get('mark') or self.feed.price()
            d = 1 if p['side'] == 'BUY' else -1
            if mark and d * (mark - price) <= 0:
                self.event(f'MARIN {kind} {price:g} already crossed ({type(exc).__name__}) -> close reduce-only')
                await self.close(kind)
                return True
            self.event(f'MARIN move {kind} failed: {err_txt(exc)}')
            return False
        old = p['sl_cid']
        p.update(sl=price, sl_cid=cid, stop_kind=kind, xstop_kind=kind)
        try:
            await self.venue.cancel_stop(old)
        except Exception:
            pass
        return True

    async def place_entry_stop(self, sym: str, side: str, price: str, cid: str) -> None:
        """-4130 at entry = a stale closePosition stop of an old trade still sits on the venue: clear, retry once."""
        try:
            await self.venue.stop(sym, side, price, cid)
        except Exception as exc:
            if getattr(exc, 'code', None) != -4130:
                raise
            for o in await self.venue.orders_view(sym):
                if 'STOP' in str(o.get('type')) and o.get('side') == side and o.get('id') != cid:
                    try:
                        await self.venue.cancel_stop(o['id'])
                    except Exception:
                        pass
            self.event(f'MARIN stale stop cleared on {sym} (-4130) -> stop re-placed')
            await self.venue.stop(sym, side, price, cid)

    async def swap_stop(self, price: float, kind: str, cid: str) -> bool:
        """-4130 path: cancel the old closePosition stop, place the new one; on failure restore the old level,
        and if even that fails close the position (never leave it naked)."""
        p, sym, tick = self.pos, self.p.symbol, self.spec['tick']
        opp = 'SELL' if p['side'] == 'BUY' else 'BUY'
        t_off = time.perf_counter()
        try:
            await self.venue.cancel_stop(p['sl_cid'])
        except Exception as exc:
            self.note_stop(kind, 'cancel', p['sl'], exc)          # may still have succeeded (timeout)
        try:
            await self.venue.stop(sym, opp, fmt(price, tick), cid)
        except Exception as exc:
            self.note_stop(kind, 'swap', price, exc)
            back = 'gs-' + uuid.uuid4().hex[:28]
            try:
                await self.venue.stop(sym, opp, fmt(p['sl'], tick), back)
                p['sl_cid'] = back
                naked = round((time.perf_counter() - t_off) * 1000, 1)
                p['stop_events'][-1]['naked_ms'] = naked
                self.event(f'MARIN move {kind} {price:g} failed after cancel ({err_txt(exc)}) -> old stop '
                           f'{p["sl"]:g} restored, unprotected {naked:.0f} ms')
            except Exception as exc2:
                self.note_stop(kind, 'restore', p['sl'], exc2)
                self.event(f'MARIN no stop could be placed ({err_txt(exc2)}) -> close {kind} reduce-only')
                await self.close(kind)
                return True
            return False
        p.update(sl=price, sl_cid=cid, stop_kind=kind, xstop_kind=kind)
        return True

    def note_stop(self, kind: str, step: str, price: float, exc: BaseException) -> None:
        """Keep the last 10 stop-API failures of this trade with the context needed to diagnose them."""
        p = self.pos
        if not p:
            return
        b = self.book()
        ev = p.setdefault('stop_events', [])
        ev.append({'t': round(self.clock(), 3), 'kind': kind, 'step': step, 'trigger': price,
                   'code': getattr(exc, 'code', None), 'meaning': BINANCE_CODES.get(getattr(exc, 'code', None)),
                   'uncertain': getattr(exc, 'uncertain', None), 'working_type': 'CONTRACT_PRICE',
                   'venue_bid': b.get('bid'), 'venue_ask': b.get('ask'), 'venue_mark': self.view.get('mark'),
                   'signal_mid': self.feed.price(), 'sl_before': p.get('sl')})
        del ev[:-10]

    def stop_covers_entry(self, p: dict) -> bool:
        """True only when the EXCHANGE stop sits at/through the entry (a soft target is not protection)."""
        d = 1 if p['side'] == 'BUY' else -1
        return p.get('sl') is not None and d * (p['sl'] - p['entry']) >= 0

    def mbe_price(self, p: dict) -> float:
        d = 1 if p['side'] == 'BUY' else -1
        return round_tick(p['entry'] * (1 + d * self.p.microbe_lock_pct / 100.0), self.spec['tick'],
                          'up' if d > 0 else 'down')

    def soft_be_price(self, p: dict) -> float:
        """Maker-exit breakeven: entry + maker in + maker out + offset (+0.06 % on VIP0)."""
        d = 1 if p['side'] == 'BUY' else -1
        off = (p.get('entry_fee_bps', self.p.maker_bps) + self.p.maker_bps) / 100.0 + self.p.be_offset_pct
        return round_tick(p['entry'] * (1 + d * off / 100.0), self.spec['tick'], 'up' if d > 0 else 'down')

    def shield_price(self, p: dict) -> float:
        """Fee shield: the better of entry + fee_shield_pct and the fee-true maker breakeven."""
        d = 1 if p['side'] == 'BUY' else -1
        fixed = round_tick(p['entry'] * (1 + d * self.p.fee_shield_pct / 100.0), self.spec['tick'],
                           'up' if d > 0 else 'down')
        be = self.soft_be_price(p)
        return max(fixed, be) if d > 0 else min(fixed, be)

    def plock_price(self, p: dict) -> float:
        d = 1 if p['side'] == 'BUY' else -1
        return round_tick(p['entry'] * (1 + d * self.p.lock_pct / 100.0), self.spec['tick'], 'up' if d > 0 else 'down')

    def adverse_flow(self, p: dict) -> float:
        """USD of aggressor flow AGAINST the position, since entry, inside the last flow_exit_s."""
        now, cfg = self.clock(), self.feed.cfg
        start = max(p['opened'], now - cfg.flow_exit_s)
        signed = sum(s for t, s in self.feed.flow.trades if t >= start)
        return -signed if p['side'] == 'BUY' else signed

    def exit_price(self, side_out: str, f: dict) -> Optional[float]:
        """Post-only reduce price: SELL at the best ask (BUY at the best bid); 1 tick inside if spread >= 2 ticks."""
        return self.gtx_price(side_out, f)

    async def start_soft_exit(self, reason: str, floor: Optional[float] = None) -> None:
        """floor (time-decay ejection): the reduce order never rests at a worse price than this level."""
        p = self.pos
        if p.get('exiting'):
            return
        b = self.book()
        ref = p.get('soft_stop') if reason.startswith('SOFT-') else None
        ref = ref or (b.get('price') if not b.get('stale') else None) or self.view.get('mark') or p['entry']
        p['exiting'] = {'reason': reason, 'cid': None, 'px': None, 'posted': 0.0, 'started': self.clock(),
                        'reprices': 0, 'floor': floor, 'floor0': floor, 'ref': ref}
        try:
            await self.venue.cancel(self.p.symbol, p['tp_cid'])     # the exit order owns the whole size now
        except Exception:
            pass
        self.event(f'MARIN SOFT EXIT {reason}: post-only reduce at the touch, re-peg every '
                   f'{self.p.exit_repeg_s:g}s - no market order (hard SL {p["sl0"]:g} stays on the exchange)')
        await self.work_soft_exit()

    async def work_soft_exit(self) -> None:
        """Maker exit queue: keep one reduce-only GTX at the touch; re-peg when the touch moves away."""
        p, sym, step, tick = self.pos, self.p.symbol, self.spec['step'], self.spec['tick']
        x = p['exiting']
        opp = 'SELL' if p['side'] == 'BUY' else 'BUY'
        await self.refresh_book(0.3)
        want = self.exit_price(opp, self.book())
        if want is None:
            return
        if x.get('floor'):                     # EJECT-BE: SELL never below the scratch level, BUY never above
            want = max(want, x['floor']) if opp == 'SELL' else min(want, x['floor'])
        now = time.monotonic()
        if x['cid']:
            st = await self.venue.order(sym, x['cid'])
            if st.get('status') == 'FILLED':
                return                                            # flat -> sync() settles
            moved = x['px'] is not None and abs(want - x['px']) >= tick - 1e-12
            if not moved or now - x['posted'] < self.p.exit_repeg_s:
                return
            await self.venue.cancel(sym, x['cid'])
            x['reprices'] += 1
        v = await self.venue.position_view(sym)
        amt = abs(v.get('amt') or 0.0)
        if amt <= 0:
            return
        cid = 'gx-' + uuid.uuid4().hex[:28]
        ok = await self.venue.take_profit(sym, opp, fmt(floor_step(amt, step), step), fmt(want, tick), cid)
        x.update(cid=cid if ok else None, px=want if ok else None, posted=now)

    async def flow_exit(self) -> None:
        await self.start_soft_exit('FLOW')

    def flow_limit(self, f: dict) -> float:
        """Adverse-flow exit threshold for a trade: max($2M, 2.5 x entry trigger flow), <= 3 x $2M (x usd_scale)."""
        base = self.feed.cfg.flow_exit_usd * self.feed.cfg.usd_scale
        trig = abs(f.get('cvd_exit_win') or 0.0) * self.p.flow_exit_trigger_mult
        return min(max(base, trig), base * self.p.flow_exit_cap_mult)

    def regime_flipped(self, p: dict, f: dict) -> bool:
        """SHORT while CVD 60 s >= +$1M (x scale), LONG while <= -$1M: the thesis is dead."""
        if not self.p.regime_flip_exit:
            return False
        cvd, lim = f.get('cvd_60s') or 0.0, self.p.regime_flip_usd * self.feed.cfg.usd_scale
        return cvd >= lim if p['side'] == 'SELL' else cvd <= -lim

    async def manage(self) -> None:
        """MARIN: adverse-flow exit -> breakeven tiers -> TP1 half -> trailing runner.
        soft_exits=True: BE / trail / flow exits are post-only maker orders at the touch; only the hard SL on the
        exchange (STOP_MARKET at the initial stop) can pay taker."""
        p, v = self.pos, self.view
        if not p or not v.get('amt'):
            return
        f = self.features or {}
        b = self.book()
        mark = (b.get('price') if not b.get('stale') else None) or v.get('mark') or self.feed.price()
        if not mark:
            return
        d = 1 if p['side'] == 'BUY' else -1
        p['peak'] = max(p['peak'], mark) if d > 0 else min(p['peak'], mark)
        fav = d * (mark / p['entry'] - 1) * 100
        peak_fav = d * (p['peak'] / p['entry'] - 1) * 100
        p['mfe_pct'] = max(p['mfe_pct'], peak_fav)
        age = self.clock() - p['opened']
        mk = p.setdefault('markout_bps', {})
        for h in MARKOUT_S:
            k = f'{h:g}s'
            if k not in mk and age >= h:
                mk[k] = [round(fav * 100, 3), round(age, 2)]    # [bps with the trade, actual age at sample]
        if p.get('exiting'):
            x = p['exiting']
            if self.soft_exit_due(p, x, mark):
                await self.escalate_exit(p, x, mark)
                return
            if x.get('floor') and self.clock() - p['opened'] >= self.p.eject_hard_s:
                x.update(floor=None, reason='EJECT')
                self.event(f'MARIN EJECT escalate: BE order unfilled after {self.p.eject_hard_s:.0f}s -> '
                           f'maker at the touch ({fav:+.3f}%, hard SL {p["sl"]:g} stays)')
            await self.work_soft_exit()
            return
        now = self.clock()
        if self.eject_fast_due(p, now, f):
            self.event(f'MARIN FAST EJECT: {now - p["opened"]:.0f}s >= {self.p.eject_fast_s:.0f}s, MFE '
                       f'{p["mfe_pct"]:+.3f}% < +{self.p.eject_fast_mfe_pct}%, 15s velocity '
                       f'{(f.get("px_vel_15s") or 0):+.3f}% weak -> post-only reduce at the touch')
            await self.start_soft_exit('EJECT-FAST')
            return
        if self.eject_due(p, now):
            px = self.eject_floor(p)
            self.event(f'MARIN TIME-DECAY EJECT: {now - p["opened"]:.0f}s >= {self.p.eject_s:.0f}s, MFE '
                       f'{p["mfe_pct"]:+.3f}% < +{self.p.eject_mfe_pct}% -> post-only reduce at BE {px:g} '
                       f'(entry {"+" if d > 0 else "-"}fees), at the touch after {self.p.eject_hard_s:.0f}s')
            await self.start_soft_exit('EJECT-BE', floor=px)
            return
        flip = self.regime_flipped(p, f)
        p['flip_since'] = (p.get('flip_since') or now) if flip else None
        p['gross_pct'] = fav
        gate = self.hurdle_gate(p, fav, now)
        p['hurdle'] = gate
        regime_due = (flip and now - p['opened'] >= self.p.regime_flip_min_hold_s
                      and now - p['flip_since'] >= self.p.regime_flip_debounce_s)
        adverse = self.adverse_flow(p)
        p['adverse_usd'] = adverse
        limit = p.get('flow_limit') or self.flow_limit({})
        p['breathing'] = now - p['opened'] < self.p.flow_breath_s
        flow_due = self.p.flow_exit and adverse >= limit and not p['breathing']
        against = self.flow.get('buy_ok' if p['side'] == 'SELL' else 'sell_ok')
        if gate and against and self.p.hurdle_concordance_override:
            gate = None                                         # all three flows oppose us: thesis dead, exit now
        if (regime_due or flow_due) and gate:
            self.note_hurdle_block(p, 'REGIME' if regime_due else 'FLOW', fav, mark, gate)
            regime_due = flow_due = False
        elif not (regime_due or flow_due):
            p['hurdle_active'] = False
        if regime_due:
            self.event(f'MARIN REGIME-FLIP EXIT: {"SHORT" if d < 0 else "LONG"} vs CVD 60s '
                       f'{(f.get("cvd_60s") or 0) / 1e6:+.2f}M (limit ±${self.p.regime_flip_usd * self.feed.cfg.usd_scale / 1e6:.2f}M)'
                       f' held {now - p["flip_since"]:.0f}s at {fav:+.3f}% -> soft maker exit')
            if self.p.soft_exits:
                await self.start_soft_exit('REGIME')
            else:
                p['exit_reason'] = 'REGIME'
                await self.close('REGIME')
            return
        if flow_due:
            self.event(f'MARIN FLOW EXIT: ${adverse / 1e6:.2f}M aggressor flow against {p["side"]} in '
                       f'{self.feed.cfg.flow_exit_s:g}s (limit ${limit / 1e6:.2f}M) at {fav:+.3f}%')
            if self.p.soft_exits:
                await self.start_soft_exit('FLOW')
            else:
                p['exit_reason'] = 'FLOW'
                await self.close('FLOW')
            return
        if p['split'] and not p['tp1_filled'] and abs(v['amt']) <= p['qty'] - p['tp_qty'] + 1e-12:
            p['tp1_filled'] = True
            self.event(f'MARIN TP1 filled {fmt(p["tp_qty"], self.spec["step"])} @ {p["tp"]:g} (maker) · runner trails')
        if self.p.soft_exits:
            await self.ratchet_stop(p, d, mark, fav)
            if not self.pos:
                return
            await self._manage_soft(p, d, mark, fav)
        else:
            await self._manage_hard(p, d, mark, fav)

    def soft_exit_due(self, p: dict, x: dict, mark: float) -> bool:
        """Bounded chase (2026-10-07r, DOGE #37): a protective maker exit escalates to a reduce-only taker exit
        once price is soft_exit_max_slip_pct beyond its reference, or after soft_exit_deadline_s while price is
        still at/beyond it. EJECT keeps its own floor -> touch schedule."""
        if x['reason'].startswith('EJECT') or not x.get('ref') or not mark:
            return False
        d = 1 if p['side'] == 'BUY' else -1
        adverse = d * (x['ref'] - mark) / x['ref'] * 100          # > 0: price went past the exit reference
        x['adverse_pct'] = round(adverse, 4)
        due = adverse >= self.p.soft_exit_max_slip_pct or (
            adverse >= 0 and self.clock() - x['started'] >= self.p.soft_exit_deadline_s)
        if due and not self.p.soft_exit_escalate:
            if 'shadow_escalate' not in x:                      # counterfactual for later evaluation
                b = self.book()
                x['shadow_escalate'] = {'after_s': round(self.clock() - x['started'], 2), 'mark': mark,
                                        'touch': b.get('bid') if d > 0 else b.get('ask'),
                                        'adverse_pct': x['adverse_pct']}
            return False
        return due

    async def escalate_exit(self, p: dict, x: dict, mark: float) -> None:
        reason = f'{x["reason"]}>TAKER'
        self.event(f'MARIN EXIT ESCALATED {x["reason"]}: maker unfilled {self.clock() - x["started"]:.1f}s, price '
                   f'{x.get("adverse_pct", 0):+.3f}% past ref {x["ref"]:g} -> reduce-only MARKET (taker fee, loss capped)')
        if x.get('cid'):
            try:
                await self.venue.cancel(self.p.symbol, x['cid'])
            except Exception as exc:
                self.event(f'MARIN exit cancel: {err_txt(exc)}')
        p['exit_escalated'] = True
        await self.close(reason)

    def eject_due(self, p: dict, now: float) -> bool:
        """Time decay: a soft-exit position open >= eject_s whose best excursion never reached +eject_mfe_pct."""
        own = p.get('eject_s')                                        # per-position schedule (SCALP 90 s)
        return ((self.p.eject or own is not None) and self.p.soft_exits and not p.get('tp1_filled')
                and now - p['opened'] >= (own or self.p.eject_s)
                and p['mfe_pct'] < p.get('eject_mfe', self.p.eject_mfe_pct))

    def eject_fast_due(self, p: dict, now: float, f: dict) -> bool:
        """Low-velocity time decay: >= 120 s, MFE never reached +0.08 %, |px_vel_15s| < 0.025 % (unknown -> no)."""
        vel = f.get('px_vel_15s')
        return (self.p.eject_fast and self.p.soft_exits and p.get('eject_s') is None and not p.get('tp1_filled')
                and now - p['opened'] >= self.p.eject_fast_s and p['mfe_pct'] < self.p.eject_fast_mfe_pct
                and vel is not None and abs(vel) < self.p.eject_fast_vel_pct)

    def eject_floor(self, p: dict) -> float:
        """Fee-true scratch: entry +/- (entry fee + maker exit fee) -> net PnL ~ 0 when the reduce order fills."""
        d = 1 if p['side'] == 'BUY' else -1
        off = (p.get('entry_fee_bps', self.p.maker_bps) + self.p.maker_bps) / 1e4
        return round_tick(p['entry'] * (1 + d * off), self.spec['tick'], 'up' if d > 0 else 'down')

    def hurdle_gate(self, p: dict, fav: float, now: float) -> Optional[str]:
        """Fee hurdle: why a FLOW / REGIME exit must wait (None = exits allowed). Hard SL and MARIN tiers ignore it."""
        if not self.p.fee_hurdle:
            return None
        age, h = now - p['opened'], self.p.hurdle_pct
        if fav >= 0 and age < self.p.hurdle_min_hold_s:
            return f'HOLD {age:.0f}/{self.p.hurdle_min_hold_s:.0f}s (gross {fav:+.3f}% >= 0)'
        if -h < fav < h:
            return f'FEE HURDLE {fav:+.3f}% inside ±{h}%'
        return None

    def note_hurdle_block(self, p: dict, why: str, fav: float, mark: float, gate: str) -> None:
        """Count each suppressed exit once (until its trigger clears) and keep the first price for the post-mortem."""
        if p.get('hurdle_active'):
            return
        p['hurdle_active'] = True
        p['hurdle_blocks'] = p.get('hurdle_blocks', 0) + 1
        p.setdefault('hurdle_first_px', mark)
        p.setdefault('hurdle_first_pct', fav)
        self.event(f'MARIN {why} exit HELD by {gate} - waiting for >= +{self.p.hurdle_pct}% / <= -{self.p.hurdle_pct}% '
                   f'(hard SL {p["sl0"]:g} stays live)')

    def fee_true_pct(self, p: dict) -> float:
        """Entry fee of THIS position + taker stop-out + slip, in % (maker entry 0.08 %, IOC entry 6 bps -> 0.12 %)."""
        e_in = p.get('entry_fee_bps')
        e_in = self.p.maker_bps if e_in is None else e_in
        return (e_in + self.p.taker_bps + self.p.slip_bps) / 100.0

    async def ratchet_stop(self, p: dict, d: int, mark: float, fav: float) -> None:
        """MFE >= +0.15 %: move the EXCHANGE stop to entry +/- max(0.08 %, own fees) once - survives a restart or a
        dead feed.  10-08w tier 2: MFE >= +0.25 % -> entry +/- (own fees + 0.05 %)."""
        if not self.p.ratchet or self.clock() < p.get('ratchet_retry_at', 0.0):
            return
        if not p.get('ratchet'):
            if p['mfe_pct'] < self.p.ratchet_trigger_pct:
                return
            lock, kind, key = max(self.p.ratchet_lock_pct, self.fee_true_pct(p)), 'RATCHET', 'ratchet'
        elif (self.p.ratchet2 and p.get('ratchet') is True and not p.get('ratchet2')
              and p['mfe_pct'] >= self.p.ratchet2_trigger_pct):
            lock, kind, key = self.fee_true_pct(p) + self.p.ratchet2_buffer_pct, 'RATCHET2', 'ratchet2'
        else:
            return
        tgt = round_tick(p['entry'] * (1 + d * lock / 100.0), self.spec['tick'], 'up' if d > 0 else 'down')
        if d * (tgt - p['sl']) <= 0 or d * (mark - tgt) <= 0:
            return
        if not await self.move_stop(tgt, kind):
            fails = p[f'{key}_fails'] = p.get(f'{key}_fails', 0) + 1
            p['ratchet_retry_at'] = self.clock() + self.p.ratchet_retry_s * 2 ** (fails - 1)
            if fails >= self.p.ratchet_max_fail:
                p[key] = 'FAILED'
                self.event(f'MARIN {kind} gave up after {fails} failures - exchange stop stays '
                           f'{p["sl"]:g}; soft tiers still manage the exit')
            return
        if self.pos:
            p[key] = True
            self.event(f'MARIN {kind} at MFE {p["mfe_pct"]:+.3f}% -> exchange stop {tgt:g} '
                       f'(entry {"+" if d > 0 else "-"}{lock:.3f}%)')

    async def _manage_soft(self, p: dict, d: int, mark: float, fav: float) -> None:
        """4 tiers (soft maker exits): T1 +0.08 % -> entry +0.03 % · T2 +0.12 % -> entry + fees + 0.02 %
        · T3 TP1 50 % at +0.35 % (resting maker order) · T4 runner trails 0.20 % from +0.50 %, 0.15 % from +0.70 %."""
        level, kind = p.get('soft_stop'), p.get('soft_kind')
        cands = []
        if p['mfe_pct'] >= self.p.microbe_trigger_pct:
            cands.append((self.mbe_price(p), 'LOCK'))
        if p['mfe_pct'] >= self.p.be_trigger_pct:
            cands.append((self.shield_price(p), 'BE'))
        if p['mfe_pct'] >= self.p.lock_trigger_pct:
            cands.append((self.plock_price(p), 'PROFIT'))
        gaps = []
        if p['mfe_pct'] >= self.p.trail_activate_pct:
            gaps.append(self.p.trail_tight_gap_pct if p['mfe_pct'] >= self.p.trail_tight_pct else self.p.trail_gap_pct)
        if p.get('atr_trail_pct') and p.get('tp1_filled'):         # TP2: runner trails 1.2 x ATR after TP1
            gaps.append(p['atr_trail_pct'])
        if gaps:
            gap = min(gaps)
            cands.append((round_tick(p['peak'] * (1 - d * gap / 100.0), self.spec['tick'], 'down' if d > 0 else 'up'),
                          'TRAIL'))
            p['trail_gap'] = round(gap, 4)
        for px, k in cands:
            if level is None or d * (px - level) > 0:
                if k != kind:
                    self.event({'LOCK': f'MARIN T1 MICRO-LOCK at {fav:+.3f}% -> exit level {px:g} (entry +'
                                        f'{self.p.microbe_lock_pct}%, loss <= fees)',
                                'BE': f'MARIN T2 FEE-SHIELD at {fav:+.3f}% -> exit level {px:g} (>= entry +'
                                      f'{self.p.fee_shield_pct}% and entry + fees + {self.p.be_offset_pct}%)',
                                'PROFIT': f'MARIN T2b PROFIT LOCK at {fav:+.3f}% -> exit level {px:g} (entry +'
                                          f'{self.p.lock_pct}%)',
                                'TRAIL': f'MARIN T4 TRAIL {p.get("trail_gap")}% behind peak {p["peak"]:g} -> {px:g}'}[k])
                level, kind = px, k
        if kind in ('BE', 'PROFIT', 'TRAIL'):
            p['be_locked'] = True
        if kind in ('PROFIT', 'TRAIL'):
            p['plock'] = True
        if kind == 'LOCK':
            p['mbe_locked'] = True
        p['trail_on'] = kind == 'TRAIL'
        p['soft_stop'], p['soft_kind'] = level, kind
        if level is not None:
            p['stop_kind'] = kind
            if d * (mark - level) <= 0:
                await self.start_soft_exit(f'SOFT-{kind}')

    async def _manage_hard(self, p: dict, d: int, mark: float, fav: float) -> None:
        target, kind = None, None
        if p['stop_kind'] == 'SL' and p['mfe_pct'] >= self.p.microbe_trigger_pct:
            target, kind = self.mbe_price(p), 'MBE'
        if not p['be_locked'] and p['mfe_pct'] >= self.p.be_trigger_pct:
            target, kind = self.be_price(p), 'BE'
        if not p.get('plock') and p['mfe_pct'] >= self.p.lock_trigger_pct:
            lk = max(self.plock_price(p), self.be_price(p)) if d > 0 else min(self.plock_price(p), self.be_price(p))
            if d * (lk - (target if target is not None else p['sl'])) > 0:
                target, kind = lk, 'PROFIT'
        if p['mfe_pct'] >= self.p.trail_activate_pct:
            trail = round_tick(p['peak'] * (1 - d * self.p.trail_gap_pct / 100.0), self.spec['tick'],
                               'down' if d > 0 else 'up')
            floor = self.be_price(p)
            trail = max(trail, floor) if d > 0 else min(trail, floor)
            best = target if target is not None else p['sl']
            if d * (trail - best) >= p['entry'] * self.p.trail_min_step_pct / 100.0:
                target, kind = trail, 'TRAIL'
        if target is not None and d * (target - p['sl']) > 0 and d * (mark - target) > 0:
            if await self.move_stop(target, kind) and self.pos:
                if kind == 'MBE':
                    p['mbe_locked'] = True
                    self.event(f'MARIN MICRO-BE at {fav:+.3f}% -> stop {target:g}')
                elif kind == 'BE' and not p['be_locked']:
                    p['be_locked'] = True
                    self.event(f'MARIN BREAKEVEN LOCK at +{fav:.3f}% -> stop {target:g} (net >= +{self.p.be_offset_pct}%)')
                elif kind == 'PROFIT':
                    p['be_locked'] = p['plock'] = True
                    self.event(f'MARIN PROFIT LOCK at +{fav:.3f}% -> stop {target:g} (entry +{self.p.lock_pct}%)')
                elif kind == 'TRAIL':
                    p['be_locked'] = p['trail_on'] = True
        elif (target is not None and d * (mark - target) <= 0 and kind in ('MBE', 'BE', 'PROFIT')
              and d * (target - p['sl']) > 0):
            self.event(f'MARIN {kind} {target:g} already crossed at mark {mark:g} -> close reduce-only')
            await self.close(kind)

    async def close(self, reason: str) -> None:
        sym = self.p.symbol
        if self.pos:
            await self.venue.cancel(sym, self.pos['tp_cid'])
        v = await self.venue.position_view(sym)
        if v['amt']:
            await self.venue.close(sym, v['amt'], self.spec['step'])
        if self.pos:
            self.pos['exit_reason'] = reason
            try:
                await self.venue.cancel_stop(self.pos['sl_cid'])
            except Exception as exc:                       # already triggered / gone
                self.event(f'MARIN stop cancel: {type(exc).__name__}')
        await self.sync(force_settle=True)

    async def adopt(self) -> None:
        """Re-attach MARIN to a position found on the venue (restart / kill of the previous process)."""
        v, sym, tick, step = self.view, self.p.symbol, self.spec['tick'], self.spec['step']
        amt = v.get('amt') or 0.0
        side, qty, entry = ('BUY' if amt > 0 else 'SELL'), abs(amt), v.get('entry') or self.feed.price()
        if not entry:
            return
        d = 1 if side == 'BUY' else -1
        stop = next((o for o in self.orders if 'STOP' in str(o.get('type')) and o.get('reduce')
                     and o.get('side') != side), None)
        mark = v.get('mark') or self.feed.price() or entry
        if stop:
            sl, sl_cid, t0 = stop['price'], stop['id'], stop.get('time') or 0.0
        else:
            sl = round_tick(min(entry, mark) * (1 - self.p.sl_pct / 100.0) if d > 0
                            else max(entry, mark) * (1 + self.p.sl_pct / 100.0), tick, 'down' if d > 0 else 'up')
            sl_cid, t0 = 'gs-' + uuid.uuid4().hex[:28], 0.0
            try:
                await self.venue.stop(sym, 'SELL' if d > 0 else 'BUY', fmt(sl, tick), sl_cid)
            except Exception as exc:
                self.event(f'MARIN adopt: stop failed ({type(exc).__name__}) - soft exits only')
        now = self.clock()
        peak = max(entry, mark) if d > 0 else min(entry, mark)
        start_ms = None
        if hasattr(self.venue, 'entry_start_ms'):
            try:
                start_ms = await self.venue.entry_start_ms(sym, amt, step)
            except Exception as exc:
                self.event(f'MARIN adopt: entry fills lookup failed ({err_txt(exc)}) - entry fee UNKNOWN')
        opened_ms = start_ms - 1 if start_ms else int((t0 or now) * 1000) - 1000
        self.pos = {'symbol': sym, 'side': side, 'qty': qty, 'qty_str': fmt(qty, step), 'entry': entry,
                    'cost_complete': bool(start_ms),
                    'opened': now, 'opened_ms': opened_ms, 'sl': sl, 'tp': None, 'sl0': sl,
                    'sl_cid': sl_cid, 'tp_cid': 'gt-adopt-' + uuid.uuid4().hex[:20], 'stop_kind': 'SL',
                    'tp_qty': qty, 'split': False, 'tp1_filled': False, 'be_locked': False, 'mbe_locked': False,
                    'trail_on': False, 'peak': peak, 'mfe_pct': d * (peak / entry - 1) * 100, 'adverse_usd': 0.0,
                    'regime': (self.reg or {}).get('name'), 'trigger_usd': 0.0, 'flow_limit': self.flow_limit({}),
                    'edge_bps': None, 'ker': None, 'atr_pct': None, 'atr_trail_pct': None, 'bracket_src': 'ADOPT',
                    'rr_gross': None, 'rr_net': None, 'be_wr': None, 'vpin': None, 'ofi': None, 'kelly_f': None,
                    'quote_px': entry, 'size_mult': 1.0, 'px_vel_15s': None, 'macro': self.macro.get('name'),
                    'tag': 'ADOPTED', 'path': 'ADOPT', 'score': 0.0, 'reason': 'external position re-adopted',
                    'hold_s': self.p.max_hold_s, 'latency_ms': 0.0, 'tp_pct': None, 'sl_pct': abs(sl / entry - 1) * 100,
                    'entry_type': 'ADOPT', 'entry_fee_bps': self.p.maker_bps}
        self.phase = 'POSITION'
        self.event(f'MARIN ADOPT {"LONG" if d > 0 else "SHORT"} {fmt(qty, step)} {sym} @ {entry:g} · stop {sl:g}'
                   f'{" (sàn, giữ nguyên)" if stop else " (đặt mới)"} · MFE {self.pos["mfe_pct"]:+.3f}% -> MARIN quản lý lại')

    async def retry_orphans(self) -> None:
        """10-08w: cancel + read every entry whose cancel failed earlier (venue 5xx); a fill is ADOPTed below."""
        for cid, o in list(self.orphans.items()):
            try:
                await self.venue.cancel(self.p.symbol, cid)
                st = await self.venue.order(self.p.symbol, cid)
            except Exception as exc:
                o['tries'] += 1
                if o['tries'] >= 20:
                    self.orphans.pop(cid, None)
                    self.event(f'VESKA orphan {cid[:10]}… still unreachable after 20 syncs -> left to the venue '
                               f'order view ({type(exc).__name__})')
                continue
            self.orphans.pop(cid, None)
            self.event(f'VESKA orphan {cid[:10]}… reconciled: {st.get("status")} filled {st.get("filled") or 0:g}')

    async def sync(self, force_settle: bool = False) -> None:
        sym = self.p.symbol
        if self.orphans and not self.busy:
            await self.retry_orphans()
        self.view = await self.venue.position_view(sym)
        try:
            self.account = await self.venue.account_view()
        except Exception:
            pass
        self.orders = await self.venue.orders_view(sym)
        if (not self.pos and self.view.get('amt') and self.p.adopt_external and self.started and not self.busy
                and self.spec):
            await self.adopt()
            return
        p = self.pos
        if p and not self.view['amt']:
            await self._settle(p)
        elif p and not force_settle:
            if p['hold_s'] and self.clock() - p['opened'] >= p['hold_s']:
                self.event(f'MARIN time stop {p["hold_s"]:.0f}s -> close {p["side"]} {p["qty_str"]}')
                await self.close('TIME')
            else:
                await self.manage()

    async def _settle(self, p: dict) -> None:
        sym = self.p.symbol
        reason = p.get('exit_reason')
        x = p.get('exiting')
        if x and not reason:
            reason = ('TP1+' if p['tp1_filled'] else '') + x['reason']
            if x.get('cid'):
                try:
                    await self.venue.cancel(sym, x['cid'])
                except Exception:
                    pass
        if not reason:
            st = await self.venue.order(sym, p['tp_cid'])
            tp_hit = st.get('status') == 'FILLED' or p['tp1_filled']
            if tp_hit and not p['split']:
                reason = 'TP'
            else:
                reason = ('TP1+' if tp_hit else '') + p.get('xstop_kind', 'SL')
        try:
            await self.venue.cancel(sym, p['tp_cid'])
        except Exception:
            pass
        try:
            await self.venue.cancel_stop(p['sl_cid'])
        except Exception:
            pass
        if hasattr(self.venue, 'realized_parts'):
            parts = await self.venue.realized_parts(sym, p['opened_ms'])
        else:
            net = await self.venue.realized(sym, p['opened_ms'])
            parts = {'gross': net, 'fee': 0.0, 'net': net}
        pnl = parts['net']
        d = 1 if p['side'] == 'BUY' else -1
        notional = p['entry'] * p['qty']
        row = {'t': self.clock(), 'day': datetime.now().strftime('%Y-%m-%d'), 'mode': self.p.mode,
               'venue': self.venue.label, 'symbol': sym, 'side': p['side'], 'qty': p['qty'], 'entry': p['entry'],
               'exit': p['entry'] * (1 + d * (parts['gross'] / notional)) if notional else None,
               'gross': parts['gross'], 'fee': parts['fee'], 'pnl': pnl,
               'fee_bps': parts['fee'] / notional * 1e4 if notional else None,
               'maker_fills': parts.get('maker_fills'), 'taker_fills': parts.get('taker_fills'),
               'pnl_pct': pnl / (notional / self.p.leverage) * 100 if notional else 0.0, 'reason': reason,
               'be_locked': p['be_locked'], 'mbe_locked': p.get('mbe_locked'), 'tp1': p['tp1_filled'],
               'mfe_pct': p['mfe_pct'], 'adverse_usd': p.get('adverse_usd'), 'regime': p.get('regime'),
               'exit_mode': ('maker+taker' if p.get('exit_escalated') else 'maker') if x else 'exchange',
               'exit_reprices': (x or {}).get('reprices'), 'exit_ref': (x or {}).get('ref'),
               'shadow_escalate': (x or {}).get('shadow_escalate'),
               'stop_covered_entry': self.stop_covers_entry(p), 'stop_events': p.get('stop_events') or None,
               'cost_complete': p.get('cost_complete', True),
               'tag': p['tag'], 'path': p['path'], 'score': p['score'], 'edge_bps': p.get('edge_bps'),
               'ker': p.get('ker'), 'flow_limit': p.get('flow_limit'), 'macro': p.get('macro'),
               'bracket_src': p.get('bracket_src'), 'atr_pct': p.get('atr_pct'), 'rr_gross': p.get('rr_gross'),
               'rr_net': p.get('rr_net'), 'be_wr': p.get('be_wr'), 'vpin': p.get('vpin'), 'ofi': p.get('ofi'),
               'kelly_f': p.get('kelly_f'), 'size_mult': p.get('size_mult'), 'ratchet': bool(p.get('ratchet')),
               'alt_boost': bool(p.get('alt_boost')), 'eject_floor': (x or {}).get('floor0'),
               'probe': p.get('probe'), 'super_size': bool(p.get('super_size')), 'drift_probe': p.get('drift_probe'),
               'schema': LEDGER_SCHEMA, 'policy': policy_id(self.p), 'code': CODE_VERSION,
               'signal_mid': p.get('signal_mid'), 'venue_mid': p.get('venue_mid'), 'basis_bps': p.get('basis_bps'),
               'basis_exit_bps': basis_bps(self.feed.price(), self.book().get('price')),
               'ack_ms': p.get('ack_ms'), 'fill_wait_ms': p.get('fill_wait_ms'), 'markout_bps': p.get('markout_bps'),
               'px_vel_15s': p.get('px_vel_15s'), 'plock': p.get('plock', False),
               'hurdle_blocks': p.get('hurdle_blocks', 0), 'hurdle_first_px': p.get('hurdle_first_px'),
               'hurdle_first_pct': p.get('hurdle_first_pct'), 'hold_s': self.clock() - p['opened'],
               'latency_ms': p['latency_ms'], 'entry_type': p.get('entry_type', 'GTX')}
        self.pos = None
        self.phase = 'SCANNING'
        self.day_pnl += pnl
        self.history.appendleft(row)
        self.all_rows.append(row)
        self._write_ledger(row)
        self.cooldown_until = self.clock() + self.p.cooldown_s
        if pnl < 0 and p['tag'] == 'AUTO':
            self.consec_losses += 1
            if self.consec_losses >= self.p.max_consec_losses:
                self.pause_until = self.clock() + self.p.loss_pause_s
        elif pnl > 0:
            self.consec_losses = 0
        self.event(f'CLOSED {row["side"]} {row["qty"]:g} {sym} {reason} gross {parts["gross"]:+.4f} fee '
                   f'{-parts["fee"]:+.4f} net {pnl:+.4f} U ({row["pnl_pct"]:+.2f}% ROE) in {row["hold_s"]:.0f}s '
                   f'· day {self.day_pnl:+.4f} U')

    # ------------------------------------------------------------------ operator controls
    async def force_test_trade(self, side: str = 'BUY') -> dict:
        """Demo only: one post-only GTX entry through VESKA (swarm bypassed, labelled), full MARIN exits."""
        if self.p.mode != 'demo':
            return {'ok': False, 'error': 'FORCE TEST chỉ cho phép ở chế độ DEMO'}
        if side not in ('BUY', 'SELL'):
            return {'ok': False, 'error': 'side must be BUY or SELL'}
        if not self.started:
            return {'ok': False, 'error': 'venue chưa sẵn sàng'}
        if self.pos or self.busy or abs(self.view.get('amt') or 0):
            return {'ok': False, 'error': 'đang có vị thế / lệnh - đóng trước khi test'}
        reg = msig.regime(self.feed.features(self.clock()), self.sig_cfg)
        if side not in reg['allow']:
            self.event(f'FORCE TEST {side} refused: {reg["label"]} · {reg["why"]}')
            return {'ok': False, 'error': f'CẤM {side}: {reg["label"]} ({reg["why"]}) - thử chiều '
                                          f'{reg["allow"][0]}', 'regime': reg['name']}
        d = Decision(side, 1.0, True, False, 'OPERATOR FORCE TEST (swarm bypassed)', .768, 'NORMAL')
        self.event(f'FORCE TEST {side} requested from dashboard (Post-Only GTX + BE lock + TP1/trail)')
        async with self.lock:
            res = await self.execute(side, d, 'OPERATOR', tag='TEST', hold_s=self.p.test_hold_s)
        return res

    async def close_now(self) -> dict:
        if not (self.pos or abs(self.view.get('amt') or 0)):
            return {'ok': False, 'error': 'không có vị thế'}
        async with self.lock:
            await self.close('MANUAL')
        return {'ok': True}

    # ------------------------------------------------------------------ telemetry
    def agent_rows(self) -> list:
        votes = {v.agent: v for v in self.votes}
        rows = []
        for name in ('VESKA', 'NORO', 'LUMEN', 'TIDAL', 'ZEPHR', 'RUNE', 'OKAPI', 'MARIN'):
            v = votes.get(name)
            side, conf = (v.side, v.confidence) if v else ('HOLD', 0.0)
            veto = bool(v and v.veto)
            if not self.started:
                state, label = 'VETO' if name == 'VESKA' else 'SCANNING', \
                    ('VENUE OFFLINE' if self.last_error else 'CONNECTING VENUE') if name == 'VESKA' else 'WAITING VENUE'
            elif self.phase == 'EXECUTING':
                dside = self.decision.side if self.decision else side
                state, label = 'EXECUTING', 'EXECUTING ORDER' if name == 'VESKA' else f'AUTHORIZED {dside}'
            elif self.pos:
                state = 'POSITION'
                ps = self.pos
                tp_pct = ps.get('tp_pct')
                tp_str = f'+{tp_pct:.2f}%' if tp_pct is not None else '—'
                side_str = 'LONG' if ps.get('side') in ('BUY', 'LONG') else 'SHORT'
                covered = self.stop_covers_entry(ps) if ps.get('entry') is not None else False
                marin = ('TRAILING RUNNER' if ps.get('trail_on') else
                         ('BREAKEVEN LOCKED' if covered else 'BE TARGET (soft)') if ps.get('be_locked')
                         else ('MICRO-BE LOCKED' if covered else 'MICRO-BE TARGET (soft)') if ps.get('mbe_locked')
                         else f'{side_str} OPEN (TP1: {tp_str})')
                sl_val = ps.get('sl')
                sl_str = f'{sl_val:g}' if sl_val is not None else '—'
                qty_str = ps.get('qty_str') or str(ps.get('qty') or '')
                label = (marin if name == 'MARIN' else
                         f'GUARD {ps.get("stop_kind", "SL")} {sl_str}' if name == 'RUNE' else
                         f'FILLED {side_str} {qty_str}' if name == 'VESKA' else
                         f'HOLDING {side_str}')
            elif veto:
                state, label = 'VETO', (v.reason if v else 'veto')[:48]
            elif side in ('BUY', 'SELL'):
                state, label = 'VOTING', f'VOTING {side} ({conf * 100:.0f}%)'
            else:
                state, label = 'SCANNING', 'SCANNING'
            rows.append({'agent': name, 'side': side, 'veto': veto, 'confidence': conf,
                         'reason': v.reason if v else 'boot', 'state': state, 'label': label})
        return rows

    def position_row(self) -> Optional[dict]:
        v = self.view
        amt = v.get('amt') or 0.0
        if not amt:
            return None
        entry, mark = v.get('entry') or 0.0, v.get('mark') or self.features.get('mark') or 0.0
        margin = v.get('margin') or abs(amt) * entry / self.p.leverage
        upnl = v.get('upnl') or 0.0
        p = self.pos or {}
        opened = p.get('opened')
        age_s = (self.clock() - opened) if opened is not None else None
        mfe_val = p.get('mfe_pct')
        eject_in = (max(0.0, self.p.eject_s - (self.clock() - opened))
                    if opened is not None and self.p.eject and mfe_val is not None and mfe_val < self.p.eject_mfe_pct else None)
        return {'symbol': self.p.symbol, 'side': 'LONG' if amt > 0 else 'SHORT', 'size': abs(amt),
                'entry': entry, 'mark': mark, 'upnl': upnl, 'roe_pct': upnl / margin * 100 if margin else 0.0,
                'liq': v.get('liq') or None, 'leverage': self.p.leverage,
                'margin_type': 'Cross' if self.p.margin_type == 'CROSSED' else 'Isolated', 'margin': margin,
                'tp': p.get('tp'), 'sl': p.get('sl'), 'age_s': age_s,
                'stop_kind': p.get('stop_kind'), 'be_locked': p.get('be_locked'), 'trail_on': p.get('trail_on'),
                'stop_covers_entry': self.stop_covers_entry(p) if p.get('entry') is not None else None,
                'protection': (None if not p else 'EXCHANGE STOP >= ENTRY' if self.stop_covers_entry(p)
                               else 'SOFT TARGET ONLY' if p.get('soft_stop') is not None else 'HARD SL ONLY'),
                'mbe_locked': p.get('mbe_locked'), 'adverse_usd': p.get('adverse_usd'),
                'soft_stop': p.get('soft_stop'), 'soft_exits': self.p.soft_exits,
                'exiting': None if not p.get('exiting') else {k: p['exiting'][k] for k in
                                                               ('reason', 'px', 'reprices', 'started', 'floor')},
                'flow_exit_usd': p.get('flow_limit') or self.feed.cfg.flow_exit_usd * self.feed.cfg.usd_scale,
                'breath_left_s': max(0.0, self.p.flow_breath_s - (self.clock() - opened)) if opened is not None else None,
                'regime_flip_usd': self.p.regime_flip_usd * self.feed.cfg.usd_scale,
                'shield': bool(p.get('be_locked')), 'plock': bool(p.get('plock')),
                'gross_pct': p.get('gross_pct'), 'hurdle': p.get('hurdle'), 'hurdle_pct': self.p.hurdle_pct,
                'ratchet': bool(p.get('ratchet')), 'size_mult': p.get('size_mult'),
                'alt_boost': bool(p.get('alt_boost')),
                'eject_in_s': eject_in,
                'risk_at_stop': self.risk_at_stop(),
                'bracket_src': p.get('bracket_src'), 'rr_gross': p.get('rr_gross'), 'rr_net': p.get('rr_net'),
                'be_wr': p.get('be_wr'), 'atr_pct': p.get('atr_pct'), 'trail_gap': p.get('trail_gap'),
                'hurdle_blocks': p.get('hurdle_blocks', 0), 'fee_hurdle': self.p.fee_hurdle,
                'fee_shield_trigger_pct': self.p.be_trigger_pct, 'lock_trigger_pct': self.p.lock_trigger_pct,
                'microbe_trigger_pct': self.p.microbe_trigger_pct,
                'tp1_filled': p.get('tp1_filled'), 'tp_qty': p.get('tp_qty'), 'mfe_pct': p.get('mfe_pct'),
                'be_price': self.be_price(p) if p and self.spec else None,
                'be_trigger_pct': self.p.be_trigger_pct, 'entry_type': p.get('entry_type'),
                'hold_s': p.get('hold_s'), 'tag': p.get('tag', 'EXTERNAL'), 'path': p.get('path')}

    def quant_view(self) -> dict:
        f = self.features or {}
        bp = {k: v for k, v in self.bracket_plan('SWARM', f).items() if k != 'profile'}
        return {'ofi': f.get('ofi_10s'), 'ofi_n': f.get('ofi_n'), 'vpin': f.get('vpin'), 'vpin_n': f.get('vpin_n'),
                'vpin_side': self.toxic_side if self.clock() < self.toxic_until else None, 'symbol': self.p.symbol,
                'tox': self.tox.get('state'), 'toxic_left': max(0.0, self.toxic_until - self.clock()),
                'atr_pct': f.get('atr_pct'), 'yz_pct': f.get('yz_pct'), 'sigma_bps': f.get('sigma_bps'),
                'bars_n': f.get('bars_n'), 'brackets': bp, 'kelly': self.kelly()}

    def fee_model(self) -> Optional[dict]:
        px = (self.features or {}).get('price')
        if not px or not self.spec:
            return None
        pl = plan('BUY', px, self.spec, self.p, None)
        if not pl.get('ok'):
            return {'ok': False, 'why': pl.get('why')}
        n = pl['notional']
        return {'ok': True, 'qty': pl['qty_str'], 'notional': n, 'maker_rt_usdt': 2 * n * self.p.maker_bps / 1e4,
                'taker_exit_usdt': n * (self.p.maker_bps + self.p.taker_bps) / 1e4,
                'fee_breakeven_pct': 2 * self.p.maker_bps / 100}

    def snapshot(self) -> dict:
        d, f = self.decision, self.features
        acc = self.account or {}
        lat = sorted(self.latency)
        if self.pos:
            tp_val = self.pos.get('tp')
            sl_val = self.pos.get('sl')
            tp_str = f"{tp_val:g}" if tp_val is not None else "—"
            sl_str = f"{sl_val:g}" if sl_val is not None else "—"
            qty_str = self.pos.get("qty_str", self.pos.get("qty", ""))
            tag_str = self.pos.get("tag", "MANUAL")
            pos_status = f'POSITION {self.pos["side"]} {qty_str} {self.p.symbol} · TP {tp_str} · SL {sl_str} · {tag_str}'
        else:
            pos_status = None
        return {
            'mode': f'{self.p.mode.upper()} · {self.venue.label}', 'engine_mode': self.p.mode,
            'venue': self.venue.label, 'symbol': self.p.symbol,
            'status': self.halted or pos_status or ('ORDER IN FLIGHT' if self.busy else (d.reason if d else
                                       f'VENUE OFFLINE · {self.last_error}' if self.last_error else 'CONNECTING VENUE')),
            'phase': self.phase,
            'armed': self.started, 'equity': acc.get('wallet'), 'wallet': acc.get('wallet'),
            'available': acc.get('available'), 'pnl': self.day_pnl + (acc.get('upnl') or 0.0),
            'day_pnl': self.day_pnl, 'volume': 0, 'fees': None,
            'score': d.score if d else 0.0, 'threshold': .768, 'veto_threshold': .70,
            'agents': self.agent_rows(), 'authorized': self.authorized, 'auth_at': self.auth_at,
            'decision': None if d is None else {'side': d.side, 'score': d.score, 'allowed': d.allowed,
                                                'reason': d.reason, **{k: self.diag.get(k) for k in
                                                                     ('jev', 'edge_bps', 'path', 'side',
                                                                      'confluence')}},
            'signals': {k: v for k, v in self.sig.items()},
            'regime': {k: v for k, v in (self.reg or {}).items()},
            'features': {k: f.get(k) for k in ('price', 'mark', 'bid', 'ask', 'spread_bps', 'obi', 'cvd_norm',
                                               'cvd_60s', 'cvd_10s', 'vwap', 'vwap_dev_pct', 'range_pos',
                                               'liq_long_usd', 'liq_short_usd', 'stale', 'trades_silent',
                                               'book_age_s', 'trade_age_s', 'trades', 'cvd_3m', 'cvd_vel_10s',
                                               'cvd_vel_60s', 'depth10_imb', 'box_pct', 'box_lo', 'box_hi',
                                               'ker', 'ker_span_s', 'cvd_15s', 'px_vel_15s', 'vwap_5m',
                                               'vwap_5m_span', 'ema5_20', 'ema5_slope', 'ema5_n')},
            'absorption': self.absorb, 'macro': {k: v for k, v in self.macro.items() if k != 'allow'},
            'vfr': self.vfr_view(),
            'chop': {'ker': f.get('ker'), 'locked': self.chop_locked, 'gate': self.p.ker_gate,
                     'lock': self.sig_cfg.ker_lock, 'unlock': self.sig_cfg.ker_unlock,
                     'reason': self.chop_reason() if self.chop_locked else None},
            'market_data': 'binance-ws' if not f.get('stale') else 'stale',
            'position': self.position_row(), 'open_orders': self.orders, 'trade_history': list(self.history)[:30],
            'session': {'trades': len(self.history), 'wins': sum(1 for r in self.history if (r.get('pnl') or 0) > 0),
                        'realized': self.day_pnl, 'kill': self.p.daily_kill_usdt,
                        'gross': sum(r['gross'] for r in self.history if r.get('gross') is not None
                                     and r.get('fee') is not None),
                        'fees': sum(r['fee'] for r in self.history if r.get('gross') is not None
                                    and r.get('fee') is not None),
                        'rows_unknown_cost': sum(1 for r in self.history if r.get('gross') is None
                                                 or r.get('fee') is None)},
            'cost': self.cost.view(),
            'stats': self.stats(), 'stats_today': self.stats(list(self.history)),
            'exec_book': {k: v for k, v in self.book().items()} if self.spec else None,
            'profile': asdict(self.p), 'live_allowed': self.allow_live, 'symbols': list(SYMBOLS),
            'fee_model': self.fee_model(),
            'quant': self.quant_view(),
            'portfolio': self.portfolio_view(),
            'flow': {**self.flow, 'gate': self.p.concordance_gate, 'drift_lock': self.p.drift_lock,
                     'drift': (self.reg or {}).get('name') == 'DRIFT'},
            'governor': {'min_score': self.p.min_score, 'min_edge_bps': self.p.min_edge_bps,
                         'max_trades_hour': self.p.max_trades_hour, 'trades_hour': len(self.trade_times),
                         'cooldown_s': self.p.cooldown_s, 'flow_breath_s': self.p.flow_breath_s,
                         'flow_exit_usd': self.feed.cfg.flow_exit_usd * self.feed.cfg.usd_scale,
                         'regime_flip_usd': self.p.regime_flip_usd * self.feed.cfg.usd_scale},
            'latency_ms': {'last': self.latency[-1] if self.latency else None,
                           'p50': lat[len(lat) // 2] if lat else None},
            'markets': [], 'events': list(self.events)[:30], 'updated_at': self.clock(),
        }


# ============================================================================ runner (one process: WS + engine + UI)
def fetch_klines(symbol: str, interval: str = '1m', limit: int = 30, timeout: float = 5.0) -> list:
    """Closed candles [(open_s, o, h, l, c, close_s)] from Binance USD-M mainnet public REST (no key)."""
    import urllib.request
    url = f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol.upper()}&interval={interval}&limit={int(limit)}'
    with urllib.request.urlopen(url, timeout=timeout) as r:
        rows = json.loads(r.read().decode())
    now_ms = time.time() * 1000
    return [(k[0] / 1000.0, float(k[1]), float(k[2]), float(k[3]), float(k[4]), k[6] / 1000.0)
            for k in rows if k[6] < now_ms]


def fetch_klines_5m(symbol: str, limit: int = 40, timeout: float = 5.0) -> list:
    """Closed 5 m candles [(close_time_s, close)]."""
    return [(r[5], r[4]) for r in fetch_klines(symbol, '5m', limit, timeout)]


def build_scanner(profiles: dict, venue, feeds: dict, telemetry=None, ledger: Path = LEDGER,
                  allow_live: bool = False) -> dict:
    """One Engine per symbol over ONE venue and ONE RiskBook: shared governor, ledger view and position slot."""
    risk = RiskBook()
    return {s: Engine(profiles[s], venue, feeds[s], telemetry, ledger=ledger, allow_live=allow_live, risk=risk)
            for s in profiles}


def _touch(feed) -> tuple:
    if feed is None:
        return 0.0, 0.0
    return feed.bid or feed.price() or 0.0, feed.ask or feed.price() or 0.0


class Runner:
    def __init__(self, mode: str, allow_live: bool, env: Optional[dict] = None, symbol: Optional[str] = None,
                 port: int = 8765, serve: bool = True, overrides: Optional[dict] = None, scan: bool = True):
        self.env = read_env() if env is None else env
        self.scan = scan
        self.engines: dict = {}
        self.focus_sym: Optional[str] = symbol.upper() if symbol else None
        self.allow_live, self.symbol, self.port, self.serve = allow_live, symbol, port, serve
        self.overrides = overrides or {}
        self.mode = self.start_mode = mode
        self.symbols = {mode: symbol.upper()} if symbol else {}
        self.engine: Optional[Engine] = None
        self.client = None
        self.tasks: list = []
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.tel = None
        self.ws = {'connected': False, 'error': '', 'reconnects': 0}

    def profile(self, mode: str) -> Profile:
        p = PROFILES[mode]
        sym = self.symbols.get(mode)
        if sym and sym != p.symbol:
            p = replace(p, symbol=sym, qty=SYMBOL_QTY.get(sym, 0.0) if mode == 'demo' else p.qty)
        if mode != self.start_mode:                       # CLI overrides belong to the mode they were typed for
            return p
        return replace(p, **{k: v for k, v in self.overrides.items() if v is not None})

    def profile_for(self, mode: str, symbol: str) -> Profile:
        p = self.profile(mode)
        if symbol != p.symbol:
            p = replace(p, symbol=symbol, qty=SYMBOL_QTY.get(symbol, 0.0))
        return p

    def refocus(self) -> Optional['Engine']:
        """UI / controls follow the engine holding the slot, else the symbol the user picked."""
        hot = next((e for e in self.engines.values() if e.pos or e.busy or abs(e.view.get('amt') or 0)), None)
        if hot is not None or self.engines:
            self.engine = hot or self.engines.get(self.focus_sym) or next(iter(self.engines.values()))
        return self.engine

    async def build_multi(self, mode: str) -> dict:
        from live.binance_futures_client import BinanceFuturesClient
        feeds = {s: msig.MicroFeed(s, msig.config_for(s)) for s in SCAN_SYMBOLS}
        profiles = {s: self.profile_for(mode, s) for s in SCAN_SYMBOLS}
        key, sec = credentials(mode, self.env)
        if not (key and sec):
            venue = MockVenue(profiles['BTCUSDT'], quote=lambda s: _touch(feeds.get(s)))
        else:
            env = dict(self.env, BINANCE_TESTNET_API_KEY=key, BINANCE_TESTNET_SECRET_KEY=sec)
            self.client = await BinanceFuturesClient(Settings.load('testnet', env=env)).__aenter__()
            venue = BinanceVenue(self.client, profiles['BTCUSDT'].margin_type)
        return build_scanner(profiles, venue, feeds, self.tel, LEDGER, self.allow_live)

    def live_ready(self) -> tuple:
        key, sec = credentials('live', self.env)
        if not self.allow_live:
            return False, 'khởi động bằng "run.bat live" (gõ xác nhận) mới mở được LIVE'
        if not (key and sec):
            return False, 'thiếu BINANCE_MAINNET_API_KEY / SECRET_KEY trong .env.local'
        return True, 'ok'

    async def build(self, mode: str) -> Engine:
        from live.binance_futures_client import BinanceFuturesClient
        p = self.profile(mode)
        feed = msig.MicroFeed(p.symbol, msig.config_for(p.symbol))
        key, sec = credentials(mode, self.env)
        if mode == 'demo' and not (key and sec):
            venue = MockVenue(p, quote=lambda s: (feed.bid or feed.price() or 0.0, feed.ask or feed.price() or 0.0))
        elif mode == 'demo':
            env = dict(self.env, BINANCE_TESTNET_API_KEY=key, BINANCE_TESTNET_SECRET_KEY=sec)
            self.client = await BinanceFuturesClient(Settings.load('testnet', env=env)).__aenter__()
            venue = BinanceVenue(self.client, p.margin_type)
        else:
            ok, why = self.live_ready()
            if not ok:
                raise PermissionError(why)
            env = dict(self.env, BINANCE_MAINNET_API_KEY=key, BINANCE_MAINNET_SECRET_KEY=sec)
            self.client = await BinanceFuturesClient(Settings.load('mainnet', arm=True, env=env)).__aenter__()
            venue = LiveVenue(self.client, p.margin_type)
        return Engine(p, venue, feed, self.tel, ledger=LEDGER, allow_live=self.allow_live)

    async def pump(self, feed: msig.MicroFeed, route: str = 'market') -> None:
        await self.pump_multi({feed.symbol: feed}, route)

    @staticmethod
    def demux(feeds: dict, msg: dict) -> bool:
        """Route one combined-stream frame to its symbol's feed ('solusdt@aggTrade' -> SOLUSDT)."""
        f = feeds.get(str(msg.get('stream', '')).split('@')[0].upper()) if isinstance(msg, dict) else None
        if f is None:
            return False
        f.on_message(msg)
        return True

    async def pump_multi(self, feeds: dict, route: str = 'market') -> None:
        import websockets
        backoff = 1.0
        url = msig.streams_multi(list(feeds))[route]
        while True:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20,
                                              open_timeout=8, max_queue=4096) as ws:
                    self.ws.update(connected=True, error='')
                    self.ws[route] = True
                    backoff = 1.0
                    async for raw in ws:
                        try:
                            self.demux(feeds, json.loads(raw))
                        except ValueError:
                            continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.ws.update(connected=False, error=f'{route}: {type(exc).__name__}: {exc}'[:160])
            self.ws[route] = False
            self.ws['reconnects'] += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 15.0)

    async def decide_loop(self, e: Engine) -> None:
        while True:
            if e.started and e.feed.last_msg:
                try:
                    await e.tick()
                except Exception as exc:
                    e.event(f'tick error {type(exc).__name__}: {exc}')
            await asyncio.sleep(0.2)

    def idle(self, e: Engine) -> bool:
        """A flat OBSERVER engine polls REST slowly (rate-limit budget: 3 symbols on one key).
        10-08v: cadence follows trading permission, not which symbol the dashboard shows - before, a BTC
        observer in focus left the tradable SOL/DOGE engines at book 3 s / sync 4 s."""
        return len(self.engines) > 1 and not (e.pos or e.busy) and e.observer

    def cadence(self, e: Engine, period: float = 1.2) -> tuple:
        """(venue book s, REST sync s) for one engine.  Decisions never wait on these (decide_loop 0.2 s on the
        WS feed; _execute re-reads the venue book before quoting).  Weight/sync ~12 (positionRisk 5 + account 5
        + 2 order lists), bookTicker 2, ~0.6 s REST per sync: position/busy (0.3, 1.2) ~500/min, focused flat
        (1.0, 1.2) ~500, other flat tradable (2.0, 3.0) ~260, flat observer (3.0, 4.0) ~190 -> <= ~960/min with
        3 symbols flat.  While any engine holds a position / order, the other flat engines drop to (3.0, 4.0):
        the protected position keeps the REST budget (same total as before 10-08v)."""
        if e.pos or e.busy:
            return 0.3, period
        if self.idle(e) or any(x.pos or x.busy for x in self.engines.values() if x is not e):
            return 3.0, 4.0
        if len(self.engines) > 1 and e is not self.engine:
            return 2.0, max(period, 3.0)
        return 1.0, period

    async def vbook_loop(self, e: Engine) -> None:
        while True:
            if e.started and e.own_book:
                await e.refresh_book(0.0)
                await asyncio.sleep(self.cadence(e)[0])
            else:
                await asyncio.sleep(1.0)

    async def sync_loop(self, e: Engine) -> None:
        period = 0.5 if isinstance(e.venue, MockVenue) else 1.2
        while True:
            try:
                if not e.started:
                    await e.start()
                await e.locked_sync()
            except Exception as exc:
                e.last_error = f'{type(exc).__name__}: {exc}'[:160]
                e.event(f'sync error {e.last_error}')
                await asyncio.sleep(3)
            await asyncio.sleep(self.cadence(e, period)[1])

    async def publish_loop(self) -> None:
        n = 0
        while True:
            e = self.refocus()
            if e is not None and self.tel is not None:
                snap = e.snapshot()
                snap['scanner'] = self.scanner_view(e)
                live_ok, live_why = self.live_ready()
                snap.update(ws=dict(self.ws), live_ready=live_ok, live_reason=live_why,
                            demo_keys=all(credentials('demo', self.env)))
                self.tel.update(**snap)
                n += 1
                if n % 2 == 0:
                    self.tel.persist()
                if self.tel.state.get('shutdown'):
                    e.event('KILL SWITCH -> flatten + halt')
                    e.halted = 'KILL SWITCH'
                    await kill_switch(list(self.engines.values()) or [e])
                    await asyncio.sleep(1.0)
                    raise SystemExit(0)
            await asyncio.sleep(0.5)

    def scanner_view(self, e: Engine) -> dict:
        """Scanner block of the SSE snapshot.  intents / fills / cancels are session counters summed over the
        engines (10-08v: app.js read them, the engine never published them -> 'undefined')."""
        es = list(self.engines.values())
        return {'on': len(es) > 1, 'focus': e.p.symbol, 'rows': [x.radar() for x in es],
                **xf.merge_counters(x.funnel.counters() for x in es), 'counters_scope': 'session',
                'funnel': {x.p.symbol: x.funnel.view() for x in es},
                'feasibility': {x.p.symbol: x.lane_feasibility() for x in es}}

    def default_focus(self) -> str:
        """First engine allowed to trade (BTC observer only when nothing else exists)."""
        return next((s for s, x in self.engines.items() if not x.observer), next(iter(self.engines)))

    async def activate(self, mode: str) -> Engine:
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []
        if self.client is not None:
            await self.client.__aexit__(None, None, None)
            self.client = None
        if self.scan and mode == 'demo':
            self.engines = await self.build_multi(mode)
        else:
            e = await self.build(mode)
            self.engines = {e.p.symbol: e}
        self.mode = mode
        if self.focus_sym not in self.engines:
            sym = self.symbols.get(mode)
            self.focus_sym = sym if sym in self.engines else self.default_focus()
        e = self.refocus()
        feeds = {s: x.feed for s, x in self.engines.items()}
        self.tasks = [asyncio.create_task(self.pump_multi(feeds, 'public')),
                      asyncio.create_task(self.pump_multi(feeds, 'market'))]
        for x in self.engines.values():
            self.tasks += [asyncio.create_task(self.seed_macro(x)), asyncio.create_task(self.seed_bars(x)),
                           asyncio.create_task(self.sync_loop(x)),
                           asyncio.create_task(self.vbook_loop(x)), asyncio.create_task(self.decide_loop(x))]
        return e

    async def seed_bars(self, e: Engine) -> None:
        """ATR(14) / Yang-Zhang / ATR 15 m need closed 1 m bars: seed 240 (4 h) from public klines."""
        try:
            rows = await asyncio.to_thread(fetch_klines, e.feed.symbol, '1m', 240)
            n = e.feed.seed_bars([r[:5] for r in rows])
            e.event(f'ATR 1m: seeded {n} closed 1 m bars -> ATR brackets live at once')
        except Exception as exc:
            e.event(f'ATR 1m: kline seed failed ({type(exc).__name__}) -> static brackets until '
                    f'{e.feed.cfg.atr_min_bars} live bars')

    async def seed_macro(self, e: Engine) -> None:
        """EMA-20(5 m) needs 100 min of candles: seed it once from mainnet public klines (no key)."""
        try:
            closes = await asyncio.to_thread(fetch_klines_5m, e.feed.symbol)
            n = e.feed.seed_candles(closes)
            e.event(f'MACRO 5m: seeded EMA-20 with {n} closed 5 m candles (VWAP 5 m warms from live prints)')
        except Exception as exc:
            e.event(f'MACRO 5m: kline seed failed ({type(exc).__name__}) -> EMA warms live (100 min)')

    async def switch_mode(self, mode: str, confirm: str = '') -> dict:
        if mode not in MODES:
            return {'ok': False, 'error': 'mode must be demo or live'}
        e = self.engine
        if mode == self.mode:
            return {'ok': True, 'mode': mode, 'note': 'đang ở chế độ này'}
        if any(x.pos or x.busy or abs(x.view.get('amt') or 0) for x in (list(self.engines.values()) or ([e] if e else []))):
            return {'ok': False, 'error': 'đóng vị thế trước khi đổi chế độ'}
        if mode == 'live':
            ok, why = self.live_ready()
            if not ok:
                return {'ok': False, 'error': why}
            if confirm != LIVE_CONFIRM:
                return {'ok': False, 'error': f'gõ "{LIVE_CONFIRM}" để xác nhận tiền thật'}
        try:
            await self.activate(mode)
        except Exception as exc:
            await self.activate('demo')
            return {'ok': False, 'error': f'{type(exc).__name__}: {exc}'[:200]}
        return {'ok': True, 'mode': mode}

    async def switch_symbol(self, symbol: str) -> dict:
        symbol = str(symbol or '').upper()
        if symbol not in SYMBOLS:
            return {'ok': False, 'error': f'symbol must be one of {", ".join(SYMBOLS)}'}
        if len(self.engines) > 1 and symbol in self.engines:      # scanner: all symbols run, the button picks the view
            self.focus_sym = symbol
            self.refocus()
            return {'ok': True, 'symbol': symbol, 'note': 'scanner: đổi màn hình theo dõi (cả 3 cặp vẫn quét)',
                    'focus': self.engine.p.symbol if self.engine else symbol}
        e = self.engine
        if e and e.p.symbol == symbol:
            return {'ok': True, 'symbol': symbol, 'note': 'đang dùng symbol này'}
        if e and (e.pos or e.busy or abs(e.view.get('amt') or 0)):
            return {'ok': False, 'error': 'đóng vị thế trước khi đổi coin'}
        old = self.symbols.get(self.mode)
        self.symbols[self.mode] = symbol
        try:
            await self.activate(self.mode)
        except Exception as exc:
            self.symbols[self.mode] = old
            await self.activate(self.mode)
            return {'ok': False, 'error': f'{type(exc).__name__}: {exc}'[:200]}
        return {'ok': True, 'symbol': symbol}

    async def run(self) -> None:
        from core.web_ui_server import Telemetry, make_server
        import threading
        self.loop = asyncio.get_running_loop()
        self.tel = Telemetry()
        if self.serve:
            server = make_server(self.tel, port=self.port, shutdown_file=SHUTDOWN_FILE, controller=Controller(self))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            print(f'  UI  http://127.0.0.1:{self.port}', flush=True)
        await self.activate(self.mode)
        await self.publish_loop()


class Controller:
    """Thread-safe bridge: HTTP handler thread -> engine event loop."""

    def __init__(self, runner: Runner):
        self.r = runner

    def _call(self, coro, timeout: float = 20.0) -> dict:
        try:
            return asyncio.run_coroutine_threadsafe(coro, self.r.loop).result(timeout)
        except Exception as exc:
            return {'ok': False, 'error': f'{type(exc).__name__}: {exc}'[:200]}

    def force_test_trade(self, body: dict) -> dict:
        self.r.refocus()
        if self.r.engine is None:
            return {'ok': False, 'error': 'engine chưa chạy'}
        return self._call(self.r.engine.force_test_trade(str(body.get('side', 'BUY')).upper()))

    def switch_mode(self, body: dict) -> dict:
        return self._call(self.r.switch_mode(str(body.get('mode', '')).lower(), str(body.get('confirm', ''))), 60)

    def switch_symbol(self, body: dict) -> dict:
        return self._call(self.r.switch_symbol(body.get('symbol', '')), 60)

    def close_position(self, body: dict) -> dict:
        self.r.refocus()
        if self.r.engine is None:
            return {'ok': False, 'error': 'engine chưa chạy'}
        return self._call(self.r.engine.close_now())


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description='GROKTOPUS 2-mode engine: demo | live')
    ap.add_argument('--mode', choices=MODES, default='demo')
    ap.add_argument('--symbol', default=None, help='default demo BTCUSDT, live DOGEUSDT')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--arm-live', default='', help=f'must equal "{LIVE_CONFIRM}" to allow mainnet orders')
    ap.add_argument('--tp', type=float, default=None, help='TP1 %% (50 %% of size, rest trails)')
    ap.add_argument('--sl', type=float, default=None, help='stop %% (demo; live derives it from max loss)')
    ap.add_argument('--hold', type=float, default=None, help='optional max hold seconds (default: none)')
    ap.add_argument('--jev-min', type=float, default=None)
    ap.add_argument('--no-scan', action='store_true', help='demo: trade only --symbol instead of scanning BTC/SOL/DOGE')
    a = ap.parse_args(argv)
    allow_live = (a.arm_live or os.environ.get('GROKTOPUS_ARM_LIVE', '')) == LIVE_CONFIRM   # typed per session
    if a.mode == 'live' and not allow_live:
        raise SystemExit(f'LIVE bị khoá: chạy run.bat live và gõ {LIVE_CONFIRM} để xác nhận.')
    overrides = {'tp_pct': a.tp, 'sl_pct': a.sl if a.mode == 'demo' else None,
                 'max_hold_s': a.hold, 'jev_min': a.jev_min}
    print('=' * 86)
    print(f'  GROKTOPUS ENGINE · mode {a.mode.upper()} · live {"ARMED" if allow_live else "LOCKED"} · Ctrl+C dừng')
    print('=' * 86, flush=True)
    r = Runner(a.mode, allow_live, symbol=a.symbol, port=a.port, overrides=overrides, scan=not a.no_scan)
    try:
        asyncio.run(r.run())
    except KeyboardInterrupt:
        print('\n[STOP] Ctrl+C. Vị thế (nếu có) vẫn được STOP_MARKET trên sàn bảo vệ.')


if __name__ == '__main__':
    main()
