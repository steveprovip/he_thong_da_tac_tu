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

from config.settings import Settings, micro_capital_env, read_env
from core import micro_signals as msig
from core import exec_funnel as xf
from core import micro_swarm as swarm
from core import regime_classifier as rc
from core import invariants as inv
from core import toxicity_guard as tg
from core.harvest_markout import MarkoutLog
from core.consensus_engine import ConsensusEngine, Decision, TokenAuthority
from core.position_sizer import MicroCapitalSizer
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


CARRY_LOG_SCHEMA = 1


def write_carry_rows(ledger, rows) -> int:
    """11-10 (j): append the simulated carry book's rows (research.carry_lane.CarrySim.records) to carry_sim.jsonl beside
    the trade ledger -> rows written. Bookkeeping only: an engine without a ledger path writes nothing and a failed
    write is swallowed, so the funding loop never depends on it."""
    if not ledger or not rows:
        return 0
    try:
        lines = [json.dumps({**r, 'schema': CARRY_LOG_SCHEMA, 'code': CODE_VERSION}, ensure_ascii=False) for r in rows]
        with open(Path(ledger).parent / 'carry_sim.jsonl', 'a', encoding='utf-8') as fh:
            fh.write('\n'.join(lines) + '\n')
    except (OSError, TypeError, ValueError):
        return 0
    return len(lines)


def save_carry_state(ledger, state) -> bool:
    """11-10 (k): the simulated carry book's snapshot -> carry_sim_state.json beside the trade ledger, written to a .tmp
    and renamed (a kill in mid-write leaves the previous file). Bookkeeping: a failure is swallowed."""
    if not ledger:
        return False
    try:
        path = Path(ledger).parent / 'carry_sim_state.json'
        tmp = path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(state), encoding='utf-8')
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError):
        return False
    return True


def load_carry_state(ledger) -> Optional[dict]:
    """-> the saved snapshot, or None when there is none or it cannot be read (the book then starts fresh)."""
    if not ledger:
        return None
    try:
        state = json.loads((Path(ledger).parent / 'carry_sim_state.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) else None


def carry_gap_prints(windows: dict, fetch) -> dict:
    """{coin: funding prints of both venues} for each offline window (blocking: run it in a thread). A coin whose
    history cannot be read is left out, which CarrySim.restore reads as UNKNOWN (nothing credited)."""
    out = {}
    for coin, (start_ms, end_ms) in windows.items():
        try:
            out[coin] = fetch(coin, start_ms, end_ms)
        except Exception:
            continue
    return out


def jev_lift(path: str, raw_consensus: Optional[float]) -> Optional[bool]:
    """Label JEV authorizations whose raw swarm score was below the fixed 76.8% quorum."""
    if raw_consensus is None:
        return None if path == 'JEV' else False
    return path == 'JEV' and raw_consensus < inv.SUPERMAJORITY - 1e-12


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
MICRO_UNIVERSE = ('DOGEUSDT', 'XRPUSDT', 'ADAUSDT', '1000PEPEUSDT')   # 11-09 micro-capital rotation: low nominal price,
                                                                     # a 5.1-6.0 U order fits their lot step


def scan_universe(rotation: bool, micro_symbols: tuple = MICRO_UNIVERSE) -> tuple:
    """Symbols the demo scanner runs: BTC / SOL / DOGE, plus the micro coins when the rotation is on."""
    return SCAN_SYMBOLS + tuple(x for x in micro_symbols if x not in SCAN_SYMBOLS) if rotation else SCAN_SYMBOLS
ASSET_BRACKETS = {'BTCUSDT': (0.15, 0.38), 'SOLUSDT': (0.35, 0.85), 'DOGEUSDT': (0.50, 1.25)}  # (SL min %, TP1 min %)
LIVE_CONFIRM = 'LIVE'
FALLBACK_SPECS = {
    'BTCUSDT': {'step': 0.001, 'min_qty': 0.001, 'min_notional': 100.0, 'tick': 0.1},
    'ETHUSDT': {'step': 0.001, 'min_qty': 0.001, 'min_notional': 20.0, 'tick': 0.01},
    'SOLUSDT': {'step': 0.01, 'min_qty': 0.01, 'min_notional': 5.0, 'tick': 0.01},
    'DOGEUSDT': {'step': 1.0, 'min_qty': 1.0, 'min_notional': 5.0, 'tick': 0.00001},
    'ADAUSDT': {'step': 1.0, 'min_qty': 1.0, 'min_notional': 5.0, 'tick': 0.0001},           # 11-09 offline fallbacks:
    '1000PEPEUSDT': {'step': 1.0, 'min_qty': 1.0, 'min_notional': 5.0, 'tick': 0.0000001},   # check exchangeInfo
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
    be_offset_pct: float = 0.08         # SOFT maker BE = entry + maker in + maker out + 0.08 % (11-09: was 0.02; the
                                        # offset covers fee_shield 0.04 + a taker escalation 0.04 = 5 - 2 + 1 slip bps;
                                        # be_offset_eff() floors it at BE_OFFSET_FLOOR: 0.08, SOL 0.065)
    be_offset_hard_pct: float = 0.02    # HARD exchange-stop BE = stop_out_pct() (maker in + taker stop + slip) + 0.02 %
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
    scalp_tp_pct: float = 0.32          # maker scalp TP; route fees yield net RR 1.40 at 0.12 % SL
    scalp_sl_pct: float = 0.12          # exchange stop
    scalp_sl_min_pct: float = 0.15      # 11-08 owner: SCALP stop after regime scaling never tighter than this (0 = off)
    scalp_floor_resize: bool = True     # 11-08 owner: when that floor widens the stop, size shrinks so the loss at the
                                        # stop (fees in, as plan() counts it) stays what the unfloored stop would lose
    scalp_net_rr_min: float = 1.35      # 11-08 owner: SCALP net-RR floor (max with net_rr_min); TP lifted to clear it
    scalp_macro_lock: bool = True       # 11-08 owner: scalp side must be allowed by the 5 m macro AND the CVD regime
    scalp_cvd3m_usd: float = 50_000.0   # 11-08 owner: no SELL scalp at CVD 3 m > +$50k, no BUY < -$50k (0 = off)
    scalp_align3: bool = True           # 11-08 owner: an 'on' scalp needs every ALIGN3 layer with it (even align3 shadow)
    scalp_net_rr: bool = True           # SCALP orders use the same ZEPHR net-RR gate as other AUTO entries
    scalp_min_edge_bps: float = 18.0    # modeled edge floor; UNKNOWN is not treated as zero
    scalp_min_score: float = 0.768
    scalp_be_s: float = 45.0            # TP not filled after 45 s -> maker exit at entry + fees (EJECT-BE; 11-08 owner, was 90)
    scalp_hard_s: float = 180.0
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
    eject_guard: bool = True            # 11-09: the EJECT floor (scratch order) is dropped for a touch exit only once gross
                                        # >= the fee hurdle, adverse momentum / toxic flow is confirmed, or ...
    eject_defer_max_s: float = 120.0    # ... this many seconds past eject_hard_s (bounded: the time stop survives)
    scalp_tp_mode: str = 'static'       # 11-09: 'static' = scalp_tp_pct (+ net-RR lift) | 'atr' = clamp(mult x ATR15)
    scalp_tp_atr_mult: float = 0.6      # 'atr': TP = 0.6 x ATR 15 m, clamped to [min, max]. At SL 0.15 % that is net RR
    scalp_tp_min_pct: float = 0.09      # ~0.35 (break-even win rate 70-79 %): it only takes effect with scalp_net_rr=False
    scalp_tp_max_pct: float = 0.14
    collinear_penalty: float = 0.5      # 11-09: TIDAL flow-family vote x this when LUMEN says the same thing (1.0 = off)
    kelly_no_edge_gate: bool = True     # 11-09: Kelly state NO EDGE (f* <= 0) -> no AUTO entry, no minimum-size fallback
    edge_shadow: bool = True            # 11-09 shadow path: while NO EDGE blocks, each refused AUTO entry opens ONE virtual
                                        # trade (no order); the gate lifts only when THEIR Kelly is EDGE (n >= kelly_min_trades)
    edge_shadow_every_s: float = 20.0   # at most one virtual entry per 20 s
    edge_shadow_ttl_s: float = 30.0     # the virtual maker quote must be traded THROUGH within 30 s, else NOFILL (uncounted)
    edge_shadow_hold_s: float = 600.0   # no TP / SL after 10 min -> virtual exit at the touch (maker)
    micro_capital: str = 'auto'         # 11-09 small-account rules: 'auto' = while the wallet <= micro_equity_max | 'on' | 'off'
    micro_equity_max: float = 15.0      # (config/settings.py: MICRO_CAPITAL_MODE / MICRO_CAPITAL_EQUITY_MAX)
    micro_notional_min: float = 5.05    # entry notional window [5.05, 6.0] U: venue minimum 5 U (-4164) + buffer ...
    micro_notional_max: float = 6.0     # ... and a ceiling; the smallest lot inside it, else no trade
    micro_max_loss_usdt: float = 0.05   # loss at the stop incl. fees <= 0.05 U (1 % of 5 U)
    micro_daily_kill_usdt: float = 0.15
    micro_loss_pause_s: float = 86_400.0  # max_consec_losses (3) AUTO losses in a row -> no entry for 24 h (survives a restart)
    micro_symbols: tuple = MICRO_UNIVERSE  # the coins that may trade (one at a time); the rest observe (signals, no order)
    micro_max_positions: int = 1
    micro_max_same_side: int = 2        # 11-09g: at most this many open/in-flight micro positions on ONE side (the micro coins are
                                        # correlated: 3 same-side lots behave like one 3x position); 0 = off. Needs > 1 slot
    micro_rotation: bool = True         # a FORCED micro run scans the whole micro universe, not only the focus symbol
    micro_rank_s: float = 5.0           # router: re-rank the micro coins every 5 s by Momentum Quality = KER x |OFI L10|
    micro_bankroll: float = 5.0         # equity a FORCED micro run on a wallet > micro_equity_max pretends to have
    micro_margin_frac: float = 0.85     # compounding: margin never above 85 % of equity ...
    micro_growth_frac: float = 0.20     # ... and notional tracks 20 % of buying power (E = 10 U -> 10 U), loss ceiling applies
    micro_ttl: bool = True              # 11-09 adaptive GTX TTL while micro is active (AUTO, not MR / SCALP)
    micro_ttl_s: float = 10.0           # clean book (VPIN < micro_vpin_clean, markout non-toxic): 3 s -> 10 s ...
    micro_ttl_max_s: float = 15.0       # ... then +1 s steps up to 15 s
    micro_vpin_clean: float = 0.65
    micro_max_reprices: int = 2         # smart re-pegs: at most 2, then the order keeps its queue place
    micro_ioc: str = 'off'              # 11-09 hybrid IOC in micro mode: 'off' | 'shadow' | 'on' (I5: 'on' is reported)
    micro_ioc_consensus: float = 0.78   # RAW swarm score >= 0.78 (11-09b: was 0.80) ...
    micro_ioc_ofi: float = 0.70         # ... OFI L10 with the trade >= 0.70 (was 0.80) ...
    micro_ioc_min_tp_pct: float = 0.20  # ... TP >= 0.20 % (was 0.25) AND fee-true net RR with the taker entry ...
    micro_ioc_net_rr_min: float = 1.05  # ... >= 1.05 (was net_rr_min 1.30). With a taker entry and stop (0.12 % of fees)
                                        # that needs TP >= 1.05 x SL + 0.206 %: the 0.20 % TP floor is moot, the RR binds
    micro_net_rr_min: float = 1.10      # 11-09b micro mode: net-RR floor of every AUTO entry (was 1.30 / scalp 1.35); the
                                        # break-even win rate is 1 / (1 + RR) = 47.6 %, not a guaranteed edge
    micro_tp_lift: bool = True          # micro mode: after the regime x0.75 / x1.5 multipliers raise TP back toward the unscaled
                                        # TP until the net-RR floor holds (never above it), instead of refusing the setup
    micro_bracket: bool = True          # 11-09d micro brackets: the stop never under the noise floor, TP sized for net RR 1.40
    micro_sl_floor_pct: float = 0.22    # SL >= 0.22 % (the 0.075 % stops of the 21:33 trades were 5-8 ticks: stopped in 7 s / 11 s)
    micro_sl_atr_mult: float = 1.2      # ... or 1.2 x ATR 1 m, whichever is larger
    micro_bracket_rr: float = 1.40      # TP = (SL + in + taker out + slip) x 1.40 + (in + maker out): 0.46 % at SL 0.22 %
    micro_tp_atr_mult: float = 1.8      # ... and at least 1.8 x ATR 1 m (non-scalp)
    micro_align: bool = True            # 11-09d micro entries need KER and a triple alignment (below)
    micro_ker_min: float = 0.28         # KER (2 min) >= 0.28 = TREND lane (11-09e: was 0.30); below it = CHOP lane (the harvester)
    micro_ofi_min: float = 0.65         # OFI L10 WITH the trade >= 0.65 ...
    micro_cvd3m_usd: float = 0.0        # ... CVD 3 m must not oppose the trade (x usd_scale; 0 = any opposing sign blocks) ...
                                        # ... and the 5 m macro must not forbid the side. Unknown = not shown = blocked
    micro_cvd_soft: bool = True         # 11-09e: CVD 3 m is lagging; when OFI L10 >= micro_cvd_soft_ofi AND the 1 m price move
                                        # favour the trade it only SIZES the trade down (x micro_cvd_soft_mult), no hard block
    micro_cvd_soft_ofi: float = 0.50
    micro_cvd_soft_mult: float = 0.75   # cannot go under the venue lot: at E <= ~6.7 U the lot is already the minimum
    micro_px1m_atr: float = 0.5         # "1 m price favours the trade" = the 60 s move WITH it >= 0.5 x ATR 1 m ...
    micro_px1m_min_pct: float = 0.03    # ... and at least 0.03 % (ATR unknown -> this alone); unknown 60 s move = not favouring
    micro_harvester: str = 'shadow'     # 11-09e CHOP lane (micro, KER < micro_ker_min): 'off' | 'shadow' (log would-be harvests)
                                        # | 'on' (maker orders; a Demo rehearsal, armed live refuses it). Replay of the sister
                                        # scalper shape: -6.3..-7.5 bps net -> shadow until a time-separated holdout says otherwise
    micro_harvest_tp_pct: float = 0.15  # gross TP 0.12-0.18 % (net = TP - 0.04 % maker round trip; 0.12 % locks 0.08 %)
    micro_harvest_sl_pct: float = 0.15  # stop; with the stop costs (maker in + taker out + slip = 0.08 %) the break-even win
                                        # rate at 0.15 / 0.15 is 67.6 %: the lane has NO fee-true RR >= 1, it lives on win rate
    micro_harvest_be_wr_max: float = 0.70  # refuse a harvest whose break-even win rate is above this (replaces the net-RR floor)
    micro_harvest_ttl_s: float = 45.0   # an unfilled resting entry is cancelled after 45 s (hard cuts: adverse / spread / drift)
    micro_harvest_rotate_ker: float = 0.15  # a coin with KER < 0.15 is dead tape: it neither harvests nor competes for the slot
    micro_sniper_tp_max_pct: float = 0.70   # TREND lane: TP slides from the RR-1.40 TP (0.46 %) to 0.70 % as KER goes ...
    micro_sniper_ker_full: float = 0.60     # ... from micro_ker_min to this (the MARIN trail takes over from +0.50 % MFE)
    micro_sniper_runner: str = 'off'    # 11-10 (e) 'off' | 'on' (opt-in, Demo; armed live refuses it; UNVALIDATED). A micro AUTO entry on
                                        # the SWARM / SWARM_OVERRIDE path whose raw score AND consensus are >= swarm_override_min_score
                                        # is managed as a RUNNER: the resting TP moves out to micro_runner_tp_pct (a cap, not a target),
                                        # the time-decay ejects (EJECT / EJECT-FAST) do not apply, and the +0.50 % trail gap becomes
                                        # max(trail_gap_pct, micro_runner_trail_atr x ATR 1 m at entry). Entry gates, the net-RR check
                                        # (judged on the UNCHANGED bracket), the exchange SL, RATCHET / RATCHET2 and the 0.05 U loss
                                        # cap are untouched. Ledger: 0 of 129 trades ever reached MFE +0.5 % (max 0.441 %)
    micro_runner_tp_pct: float = 2.5
    micro_runner_trail_atr: float = 2.0
    micro_runner_inval_ofi: float = 0.70    # 11-10 (g) a runner's early invalidation is ARMED (the profile-wide one stays 'shadow'):
                                        # inside early_inval_s, OFI L10 <= -0.70 against it or CVD velocity <= -early_inval_vel_usd
                                        # -> maker exit at the touch (EARLY-INVALID). 0 = off. UNVALIDATED: of the 5 ledger trades
                                        # whose first shadow trigger was |OFI| >= 0.70 the early exit was better in 2, worse in 3
    micro_runner_stale_s: float = 420.0     # 11-10 (g) a runner has no 120 / 180 s eject; open this long with MFE still under
    micro_runner_stale_mfe_pct: float = 0.20    # this, it gets the EJECT-BE scratch (floor, then the touch eject_hard_s - eject_s
                                        # later, fee-hurdle guard as for every eject). 0 s = off: SL / ratchet / flow / regime only
    micro_equity_track: bool = True     # forced micro on a big (Demo) wallet: virtual equity = micro_bankroll + realized micro
                                        # PnL (capped at max(micro_equity_max, micro_bankroll)): every won USDT raises the lot
    micro_accel_k: float = 0.0          # 11-09f lot = plain compounding target x (1 + k x realized micro PnL USDT), capped at one
                                        # slot's margin share; 0 = off. Demo profile 5.0; live must stay 0 (invariant)
    micro_exit_policy: str = 'legacy'   # 11-10 chop-harvester exits: 'legacy' = EJECT-BE at 45 s (touch exit above the BE floor, TP
                                        # cancelled), unfilled at 180 s -> maker at the touch | 'patient' (opt-in, UNVALIDATED): the TP
                                        # stays, a BE scratch needs MFE >= micro_patient_be_mfe_pct after micro_patient_be_s, and
                                        # escalation to a negative touch needs toxic flow (below); the exchange SL is the only other exit
    micro_patient_be_s: float = 180.0
    micro_patient_be_mfe_pct: float = 0.09  # 60 % of the 0.15 % TP
    micro_patient_vpin: float = 0.80        # toxic escalation: VPIN >= this OR adverse flow >= flow_limit OR regime flipped
    micro_harvest_log: bool = True          # 11-10 Phase 1 (plan_micro_adverse_selection): per-ORDER feature vector + post-post / post-fill markouts
                                            # (signal feed and venue book) -> data/harvest_markouts.jsonl. Bookkeeping only: no order depends on it
    micro_harvest_macro_soft: bool = True   # 11-09f chop harvester: a 5 m macro that forbids the side no longer blocks when ...
    micro_harvest_macro_ofi: float = 0.30   # ... OFI L10 is WITH the trade by >= 0.30 (11-10: was 0.55; = the harvest OFI floor, so the
                                            # 5 m macro no longer gates the chop lane at all). The CVD flow regime stays a block.
    micro_harvest_ofi_min: float = 0.30     # 11-10: OFI L10 floor of a chop-lane (KER < micro_ker_min) candidate; the micro ALIGN floor
                                            # micro_ofi_min 0.65 never applied there - it is the TREND-lane floor (KER >= 0.28)
    micro_jumpy_scalp: bool = True      # micro mode: a JUMPY regime does not freeze the scalp lane when ...
    micro_jumpy_spread_bps: float = 2.0  # ... the spread is under 2 bps and ...
    micro_jumpy_vpin: float = 0.70      # ... VPIN is KNOWN and under 0.70 (the 0.3x size policy cannot go under the venue lot)
    micro_max_spread_bps: float = 3.0   # a resting micro entry is pulled when the spread widens past 3 bps
    micro_adverse_bps: float = 3.0      # ... or the mid moves this far against it (the owner spec says 0.15 %: see the plan)
    raw_quorum_gate: bool = True        # 11-09: AUTO entry needs the RAW swarm score >= 0.768; a JEV-lifted one is refused
    alt_risk_share: float = 0.85       # 85 % of portfolio_risk_usdt for SOL/DOGE: BTC may use only 15 % (0.15 U)
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
    kelly_window: int = 20
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
    # 10-08x adaptive KER for super-conviction TREND entries (form RESOLVE_CHOP_DEADLOCK, formula as given):
    # ker_lock_effective = max(0.15, ker_lock - 0.20 x (score - 0.90)) when score >= 0.95, CVD velocity 10 s
    # >= 3 000 USD/s with the trade and swarm confluence 3/3.  At score 0.99 this lowers 0.38 to 0.362 (the
    # hysteresis band up to ker_unlock is also passed while locked).  'off' | 'shadow' (SHADOW_KER rows) | 'on'.
    # Shadow: replay 10/01-10/07 SOL+DOGE: flow-surge entries lose 7-8 bps per fill in EVERY KER band.
    adaptive_ker: str = 'shadow'
    adaptive_ker_score: float = 0.95
    adaptive_ker_cvd_vel_usd: float = 3_000.0
    adaptive_ker_confluence: int = 3
    adaptive_ker_floor: float = 0.15
    adaptive_ker_slope: float = 0.20
    # 10-08x MR bracket: 'vwap' = TP |VWAP dev| clamped [mr_tp_min, mr_tp_max], SL mr_sl_pct (net RR <= 0.875,
    # blocked by net_rr_min) | 'fixed' = TP mr_tp_fixed / SL mr_sl_fixed.  0.29 / 0.10 is the smallest TP that
    # reaches net RR >= 1.35 with real fees (maker in, taker stop): the form's 0.22-0.26 / 0.10-0.12 gives 1.00-1.10.
    # Default 'vwap': replay of every MR variant -7 bps per fill (CI < 0) on dev and holdout.
    mr_bracket: str = 'vwap'
    mr_tp_fixed: float = 0.29
    mr_sl_fixed: float = 0.10
    # Regime classifier entry gate. Demo overrides the long research window with a 5-minute trend window while
    # preserving UNKNOWN fail-closed until the classifier has enough observations and confirmation time.
    regime_gate: str = 'off'
    regime_trend_window_s: float = 900.0
    regime_er_bucket_s: float = 30.0
    regime_er_min_span_s: float = 600.0
    regime_confirm_s: float = 60.0
    regime_tail_z: float = 4.5
    regime_tail_count: int = 3
    regime_tail_memory_s: float = 300.0
    regime_crash_tail_z: float = 4.5
    regime_crash_tail_count: int = 1
    regime_crash_flatten: bool = True
    # Demo-only CHOP escape: normal quorum, all eight votes, risk-gate agreement, and VESKA token remain required.
    swarm_chop_override: bool = False
    swarm_override_min_score: float = 0.80
    swarm_override_tp_pct: float = 0.50
    swarm_override_sl_pct: float = 0.18
    swarm_override_sl_max_pct: float = 0.25
    scalp_only: bool = False
    chop_router_scalp: bool = False
    tox_guard: str = 'off'
    tox_vpin_max: float = 0.65
    tox_skew_bps: float = 1.5
    tox_pull_bps: float = 3.0
    tox_min_n: int = 30
    tox_own_min_n: int = 10
    tox_own_window: int = 20
    tox_skew_ticks: int = 1
    tox_hold_cooldown_s: float = 5.0
    # 10-08y Dynamic Conviction Sizing (form POSITION_SIZING_10PCT): notional = fraction x wallet, SOL/DOGE TREND
    # entries only: score in [0.80, 0.90) -> 3 %, [0.90, 0.95) -> 5 %, >= 0.95 with OFI >= 0.85 -> 10 %.  Never above
    # the free portfolio risk budget (portfolio_risk_usdt at the stop, fees included) or the margin cap: the size is
    # CLIPPED to them, the caps are not raised.  'off' | 'shadow' (cs_* fields on the order row, size unchanged) |
    # 'on'.  Shadow: ledger SOL mean -2.0 bps per trade [CI -14.6, +9.1] - no evidence that a larger size or the
    # score adds edge (Monte Carlo: tools/simulate_position_sizing.py).
    conviction_sizing: str = 'shadow'
    cs_tiers: tuple = ((0.95, 0.10), (0.90, 0.05), (0.80, 0.03))
    cs_super_ofi: float = 0.85
    cs_max_notional: Optional[float] = None   # 10-08z hard notional ceiling for a conviction size (demo $1 000)
    cs_breaker_usdt: float = 5.0        # 10-08z: conviction-sized trades lost >= 5 U over the last 30 -> back to the
    cs_breaker_window: int = 30         # normal size until the window recovers (logged, no manual step needed)
    # 10-08z pre-stop maker exit: once the EXCHANGE stop is in profit (ratchet), the soft maker exit level is kept
    # at least pre_stop_gap_pct beyond it, so a retrace meets the post-only exit BEFORE the taker stop (#49: soft BE
    # +0.06 % sat under the ratchet stop +0.08 % -> STOP_MARKET, 6 bps of fees).  Replay 10/06-07 holdout:
    # +0.25 bps per position [0.12, 0.38] (fill through) / +0.38 [0.24, 0.53] (fill at the touch); on ratchet-stop
    # cases +4.7 / +7.0 bps.  The exchange stop stays as the safety net.
    pre_stop_maker: bool = True
    pre_stop_gap_pct: float = 0.04
    # 10-08z scaled TP1: the resting 50 % maker TP sits at +tp1_scaled_pct (TREND only, when the size splits);
    # the runner keeps the ATR plan (net RR gate still evaluates the ATR TP).  Replay: +0.08 bps [0.04, 0.14].
    tp1_scaled_pct: Optional[float] = 0.30
    tp1_scaled_big_pct: float = 0.35    # 10-08z2: TP1 >= +0.35 % when the entry notional >= cs_big_notional ($225)
    cs_big_notional: float = 225.0
    # 10-08z2 regime-adaptive conviction size caps (notional USD): chop (KER < 0.30 or the 3 flows disagree) $170
    # (~1.5 SOL), trend (KER >= 0.40 and flows agree) $315 (~2.8 SOL), otherwise $225; cs_max_notional is the hard cap.
    cs_chop_notional: float = 170.0
    cs_mid_notional: float = 225.0
    cs_trend_notional: float = 315.0
    cs_chop_ker: float = 0.30
    cs_trend_ker: float = 0.40
    # 10-08z2 hard macro CVD shield (all lanes): no BUY while CVD 60 s < -300 k USD or CVD 3 m < -500 k; no SELL while
    # > +300 k / +500 k (raw USD of the signal feed; the strong-trend regime ban is already swarm REGIME BAN).
    # Replay 10/01-07: neutral (holdout -6.42 vs -6.56 bps, dev -6.82 vs -6.60); the ledger's losers #51/#53/#55
    # were all WITH the macro flow.  'off' | 'on'.
    cvd_shield: str = 'on'
    cvd_shield_60s_usd: float = 300_000.0
    cvd_shield_3m_usd: float = 500_000.0
    # 10-08z2 three-layer alignment (CVD 3 m, CVD 60 s, CVD 15 s, OFI/OBI with the trade). 'off'|'shadow'|'on'.
    # Shadow: replay ALIGN3 (CVD layers) -7.27 vs -6.56 bps holdout, -7.16 vs -6.60 dev (worse: late entries).
    align3: str = 'shadow'
    # 10-08z2 early adverse invalidation: within early_inval_s of the fill, CVD velocity 10 s >= 50 k USD/s against
    # the position or OFI L10 flipped beyond -early_inval_ofi -> maker exit at the touch (no fee hurdle).
    # 'off' | 'shadow' (ledger field early_invalid) | 'on'.  Shadow: replay mixed (dev +0.09, holdout -0.51 bps).
    early_invalidation: str = 'shadow'
    early_inval_s: float = 45.0
    early_inval_vel_usd: float = 50_000.0
    early_inval_ofi: float = 0.30
    # 10-09 multi-horizon trend lane (core/trend_lane.py): 'off' | 'paper'.  PAPER ONLY - targets and paper fills
    # go to data/trend_paper_<bar>.jsonl, never an order.  Evidence is a hypothesis (OOS DSR < 0.95, PBO 0.35):
    # docs/research_upgrade/report_high_speed_replay_and_horizon_pivot_2026-10-09.md.
    trend_lane: str = 'off'
    # 10-09 (dd) 'active' = core/trend_executor.py trades the 4H lane on the DEMO venue (demo-only guard in code).
    trend_notional_min: float = 150.0
    trend_notional_max: float = 300.0
    trend_exec: str = 'maker'               # 'taker' | 'maker' | 'swarm' (execution timing, replay-chosen)
    trend_exec_window_s: float = 300.0
    trend_disaster_atr: float = 8.0         # exchange-side stop, ATR(14, 4h); replay: 8 ATR costs ~0.04 Sharpe
    trend_active_every_s: float = 60.0
    # 10-09 (ee) regime gate (core/trend_lane.RegimeGate): 'off' | 'trend_only' (KER10 > 0.40 and ADX14 > 25) |
    # 'no_chop' (not KER < 0.25 and ADX < 20); trend_confirm = volume > 1.2 x SMA20 and bar CVD with the entry;
    # trend_chop_exit = flat on a CHOP close.  Replay tools/trend_regime_replay.py (DEV-selected G6).
    # 10-16: 'hurst' = entries only in the TRENDING tri-state (variance-ratio Hurst(60) > 0.55, trend_lane.tri_state);
    # unvalidated by replay -> not selected by any Profile.  Telemetry trend_lane targets carry hurst / state3.
    trend_regime: str = 'off'
    trend_confirm: bool = False
    trend_chop_exit: bool = False
    # 10-09 (ee) soft profit lock: armed once the best 4H close is >= trend_lock_pct % in favour, then a close
    # beyond best -/+ trend_lock_atr x ATR(14) ('chandelier') or the 20 d Donchian mid ('donchian_mid') exits.
    trend_lock_pct: Optional[float] = None
    trend_lock_trail: str = 'chandelier'
    trend_lock_atr: float = 3.5
    # 10-09 (ee) sizing: notional = min(capital_allocation_pct, trend_risk_pct / (trend_disaster_atr x ATR / px))
    # x wallet, <= trend_notional_max.  None = legacy |weight| x wallet clipped to [min, max].
    capital_allocation_pct: Optional[float] = None
    trend_risk_pct: float = 0.01
    # 10-09 (ff) BTC macro shield for alt entries: 'off' | 'shadow' (log only) | 'sign' | 'trend'; eject = flat on
    # a close where BTC is TREND against the alt.  Replay: every enforced variant lowered OOS Sharpe -> shadow.
    trend_btc_align: str = 'off'
    trend_btc_eject: bool = False
    # 10-09 (ff) break-even: a close >= trend_pyr_arm_pct % in favour moves the exchange stop to entry +/- be %.
    trend_be_pct: Optional[float] = None
    trend_pyr_arm_pct: float = 2.0
    # 10-09 (ff) conviction: 1H + 4H + 1D agree -> min(conv alloc, conv risk / 8 ATR stop) x wallet.
    trend_conv_alloc_pct: Optional[float] = None
    trend_conv_risk_pct: float = 0.02
    # 10-10 lane 2, sub-minute volatility-burst scalper (core/burst_lane.py): 'off' | 'shadow' | 'active'.  Active is
    # refused unless config/burst_verdict.json says ACTIVE-ELIGIBLE for the symbol (replay 10-10: every variant on
    # SOL/DOGE USDT + USDC negative, -4 to -8 bps/trade -> SHADOW).  Values = the replay's pre-registered DEV-best.
    burst_lane: str = 'off'
    burst_wake_hold_s: float = 0.0
    burst_symbols: tuple = ('SOLUSDT', 'DOGEUSDT')
    burst_signal: str = 'MR'                # 'MR' fade the 10 s move | 'MOM' follow the 10 s CVD
    burst_tp_bps: float = 30.0
    burst_sl_bps: float = 30.0
    burst_sig1m_bps: float = 15.0
    burst_ofi: float = 0.85
    burst_surge: float = 3.0
    burst_repeg_s: float = 4.0
    burst_hold_s: float = 60.0
    burst_exit_maker_s: float = 10.0
    # 10-16 RFC-Q4.2: 'taker' = hold_s -> maker exit_maker_s -> TIME-TAKER (the replayed policy) | 'maker' = keep the
    # post-only exit pegged at our touch until fill, SL, or burst_exit_cap_s (then a TIME-CAP taker).  Replay SOL
    # 09-05/06/08 MR30 through-fills: taker -7.30 bps/trade, maker+cap 240 s -6.62 (taker share 52 % -> 1 %).
    burst_time_exit: str = 'taker'
    burst_exit_cap_s: float = 240.0
    # 10-16: skip entries the engine's ABSORPTION TRAP (micro_signals.absorption) would block on the same side.
    burst_absorb_gate: bool = False
    # 10-16 (b) RFC-Q4.3: no burst entry while the capital router is CHOP or the symbol's KER 120 s < 0.25 (unknown KER
    # does not gate), nor within burst_vpin_veto_s of the engine's last VPIN > vpin_max mark (either side; 0 = off).
    # Replay SOL 3 d: CHOP entries -6.66 vs others -6.28 bps/trade (CIs overlap) -> fewer trades, not a better trade.
    burst_chop_gate: bool = False
    burst_vpin_veto_s: float = 0.0
    burst_lat_s: float = 0.25               # shadow fills: an order is live / cancelled one latency later
    burst_notional_usdt: float = 75.0
    burst_budget_pct: float = 0.05
    burst_daily_kill_usdt: float = 10.0
    burst_trend_quiet_s: float = 600.0      # no new burst entry in the 10 min after a 4H close (trend lane's turn)
    burst_every_s: float = 0.25
    # 10-10 b lane 3, intraday 5m / 15m micro-swing (core/intraday_lane.py): 'off' | 'shadow' (one virtual book per
    # variant = tools/intraday_replay.py).  'active' is refused: no variant is ACTIVE-ELIGIBLE in
    # config/intraday_verdict.json (replay 10-10 b: every pre-registered variant negative) and no executor is built.
    intraday_lane: str = 'off'
    intraday_symbols: tuple = ('BTCUSDT', 'SOLUSDT', 'DOGEUSDT')
    intraday_variants: tuple = ('R1_SESSION', 'P15_tp50', 'P15_rr25atr')   # 10-10 c/d DEV-best R1 / P (shadow)
    intraday_notional_usdt: float = 150.0
    intraday_budget_pct: float = 0.04
    intraday_daily_kill_usdt: float = 25.0
    intraday_hold_min: float = 45.0
    intraday_slip_bps: float = 1.0
    intraday_trend_quiet_s: float = 600.0
    intraday_every_s: float = 10.0
    intraday_close_margin_s: float = 10.0   # accept a 1m kline only 10 s after its close on the SERVER clock
    intraday_store_bars: int = 4320         # rolling store of closed 1m klines (3 days; back-filled to 2999 at start)
    # 10-09 (dd) False = the micro lane opens NO standalone position (observer): fee bleed of 2-5 bps targets at
    # 4 bps fees.  Its features keep running (trend execution timing, telemetry).
    micro_standalone: bool = True
    # 10-09 (cc) autonomy hub (core/autonomy.py): 'off' | 'shadow' | 'on'.  On = learned alpha-agent weights in the
    # vote, Thompson bandit on cs_super_ofi / hurdle_min_hold_s / tp1_scaled_pct while flat, hot tunables from
    # config/live_tunables.json (risk only DOWN vs this baseline), dreaming directives, capital router observer.
    autonomy: str = 'off'
    trend_bars: tuple = ('1d', '4h')
    trend_equity: float = 5000.0
    trend_every_s: float = 900.0
    stat_arb_lane: str = 'off'
    stat_arb_pairs: tuple = (('SOLUSDT', 'DOGEUSDT'),)
    stat_arb_notional_usdt: float = 50.0
    stat_arb_every_s: float = 1.0
    # 10-16 (b): cross-venue funding carry quotes (Binance premiumIndex + Hyperliquid metaAndAssetCtxs, public, read-only)
    # -> core.funding_executor.ShadowCarryBook -> telemetry 'funding_carry'.  Virtual book only, never an order.
    funding_shadow: bool = False
    funding_every_s: float = 300.0
    carry_sim: bool = False
    carry_min_net_apr_pct: float = 25.0     # 11-10 (e) CarrySim entry floor on the instantaneous net APR (the RFC rule's 2.5 bps/8 h is
                                            # 25.4 % net already). An annualized instant spread, not a forecast: replay realized 1.90 %/yr
    carry_hold_breakeven: bool = True       # 11-10 (h) CarrySim: a pair is not ROTATEd before its entry spread has had the hours it needs
                                            # to repay the 15 bps round trip (40 h at 3 bps/8 h); guard / basis / flip exits still act
    carry_flip_persist_s: float = 3600.0    # 11-10 (i) CarrySim: a spread <= 0 closes a pair only after it has lasted one Hyperliquid
                                            # funding hour (it closed WLD on a single -0.36 bps/8 h print after 55 min). 0 = at once
    carry_sim_log: bool = True              # 11-10 (j) CarrySim rows (opens, closes with a reason code, flips, a book mark per refresh)
                                            # -> data/carry_sim.jsonl. Bookkeeping only: the book's state lives in memory and was lost
    carry_sim_persist: bool = True          # 11-10 (k) the book is saved to data/carry_sim_state.json every refresh and restored at
                                            # start-up (offline funding from the venues' real prints, else UNKNOWN). Delete the file
                                            # to start a fresh book


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
# 11-09: smallest offset (% above the maker round trip) of a SOFT breakeven target. fee_shield_pct 0.04 + the extra
# cost when the maker exit escalates to a taker order (taker - maker + slip = 5 - 2 + 1 bps = 0.04) = 0.08 %.
BE_OFFSET_FLOOR_DEFAULT = 0.08
BE_OFFSET_FLOOR = {'SOLUSDT': 0.065}


PROFILES = {
    # 0.002 BTC so TP1 can take exactly half (step 0.001). A 0.05 U stop is impossible on BTC:
    # min notional ~$100 x (0.02 % maker + 0.05 % taker) = 0.07 U of fees alone -> demo uses SL 0.20 %.
    'demo': Profile('demo', 'BTCUSDT', qty=0.002, sl_pct=0.20, daily_kill_usdt=5.0, conviction_sizing='on',
                    portfolio_risk_usdt=5.0, cs_max_notional=350.0, trend_lane='active', micro_standalone=True,
                    chop_scalper='on', chop_router_scalp=True, scalp_only=False, scalp_ker_max=0.42, min_score=0.768,
                    mr_min_score=0.768, scalp_min_score=0.768, mr_bracket='fixed',
                    kelly_window=20, burst_wake_hold_s=180.0, scalp_hard_s=180.0, intraday_close_margin_s=20.0,
                    autonomy='on',     # 10-08z2: hard notional cap $350 (was $1 000)
                    # 10-09 (ee) trend lane: DEV-selected gate G6 + soft lock L1 (chandelier 3.5 ATR armed at +2 %),
                    # 10 % of wallet / <= 1 % lost at the 8 ATR disaster stop, <= $600
                    trend_regime='trend_only', trend_confirm=True, trend_chop_exit=True, trend_lock_pct=2.0,
                    capital_allocation_pct=0.10, trend_risk_pct=0.01,
                    # 10-09 (ff) replay-adopted: break-even stop at +2 % (P1) and 1H+4H+1D conviction size C3 (<= 30 %
                    # of wallet, <= 2 % at the 8 ATR stop, <= $1 500); BTC shield logged only (enforced variants lost)
                    trend_be_pct=0.3, trend_conv_alloc_pct=0.30, trend_conv_risk_pct=0.02, trend_btc_align='shadow',
                    trend_notional_max=1500.0,
                    burst_lane='shadow',   # 10-10 lane 2 runs virtual (replay: every variant negative after fees)
                    # 10-16: shadow only - maker-pegged time exit + absorption gate (replay -7.30 -> -6.50 bps/trade,
                    # still negative: no venue use)
                    burst_time_exit='maker', burst_absorb_gate=True,
                    burst_chop_gate=True, burst_vpin_veto_s=60.0,   # 10-16 (b): shadow only, same caveat
                    intraday_lane='shadow',  # 10-10 b lane 3 runs virtual (replay: every variant negative)
                    stat_arb_lane='shadow',  # Stat-Arb cointegration Kalman lane (pairs trading)
                    funding_shadow=True, carry_sim=True, tox_guard='on',
                    scalp_tp_pct=0.32, scalp_net_rr=True, scalp_min_edge_bps=10.0,
                    micro_max_positions=3, micro_accel_k=5.0,   # 11-09f Demo rehearsal: 3 coins at once (<= 0.15 U at all stops), PnL-linked lot
                    regime_gate='on', regime_trend_window_s=60.0, regime_er_bucket_s=5.0,
                    regime_er_min_span_s=45.0,
                    swarm_chop_override=True, swarm_override_min_score=0.80),

    'live': Profile('live', 'DOGEUSDT', sl_pct=0.0, max_loss_usdt=0.05, equity_cap=3.85,
                    daily_kill_usdt=0.15, cooldown_s=60.0, max_trades_hour=6, max_spread_bps=8.0, mock_balance=3.85,
                    atr_scaler=False, kelly=False, convex_sizing=False, adopt_external=False, risk_parity=False,
                    asset_brackets=False, eject=False, alt_boost=False, super_size=False, chop_scalper='off',
                    chop_router_scalp=False, scalp_tp_pct=0.32, scalp_net_rr=True, kelly_window=0,
                    burst_wake_hold_s=0.0, intraday_close_margin_s=10.0, carry_sim=False, tox_guard='off',
                    regime_gate='off', swarm_chop_override=False,
                    drift_accel=False, alt_size_floor=False, eject_fast=False,
                    alt_risk_share=0.0, super_cross='off', dyn_ttl='off', ratchet2=False, adaptive_ker='off',
                    conviction_sizing='off', tp1_scaled_pct=None, align3='off', early_invalidation='off'),  # live: max loss 0.05 U, minimum size, pure maker, VFR gate on
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
        self.day_key: Optional[str] = None

    def roll(self, today: str) -> bool:
        """10-09: the daily-loss meter belongs to ONE calendar day.  Before, day_pnl was only rebuilt at start-up,
        so an engine left running past midnight kept yesterday's losses against today's kill switch."""
        if self.day_key is None:
            self.day_key = today
            return False
        if today != self.day_key:
            self.day_key, self.day_pnl = today, 0.0
            return True
        return False


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
        consensus_errors = inv.check_consensus(self.consensus, regimes=('NORMAL',))
        token_errors = inv.check_token_authority(self.authority)
        if consensus_errors or token_errors:
            raise PermissionError('INVARIANT: ' + (consensus_errors + token_errors)[0])
        profile_errors = inv.check_profile(profile)
        self.invariant_errors = profile_errors
        if profile_errors and profile.mode == 'live' and allow_live:
            raise PermissionError('INVARIANT: ' + '; '.join(profile_errors))
        for error in profile_errors:
            self.event(f'INVARIANT WARN {error}')
        if fresh:
            self.cooldown_until = 0.0
        self.chop_locked = True                       # KER unknown at start -> locked
        self.flow = {'state': 'UNKNOWN', 'buy_ok': False, 'sell_ok': False, 'why': ''}
        self.candidate: Optional[tuple] = None        # (t, quality) of an entry this engine wants (slot routing)
        self.pending_risk = 0.0                       # loss at stop of an entry being worked (portfolio cap)
        self.drift_probe: Optional[str] = None        # set by churn_gate when a DRIFT lock is lifted (half size)
        self.blockers: list = []                      # 10-08v: every churn gate that blocked the last candidate
        self.ker_adapt: Optional[dict] = None         # 10-08x adaptive KER result of the last churn check
        self.align3_last: Optional[str] = None        # 10-08z2 three-layer alignment of the last churn check
        self.funnel = xf.Funnel(clock=self.clock)    # 10-08v: why-no-trade counters (1 Hz time-share)
        self.harvest_log = (MarkoutLog(self.ledger.parent / 'harvest_markouts.jsonl', clock=lambda: self.clock())
                            if self.p.micro_harvest_log and self.ledger else None)     # 11-10 Phase 1
        self.autonomy = None                          # 10-09 (cc) core.autonomy.Autonomy, attached by the Runner
        self.trend_owned = False                      # 10-09 (dd) the venue position belongs to the 4H trend lane
        self.orphans: dict = {}                       # 10-08w: entry cids whose cancel failed -> retried in sync()
        self.scalp_next = 0.0
        # 11-09 NO EDGE shadow path: pos = the one open virtual trade, pnls = settled net % (None = not loaded yet)
        self.edge_shadow: dict = {'pos': None, 'pnls': None, 'next': 0.0, 'released': False}
        self.absorb = {'state': 'UNKNOWN', 'blocks': None}
        self.macro = {'name': 'WARMUP', 'allow': ('BUY', 'SELL')}
        self.tox = {'vpin': None, 'buckets': 0, 'toxic': False, 'state': 'WARMUP'}
        self.toxic_until = 0.0                        # VPIN pause (per symbol)
        self.toxic_side: Optional[str] = None         # side the toxic flow pushes (BUY = aggressive buying)
        self.toxic_vpin: Optional[float] = None
        self.own_mk5: deque = deque(maxlen=max(int(self.p.tox_own_window), 2))
        self.tox_policy = tg.ToxPolicy(vpin_max=self.p.tox_vpin_max, skew_bps=self.p.tox_skew_bps,
                                       pull_bps=self.p.tox_pull_bps, min_n=self.p.tox_min_n,
                                       own_min_n=self.p.tox_own_min_n, skew_ticks=self.p.tox_skew_ticks)
        self.tox_last: Optional[tuple] = None
        self._tox_evt_t = 0.0
        self.regime_clf = (rc.RegimeClassifier(rc.RegimeConfig(
            trend_window_s=self.p.regime_trend_window_s,
            er_bucket_s=self.p.regime_er_bucket_s,
            er_min_span_s=self.p.regime_er_min_span_s,
            confirm_s=self.p.regime_confirm_s,
            tail_z=self.p.regime_tail_z,
            tail_count=self.p.regime_tail_count,
            tail_memory_s=self.p.regime_tail_memory_s,
            crash_tail_z=self.p.regime_crash_tail_z,
            crash_tail_count=self.p.regime_crash_tail_count,
        )) if self.p.regime_gate in ('shadow', 'on') else None)
        self.regime_state = None
        self._regime_evt_t = 0.0
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
            self.risk.day_key = datetime.now().strftime('%Y-%m-%d')
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

    def open_side(self) -> Optional[str]:
        """'BUY' | 'SELL' of this symbol's open position or in-flight entry; None = nothing open OR the side is unknown."""
        if self.pos:
            return self.pos.get('side')
        amt = self.view.get('amt') or 0
        if amt:
            return 'BUY' if amt > 0 else 'SELL'
        return ((getattr(self, '_xm', None) or {}).get('side')) if self.busy else None

    def slot_block(self, side: Optional[str] = None) -> Optional[str]:
        """Why this symbol may not open now: single-slot owner, or portfolio count / risk budget used up."""
        if not self.p.multi_slot:
            o = self.peer()
            return None if o is None else (f'SCANNER slot: {o.p.symbol} '
                                           f'{"position" if o.pos or o.view.get("amt") else "order"} active')
        peers = self.open_peers()
        if len(peers) + 1 > self.max_positions_eff():
            return f'PORTFOLIO FULL {len(peers)}/{self.max_positions_eff()}'
        cap_s = self.p.micro_max_same_side
        if cap_s and side in ('BUY', 'SELL') and self.micro_active():
            n = sum(1 for o in peers if o.open_side() in (side, None))       # an unknown side counts: UNKNOWN is not 'other'
            if n >= cap_s:
                return f'PORTFOLIO SIDE {n}/{cap_s} {side} already open (correlated coins)'
        risk, cap = sum(o.risk_at_stop() for o in peers), self.risk_cap()
        if risk >= cap:
            return f'PORTFOLIO RISK {risk:.2f} >= {cap:g} U' + (' (BTC: phần dành SOL/DOGE)' if cap <
                                                                 self.p.portfolio_risk_usdt else '')
        return None

    def is_alt(self) -> bool:
        return self.p.symbol in ALT_SIZE

    def risk_cap(self) -> float:
        """Money-at-stop budget for a new trade on this coin: BTC gets (1 - alt_risk_share) of the pool."""
        if self.micro_active():
            # 11-09f: with several slots the money at ALL stops, on top of what the day already lost, may not go past the
            # daily kill, so three simultaneous stop-outs cannot overshoot the 0.15 U meter
            return max(0.0, min(self.p.portfolio_risk_usdt, self.daily_kill_eff() + self.day_pnl))
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
        return (self.p.symbol in (self.p.observe_only or ()) or not self.p.micro_standalone
                or (self.micro_active() and self.p.symbol not in self.p.micro_symbols))

    # ------------------------------------------------------------------ micro-capital mode (11-09)
    def micro_sizer(self) -> MicroCapitalSizer:
        c = getattr(self, '_micro_cache', None)
        if c is None or c[0] is not self.p:
            p = self.p
            c = self._micro_cache = (p, MicroCapitalSizer(
                p.micro_equity_max, p.micro_notional_min, p.micro_notional_max, p.micro_max_loss_usdt,
                p.micro_daily_kill_usdt, p.micro_loss_pause_s, p.max_consec_losses, p.micro_max_positions,
                p.micro_symbols, margin_frac=p.micro_margin_frac, growth_frac=p.micro_growth_frac, accel_k=p.micro_accel_k))
        return c[1]

    def micro_wallet(self) -> Optional[float]:
        w = self.account.get('wallet')
        return w if w else self.account.get('available')

    def micro_active(self) -> bool:
        """Small-account rules on: forced by the flag, or the known wallet is <= micro_equity_max (unknown = off)."""
        return self.micro_sizer().active(self.micro_wallet(), self.p.micro_capital)

    def daily_kill_eff(self) -> float:
        return min(self.p.daily_kill_usdt, self.p.micro_daily_kill_usdt) if self.micro_active() else self.p.daily_kill_usdt

    def max_positions_eff(self) -> int:
        return self.p.micro_max_positions if self.micro_active() else self.p.max_positions

    def micro_halt_until(self) -> Optional[float]:
        """Entries halted until this wall time by max_consec_losses AUTO losses, derived from the ledger (restart-proof)."""
        if not self.micro_active():
            return None
        rows = [r for r in self.all_rows if r.get('micro')]      # only trades taken under the micro rules (11-09d)
        return self.micro_sizer().halt_until(rows, self.clock(), lookback_s=self.p.micro_loss_pause_s)

    def micro_pnl(self) -> float:
        """Realized NET pnl (USDT) of the settled `micro`-flagged ledger rows (restart-proof); unknown pnl is skipped, never 0."""
        return math.fsum(float(r['pnl']) for r in self.all_rows if r.get('micro') and r.get('pnl') is not None)

    def micro_equity(self) -> Optional[float]:
        """Equity the compounding sizes from: the wallet; a FORCED run on a wallet above micro_equity_max (a demo
        account) rehearses a micro_bankroll account instead of sizing from thousands of virtual USDT.  11-09e: that
        bankroll compounds the realized micro PnL (it was a constant, so a won USDT never raised the lot), capped at
        max(micro_equity_max, micro_bankroll) so an explicit bankroll above 15 U (MICRO_CAPITAL_BANKROLL) is honoured."""
        w = self.micro_wallet()
        if self.p.micro_capital == 'on' and (w is None or w > self.p.micro_equity_max):
            if not self.p.micro_equity_track:
                return self.p.micro_bankroll
            return min(max(self.p.micro_equity_max, self.p.micro_bankroll), max(0.0, self.p.micro_bankroll + self.micro_pnl()))
        return w

    def micro_window_hi(self) -> float:
        return self.micro_sizer().window_hi(self.micro_equity(), self.p.leverage, self.micro_pnl())

    def micro_check(self, pl: dict) -> Optional[str]:
        """A sized plan outside the notional window, or whose loss at the stop exceeds the budget -> reason."""
        p, hi = self.p, self.micro_window_hi()
        if pl['notional'] > hi + 1e-9 or pl['notional'] < p.micro_notional_min - 1e-9:
            return (f'MICRO: notional {pl["notional"]:.2f} U outside [{p.micro_notional_min:g}, {hi:g}] U')
        if pl['loss_at_sl'] > p.micro_max_loss_usdt + 1e-9:
            return (f'MICRO: loss at the stop {pl["loss_at_sl"]:.4f} U > {p.micro_max_loss_usdt:g} U '
                    f'(SL {pl["sl_pct"]:.3f}% + fees {pl["fees_pct"]:.3f}% on {pl["notional"]:.2f} U)')
        return None

    def micro_bracket(self, sl: float, tp: float, f: dict, route: str = 'gtx', scalp: bool = False) -> tuple:
        """11-09d noise floor -> (sl, tp). SL >= max(micro_sl_floor_pct, micro_sl_atr_mult x ATR 1 m): a stop of 5-8 ticks is
        hit by bid-ask jitter before any alpha shows (the 21:33 PEPE / XRP stops: 7 s and 11 s). TP is only ever raised, to the
        TP whose fee-true net RR at that stop is micro_bracket_rr (micro_net_rr_min for the scalp lane, whose timers cannot
        wait for 0.46 %) and, outside the scalp lane, to micro_tp_atr_mult x ATR 1 m. Loss at the stop stays checked by
        micro_check (<= 0.05 U)."""
        p, atr = self.p, (f or {}).get('atr_pct')
        floor = max(p.micro_sl_floor_pct, p.micro_sl_atr_mult * atr) if atr else p.micro_sl_floor_pct
        sl2 = max(sl, floor)
        rr = p.micro_net_rr_min if scalp else p.micro_bracket_rr
        base_tp = max(self.min_tp_for(sl2, route, rr) if rr else 0.0, p.micro_tp_atr_mult * atr if atr and not scalp else 0.0)
        if not scalp and p.micro_sniper_tp_max_pct > base_tp and self.micro_lane(f) == 'TREND':
            # 11-09e TREND SNIPER: the cleaner the tape (KER micro_ker_min -> micro_sniper_ker_full) the further the TP,
            # from the RR-1.40 TP up to micro_sniper_tp_max_pct. A pure function of (sl2, ATR, KER): applying it twice
            # (bracket_plan, then the regime block of _execute) gives the same TP.
            span = max(p.micro_sniper_ker_full - p.micro_ker_min, 1e-9)
            w = min(1.0, max(0.0, (f['ker'] - p.micro_ker_min) / span))
            base_tp += w * (p.micro_sniper_tp_max_pct - base_tp)
        return sl2, max(tp, base_tp)

    # ------------------------------------------------------------------ 11-09e dual mode: CHOP harvester / TREND sniper
    def micro_lane(self, f: dict) -> Optional[str]:
        """'CHOP' (KER < micro_ker_min) | 'TREND' (KER >= it) | None (KER unknown: no lane, UNKNOWN is not CHOP)."""
        k = (f or {}).get('ker')
        if k is None:
            return None
        return 'CHOP' if k < self.p.micro_ker_min - 1e-12 else 'TREND'

    # ------------------------------------------------------------------ 11-10 Phase 1: harvest attempt log (bookkeeping only)
    HLOG_FEATURES = ('ofi_10s', 'ker', 'vpin', 'spread_bps', 'atr_pct', 'px_vel_15s', 'px_vel_60s', 'cvd_15s', 'cvd_60s', 'cvd_3m',
                     'cvd_vel_10s', 'obi', 'depth10_imb', 'bid_qty', 'ask_qty', 'vwap_dev_pct', 'range_pos')

    def hlog_features(self, f: dict, side: str, px: Optional[float], decision=None) -> dict:
        """Feature vector of one harvest order: the micro features at that moment, our queue proxy (the size at our side of the touch),
        the swarm's view, the venue-vs-signal basis.  None = unknown."""
        f = f or {}
        out = {k: (round(float(f[k]), 6) if isinstance(f.get(k), (int, float)) else None) for k in self.HLOG_FEATURES}
        b = self.book()
        out.update(queue_qty=(b.get('bid_qty') if side == 'BUY' else b.get('ask_qty')), quote_px=px,
                   basis_bps=basis_bps(self.feed.price(), b.get('price')), macro=(self.macro or {}).get('name'),
                   regime=(self.reg or {}).get('name'), flow=(self.flow or {}).get('state'))
        if decision is not None:
            out.update(score=getattr(decision, 'score', None), edge_bps=(self.diag or {}).get('edge_bps'),
                       consensus=(self.diag or {}).get('consensus'))
        return out

    def hlog_call(self, fn, *a, **kw) -> None:
        if self.harvest_log is None:
            return
        try:
            fn(*a, **kw)
        except Exception as exc:                                 # the log must never reach the trading path
            self.harvest_log.errors += 1
            if self.harvest_log.errors == 1:
                self.event(f'HARVEST LOG error {type(exc).__name__}: {exc}'[:160])

    def hlog_post(self, xm: dict, side: str, px: float, f: dict, decision) -> None:
        def go():
            key = xm['hkey'] = f'{self.p.symbol}-{int(self.clock() * 1000)}'
            self.harvest_log.post(key, self.p.symbol, side, px, self.feed.price(), self.book().get('price'),
                                  self.hlog_features(f, side, px, decision),
                                  extra={'macro_soft': bool(xm.get('macro_soft')), 'cvd_soft': xm.get('cvd_soft'),
                                         'qty_req': xm.get('qty_req'), 'policy': self.p.micro_exit_policy,
                                         **({'shadow': True, 'swarm_ok': xm.get('swarm_ok')} if xm.get('shadow') else {})})
        self.hlog_call(go)

    def hlog_fill(self, xm: dict, fill: float) -> None:
        if xm.get('hkey'):
            self.hlog_call(lambda: self.harvest_log.filled(
                xm['hkey'], fill, self.feed.price(), self.book().get('price'),
                self.hlog_features(self.feed.features(self.clock()), xm.get('side'), fill)))

    def hlog_close(self, xm: dict) -> None:
        if xm.get('hkey'):
            self.hlog_call(self.harvest_log.close, xm['hkey'])

    def hlog_step(self, f: dict) -> None:
        if self.harvest_log is not None and self.harvest_log.open:
            self.hlog_call(self.harvest_log.sample, (f or {}).get('price'), self.book().get('price'), self.p.symbol)

    def drift_exempt(self, f: Optional[dict] = None) -> bool:
        """11-10: the DRIFT lock (60 s / 3 m flows disagree: no TREND entry) is a trend-lane guard; in chop the two windows disagree most of
        the time, so the chop harvester lane is not judged by it.  Needs micro_align: the MICRO_KER gate still refuses every non-harvest
        AUTO entry below KER micro_ker_min, so no trend-lane entry gains a path."""
        f = self.features if f is None else f
        return bool(self.p.micro_align and self.harvest_lane(f or {}))

    def harvest_lane(self, f: dict) -> bool:
        """The chop harvester applies: micro account, harvester not 'off', KER known and under micro_ker_min."""
        return (self.p.micro_harvester in ('shadow', 'on') and self.micro_active() and self.micro_lane(f) == 'CHOP')

    def px1m_favors(self, side: str, f: dict) -> bool:
        """The last 60 s of price action moved WITH the trade by >= max(micro_px1m_min_pct, micro_px1m_atr x ATR 1 m)."""
        v = (f or {}).get('px_vel_60s')
        if v is None or side not in ('BUY', 'SELL'):
            return False
        atr = f.get('atr_pct')
        need = max(self.p.micro_px1m_min_pct, self.p.micro_px1m_atr * atr) if atr else self.p.micro_px1m_min_pct
        return (1 if side == 'BUY' else -1) * v >= need - 1e-12

    def cvd3m_soft(self, side: str, f: dict) -> Optional[str]:
        """11-09e: CVD 3 m opposes `side`, yet the fast evidence is strongly with it -> reason (relax the block, size down by
        micro_cvd_soft_mult), else None (not opposing, or not enough fast evidence: the hard block stands).
        Fast evidence = OFI L10 >= micro_cvd_soft_ofi WITH the trade and px1m_favors().  Unknown inputs never relax."""
        p, f = self.p, f or {}
        if not (p.micro_cvd_soft and self.micro_active()) or side not in ('BUY', 'SELL'):
            return None
        d, c3, o = (1 if side == 'BUY' else -1), f.get('cvd_3m'), f.get('ofi_10s')
        if c3 is None or o is None or d * c3 >= -p.micro_cvd3m_usd * self.feed.cfg.usd_scale - 1e-9:
            return None
        if d * o < p.micro_cvd_soft_ofi - 1e-12 or not self.px1m_favors(side, f):
            return None
        return (f'CVD 3m {c3 / 1e3:+.0f}k opposes {side}, relaxed: OFI {o:+.2f} + 1m move {f.get("px_vel_60s"):+.3f}% with it '
                f'-> size x{p.micro_cvd_soft_mult:g}')

    def harvest_score(self, f: dict) -> Optional[float]:
        """Rotation key of the chop lane: ATR 1 m (%) x |OFI L10|. None = unknown or dead tape (KER < micro_harvest_rotate_ker)."""
        f = f or {}
        a, o, k = f.get('atr_pct'), f.get('ofi_10s'), f.get('ker')
        if a is None or o is None or k is None or k < self.p.micro_harvest_rotate_ker - 1e-12:
            return None
        return float(a) * abs(float(o))

    def harvest_pick(self, f: dict) -> tuple:
        """-> (ok, why).  Only the micro coin with the highest harvest_score among the coins in the chop lane harvests; a
        coin in dead chop (KER < micro_harvest_rotate_ker) yields at once to any live rival."""
        mine = self.harvest_score(f)
        if mine is None:
            return False, 'dead or unknown tape (KER < %g / ATR / OFI unknown)' % self.p.micro_harvest_rotate_ker
        best = None
        for o in self.risk.engines:
            if o is self or o.p.symbol not in self.p.micro_symbols:
                continue
            of = o.features or {}
            s = o.harvest_score(of) if o.harvest_lane(of) else None
            if s is not None and s > mine + 1e-12 and (best is None or s > best[1]):
                best = (o.p.symbol, s)
        if best is not None:
            return False, f'yield to {best[0]} (ATR x |OFI| {best[1]:.4f} > {mine:.4f})'
        return True, ''

    def micro_entry_block(self, side: str, f: dict) -> list:
        """11-09d micro entry filter -> [(gate, reason), ...] (empty = pass).  MICRO_KER: KER (2 min) known and >= micro_ker_min.
        MICRO_ALIGN: the 5 m macro does not forbid the side, CVD 3 m does not oppose it, OFI L10 is WITH the trade by at least
        micro_ofi_min.  A missing feature cannot show alignment and blocks (UNKNOWN is not zero).  A fade of the last move
        (the MR lane) has flow against it by construction, so this filter effectively closes the MR lane in micro mode."""
        p, f = self.p, f or {}
        if not (p.micro_align and self.micro_active()) or side not in ('BUY', 'SELL'):
            return []
        d, out = (1 if side == 'BUY' else -1), []
        k = f.get('ker')
        if k is None or k < p.micro_ker_min - 1e-12:
            out.append(('MICRO_KER', f'MICRO KER {"—" if k is None else f"{k:.2f}"} < {p.micro_ker_min:g}: no directional move to reach the TP'))
        bad = []
        if side not in self.macro.get('allow', ('BUY', 'SELL')):
            bad.append(f'macro 5m {self.macro.get("name")} forbids {side}')
        c3, lim = f.get('cvd_3m'), p.micro_cvd3m_usd * self.feed.cfg.usd_scale
        if c3 is None:
            bad.append('CVD 3m unknown')
        elif d * c3 < -lim - 1e-9 and self.cvd3m_soft(side, f) is None:     # 11-09e: strong fast evidence only sizes it down
            bad.append(f'CVD 3m {c3 / 1e3:+.0f}k opposes {side}')
        o = f.get('ofi_10s')
        if o is None:
            bad.append('OFI unknown')
        elif d * o < p.micro_ofi_min - 1e-12:
            bad.append(f'OFI {o:+.2f} not <= -{p.micro_ofi_min:g}' if side == 'SELL' else f'OFI {o:+.2f} not >= +{p.micro_ofi_min:g}')
        if bad:
            out.append(('MICRO_ALIGN', f'MICRO ALIGN {side}: ' + '; '.join(bad)))
        return out

    def micro_scores(self) -> dict:
        """Router: Momentum Quality = KER x |OFI L10| of every micro coin, re-ranked at most every micro_rank_s and
        shared by the engines of one Runner (RiskBook).  Unknown KER or OFI = no score (None), never 0."""
        st = getattr(self.risk, 'micro_rank', None)
        if st is None:
            st = self.risk.micro_rank = {'t': -1e18, 'scores': {}}
        now = self.clock()
        if now - st['t'] >= self.p.micro_rank_s:
            scores = {}
            for o in self.risk.engines:
                if o.p.symbol in self.p.micro_symbols:
                    f = o.features or {}
                    k, of = f.get('ker'), f.get('ofi_10s')
                    scores[o.p.symbol] = None if k is None or of is None else round(float(k) * abs(float(of)), 6)
            st.update(t=now, scores=scores)
        return st['scores']

    def micro_quality(self, d: Decision, diag: dict) -> float:
        """Slot priority in micro mode: this coin's Momentum Quality from the last ranking (+ conviction as a tiebreak)."""
        s = self.micro_scores().get(self.p.symbol)
        return (s if s is not None else 0.0) + 1e-3 * self.conviction(d, diag)

    async def micro_keep(self, side: str, px0: float, mid0: Optional[float], age: float = 1e9) -> tuple:
        """Adaptive TTL (micro mode) -> (keep, reason).  HARD FLOOR (11-09b): until the order has rested micro_ttl_s (10 s)
        only a hard cut applies - the mid moved micro_adverse_bps against the trade, the spread widened past
        micro_max_spread_bps, or the touch left more than peg_max_drift_pct from the first quote.  VPIN warm-up (unknown),
        a dirty VPIN or toxic markouts do not cancel inside the floor (the loop's own 'toxic flow AGAINST our quote' abort
        and tox_guard PULL still run).  Past the floor, going on to micro_ttl_max_s needs a CLEAN book: VPIN known and
        under micro_vpin_clean and the markout guard says QUOTE."""
        p, d = self.p, 1 if side == 'BUY' else -1
        v = self.feed.vpin_now()
        floor = age < p.micro_ttl_s - 1e-9
        if not floor:
            if v is None:
                return False, 'VPIN unknown'
            if v >= p.micro_vpin_clean:
                return False, f'VPIN {v:.2f} >= {p.micro_vpin_clean}'
            action, _, why = self.tox_check(side)
            if action != tg.QUOTE:
                return False, f'markout {action}: {why}'
        mid = self.feed.price()
        if mid and mid0:
            mv = d * (mid / mid0 - 1) * 1e4
            if mv <= -p.micro_adverse_bps:
                return False, f'ADVERSE {mv:+.1f} bps'
        await self.refresh_book(0.5)
        b = self.book()
        bid, ask = b.get('bid'), b.get('ask')
        if bid and ask and ask >= bid:
            sp = (ask - bid) / ((ask + bid) / 2) * 1e4
            if sp > p.micro_max_spread_bps:
                return False, f'spread {sp:.1f} bps > {p.micro_max_spread_bps:g}'
        touch = bid if side == 'BUY' else ask
        if touch and px0 and d * (touch - px0) > 0 and abs(touch / px0 - 1) * 100 > p.peg_max_drift_pct:
            return False, f'touch left {abs(touch / px0 - 1) * 100:.3f}% > {p.peg_max_drift_pct}%'
        return True, ('hard floor' if floor else f'clean VPIN {v:.2f}')

    def micro_ioc_route(self, decision: Decision, f: dict, side: str, path: str, xm: dict) -> str:
        """Hybrid execution (micro mode): 'ioc' only on an extreme directional burst that pays for a taker entry:
        RAW swarm score >= micro_ioc_consensus, OFI L10 with the trade >= micro_ioc_ofi, TP >= micro_ioc_min_tp_pct,
        fee-true net RR with the taker entry >= micro_ioc_net_rr_min, tape guard QUOTE.  Anything else stays Post-Only GTX.
        'shadow' only records what it would have done."""
        p = self.p
        if p.micro_ioc not in ('shadow', 'on') or not self.micro_active():
            return 'gtx'
        d = 1 if side == 'BUY' else -1
        raw, ofi = (self.diag or {}).get('consensus'), (f.get('ofi_10s') or 0.0) * d
        why = None
        if raw is None or raw < p.micro_ioc_consensus - 1e-12:
            why = f'raw consensus {"—" if raw is None else f"{raw:.3f}"} < {p.micro_ioc_consensus}'
        elif ofi < p.micro_ioc_ofi - 1e-12:
            why = f'OFI {ofi:+.2f} < {p.micro_ioc_ofi}'
        else:
            bp = self.bracket_plan(path, f, 'ioc')
            tp, rr = bp.get('tp_pct'), bp.get('rr_net')
            if tp is None or tp < p.micro_ioc_min_tp_pct - 1e-12:
                why = f'TP {"—" if tp is None else f"{tp:.3f}"}% < {p.micro_ioc_min_tp_pct}%'
            elif p.micro_ioc_net_rr_min and (rr is None or rr < p.micro_ioc_net_rr_min - 1e-12):
                why = f'net RR {"—" if rr is None else f"{rr:.2f}"} < {p.micro_ioc_net_rr_min:g} with a taker entry'
            else:
                action, _, tox_why = self.tox_check(side)
                if action != tg.QUOTE:
                    why = f'tox {action}: {tox_why}'
        xm['micro_ioc'] = 'IOC' if why is None else f'GTX: {why}'
        if why is None and p.micro_ioc == 'shadow':
            if self.clock() - getattr(self, '_ioc_evt_t', -1e9) >= 20.0:
                self._ioc_evt_t = self.clock()
                self._shadow_row({'outcome': 'SHADOW_IOC', 'side': side, 'path': path, 'score': decision.score,
                                  'consensus': raw, 'ofi': ofi})
            return 'gtx'
        return 'gtx' if why is not None else 'ioc'

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
        smax = max(self.p.scalp_spread_max_bps, self.p.micro_max_spread_bps) if self.harvest_lane(f) else self.p.scalp_spread_max_bps
        if spread is None or spread > smax:        # 11-09e harvester: the 3 bps micro cap (1 tick of ADA is ~2.5 bps)
            return None, f'spread {spread} > {smax} bps'
        ofi_floor = self.p.micro_harvest_ofi_min if self.harvest_lane(f) else self.p.scalp_ofi_min
        if ofi is None or abs(ofi) < ofi_floor:
            return None, f'OFI {ofi} below {ofi_floor}'
        side = 'BUY' if ofi > 0 else 'SELL'
        if self.toxic_blocks(side):
            return None, 'VPIN toxic against the side'
        macro_note = ''
        if self.p.scalp_macro_lock:     # 11-08: a 10 s OFI blip may not fight the 5 m macro or the CVD regime
            # 11-09f: in the CHOP harvester lane (KER < micro_ker_min) the 5 m macro does not block a resting maker whose
            # side has OFI L10 >= micro_harvest_macro_ofi behind it.  Only the 5 m macro: the CVD flow regime (self.reg,
            # 'STRONG DOWNTREND (SHORT ONLY)') stays a block - it is a different guard and the swarm bans that side in it.
            soft = (self.p.micro_harvest_macro_soft and self.harvest_lane(f)
                    and abs(ofi) >= self.p.micro_harvest_macro_ofi - 1e-12)
            for m in (self.macro or {}, self.reg or {}):
                if side not in m.get('allow', ('BUY', 'SELL')):
                    if soft and m is self.macro:
                        macro_note = f' · macro {m.get("name")} softened (OFI {ofi:+.2f})'
                        continue
                    return None, f'MACRO {m.get("label") or m.get("name")} forbids {side} scalp'
        c3, lim = f.get('cvd_3m', 0.0), self.p.scalp_cvd3m_usd
        if lim and c3 is not None and (c3 > lim if side == 'SELL' else c3 < -lim):   # UNKNOWN: ALIGN3 ('on')
            if self.cvd3m_soft(side, f) is None:        # 11-09e micro: lagging CVD 3 m + strong OFI / 1 m move = size penalty
                return None, f'CVD_3M conflict ({c3:+.0f}) with {side}'
        return side, f'CHOP SCALP {side}: KER {k:.2f} · spread {spread:.2f} bps · OFI {ofi:+.2f}{macro_note}'

    async def chop_scalp(self, d: Decision, f: dict) -> None:
        """Second engine for chop: maker scalp at the touch (on) or a logged would-be scalp (shadow)."""
        harvest = self.harvest_lane(f)              # 11-09e: micro account, KER < micro_ker_min -> the CHOP HARVESTER lane
        mode = self.p.micro_harvester if harvest else self.p.chop_scalper
        if mode not in ('shadow', 'on') or self.busy or self.pos or self.clock() < self.scalp_next:
            return
        side, why = self.scalp_candidate(f)
        if side is None:
            if mode == 'on' and why.startswith(('MACRO ', 'CVD_3M ')):
                self.scalp_lock('MACRO_LOCK' if why.startswith('MACRO ') else 'CVD3M_LOCK', why)
            return
        if harvest:
            ok, why_pick = self.harvest_pick(f)     # ROTATION: the live-chop coin with the highest ATR x |OFI| takes the slot
            if not ok:
                self.scalp_next = self.clock() + self.p.micro_rank_s
                self.funnel.outcome('HARVEST_YIELD')
                return
            why = f'HARVEST {why}'
        # VESKA / AuthToken is NOT bypassed: TokenAuthority.issue refuses a decision under the 76.8 % supermajority, so an
        # 'on' harvest still needs the swarm to back the OFI side (d.score >= scalp_min_score).
        if mode == 'on' and (d is None or d.veto or d.side != side
                             or d.score < self.p.scalp_min_score - 1e-9):
            if harvest:        # 11-10: this return used to be silent. Samples (ticks), not opportunities.
                self.funnel.outcome('SWARM_NO_SIDE' if d is None or d.veto or not d.side else
                                    'SWARM_OPPOSITE' if d.side != side else 'SWARM_WEAK')
            return
        if mode == 'on' and self.p.scalp_align3:
            al = self.align3_last = self.align3_check(side, force=True, f=f)   # ledger: the scalp's own check
            if al:
                self.scalp_lock('ALIGN3_LOCK', al)
                return
        g = self.gates(f)
        if not all(ok for ok, _ in g.as_map().values()):
            return
        if mode == 'on' and self.p.scalp_min_edge_bps:
            edge = (self.diag or {}).get('edge_bps')
            if edge is None or edge < self.p.scalp_min_edge_bps:
                self.scalp_next = self.clock() + self.p.scalp_every_s
                self.funnel.outcome('EDGE_LOW')
                if self.clock() - getattr(self, '_edge_evt_t', 0.0) >= 60.0:
                    self._edge_evt_t = self.clock()
                    self.event(f'EDGE TOO LOW FOR FEES: SCALP {side} edge '
                               f'{"UNKNOWN" if edge is None else f"{edge:.1f}"} bps '
                               f'< {self.p.scalp_min_edge_bps:g} bps')
                return
        rr_min = self.scalp_rr_min()
        hbp = self.bracket_plan('SCALP', f, harvest=True) if harvest else None
        if mode == 'on' and harvest:
            # the harvester has no fee-true RR >= 1 (TP 0.15 % pays 0.11 %, the stop costs 0.23 %): the guard is the
            # break-even win rate, so a mistyped TP / SL cannot arm a lane that needs an impossible hit rate
            be = hbp.get('be_wr')
            if be is None or be > self.p.micro_harvest_be_wr_max + 1e-12:
                self.scalp_next = self.clock() + self.p.scalp_every_s
                self.funnel.outcome('RR_LOW')
                if self.clock() - getattr(self, '_rr_evt_t', 0.0) >= 60.0:
                    self._rr_evt_t = self.clock()
                    self.event(f'HARVEST break-even win rate {"—" if be is None else f"{be * 100:.0f}%"} > '
                               f'{self.p.micro_harvest_be_wr_max * 100:.0f}% (TP {hbp["tp_pct"]:.3f}% / SL {hbp["sl_pct"]:.3f}%)')
                return
        elif mode == 'on' and self.p.scalp_net_rr and rr_min:
            bp = self.bracket_plan('SCALP', f)
            rr = bp.get('rr_net')
            if rr is None or rr < rr_min - 1e-12:
                self.scalp_next = self.clock() + self.p.scalp_every_s
                self.funnel.outcome('RR_LOW')
                if self.clock() - getattr(self, '_rr_evt_t', 0.0) >= 60.0:
                    self._rr_evt_t = self.clock()
                    self.event(f'ZEPHR NET RR {"—" if rr is None else f"{rr:.2f}"} < {rr_min:g}: SCALP '
                               f'{side} TP {bp["tp_pct"]:.3f}% / SL {bp["sl_pct"]:.3f}% after fees '
                               f'(break-even win rate {(bp.get("be_wr") or 1) * 100:.0f}%)')
                return
        self.scalp_next = self.clock() + self.p.scalp_every_s
        if mode == 'shadow':
            b = self.book()
            touch = b.get('bid') if side == 'BUY' else b.get('ask')
            if harvest and touch:
                # 11-10 (e): a shadow harvest still writes its quote-side markouts (the q_* series; no order, so no fill and no f_*)
                # to data/harvest_markouts.jsonl, flagged `shadow`. swarm_ok = the 'on' lane would have had its VESKA quorum here
                sx = {'macro_soft': 'softened' in why, 'cvd_soft': self.cvd3m_soft(side, f), 'shadow': True,
                      'swarm_ok': bool(d is not None and not d.veto and d.side == side
                                       and d.score >= self.p.scalp_min_score - 1e-9)}
                self.hlog_post(sx, side, touch, f, d)
                self.hlog_close(sx)
            self._shadow_row({'outcome': 'SHADOW_SCALP', 'side': side, 'path': 'SCALP', 'why': why,
                              'touch': touch, 'ker': f.get('ker'),
                              'spread_bps': f.get('spread_bps'), 'ofi': f.get('ofi_10s'),
                              **({'harvest': True, 'tp_pct': hbp['tp_pct'], 'sl_pct': hbp['sl_pct'], 'be_wr': hbp['be_wr'],
                                  'atr_pct': f.get('atr_pct'), 'cvd_soft': self.cvd3m_soft(side, f),
                                  'macro_soft': 'softened' in why} if harvest else {})})
            return
        self.event(why)
        sd = replace(d, side=side, allowed=True, veto=False, reason=why)
        self._harvest_next = harvest                # consumed (and cleared) at the top of _execute
        self._harvest_macro = harvest and 'softened' in why
        await self.execute(side, sd, 'SCALP')

    def scalp_lock(self, outcome: str, why: str) -> None:
        """11-08: an 'on' scalp refused by the macro / CVD 3 m / ALIGN3 lock -> throttle, funnel count, event at most
        once a minute per symbol.  Before any token, like EDGE_LOW and RR_LOW."""
        self.scalp_next = self.clock() + self.p.scalp_every_s
        self.funnel.outcome(outcome)
        if self.clock() - getattr(self, '_lock_evt_t', 0.0) >= 60.0:
            self._lock_evt_t = self.clock()
            self.event(f'SCALP LOCK: {why}')

    def runner_grade(self, path: str, tag: str, d: Optional[Decision]) -> bool:
        """11-10 (e): is this fill managed as a TREND SNIPER runner (Profile.micro_sniper_runner)?  Demo micro AUTO entries on the
        swarm trend / override paths only, raw score and raw consensus both at or above the override floor.  Unknown = no."""
        p = self.p
        if (p.micro_sniper_runner != 'on' or p.mode != 'demo' or tag != 'AUTO' or path not in ('SWARM', 'SWARM_OVERRIDE')
                or d is None or not self.micro_active()):
            return False
        floor, cons = max(inv.SUPERMAJORITY, p.swarm_override_min_score), (self.diag or {}).get('consensus')
        return (isinstance(cons, (int, float)) and math.isfinite(cons) and math.isfinite(d.score)
                and min(cons, d.score) >= floor - 1e-12)

    def swarm_override_eligible(self, d: Decision, votes: list, diag: dict) -> bool:
        """Allow only a demo, KER-locked consensus override above both the policy quorum and its score floor."""
        if (self.p.mode != 'demo' or not self.p.swarm_chop_override or not self.chop_locked
                or d is None or d.side not in ('BUY', 'SELL') or not d.allowed or d.veto):
            return False
        if (diag or {}).get('path') != 'SWARM' or not math.isfinite(d.score):
            return False
        if not math.isfinite(self.p.swarm_override_min_score) or self.p.swarm_override_min_score > 1.0:
            return False
        floor = max(inv.SUPERMAJORITY, self.p.swarm_override_min_score)
        score = (diag or {}).get('consensus')
        if (not isinstance(score, (int, float)) or not math.isfinite(score)
                or score < floor - 1e-12 or d.score < floor - 1e-12
                or d.threshold < inv.SUPERMAJORITY - 1e-12):
            return False
        weights = self.consensus.weights
        if len(votes) != len(weights) or {getattr(v, 'agent', None) for v in votes} != set(weights):
            return False
        if any(getattr(v, 'veto', False) for v in votes):
            return False
        rune = next(v for v in votes if v.agent == 'RUNE')
        return rune.side == d.side and rune.confidence >= inv.VETO - 1e-12

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

    def net_rr_eff(self) -> float:
        """Net-RR floor of an AUTO entry: net_rr_min, or micro_net_rr_min while the micro mode is active (11-09b: the regime
        multipliers shrink TP and SL together while the fees stay fixed, e.g. MR 0.29 / 0.10 x SIDEWAYS 0.75 = 0.217 / 0.075
        is RR 1.15, which a 1.30 floor refused 8 times in a row)."""
        return self.p.micro_net_rr_min if self.micro_active() else self.p.net_rr_min

    def scalp_rr_min(self) -> float:
        """11-08: SCALP net-RR floor = max(net_rr_min, scalp_net_rr_min); 0 = no gate. Micro mode: micro_net_rr_min."""
        if self.micro_active():
            return self.p.micro_net_rr_min or 0.0
        return max(self.p.net_rr_min or 0.0, self.p.scalp_net_rr_min or 0.0)

    def scalp_min_tp(self, sl: float, route: str = 'gtx') -> float:
        """11-08: smallest TP % whose fee-true net RR at stop `sl` clears scalp_rr_min() (same fees as plan_rr;
        gtx: TP pays maker in + maker out 0.04 %, the stop pays maker in + taker out + slip 0.08 %)."""
        p = self.p
        e_in = (p.taker_bps + p.slip_bps) if route == 'ioc' else p.maker_bps
        return self.min_tp_for(sl, route, self.scalp_rr_min())

    def min_tp_for(self, sl: float, route: str, rr_floor: float) -> float:
        """Smallest TP % whose fee-true net RR at stop `sl` reaches rr_floor: (sl + in + taker out + slip) x RR + (in + maker out)."""
        p = self.p
        e_in = (p.taker_bps + p.slip_bps) if route == 'ioc' else p.maker_bps
        return (sl + (e_in + p.taker_bps + p.slip_bps) / 100.0) * rr_floor + (e_in + p.maker_bps) / 100.0

    def super_conviction(self, d: Decision, diag: dict, f: dict) -> bool:
        """Raw swarm vote >= 0.95 (no JEV lift) AND OFI L10 >= 0.80 with the trade, SOL/DOGE only."""
        if not self.p.super_size or self.p.symbol not in SUPER_SIZE or d is None or not d.side:
            return False
        vote = max(d.score, (diag or {}).get('consensus') or 0.0)
        ofi = (f.get('ofi_10s') or 0.0) * (1 if d.side == 'BUY' else -1)
        return vote >= self.p.super_score - 1e-9 and ofi >= self.p.super_ofi

    def conviction_size(self, d: Decision, f: dict, price: float, base: Profile) -> dict:
        """10-08y Dynamic Conviction Sizing -> {'tier', 'frac', 'qty', 'notional', 'clip'} (qty None = no tier).
        Notional = tier fraction x wallet; clipped to the free portfolio risk at the stop and the margin cap."""
        p, sym = self.p, self.p.symbol
        out = {'tier': None, 'frac': None, 'qty': None, 'notional': None, 'clip': None, 'regime': None}
        if not self.is_alt() or not price or not self.spec:
            return out
        score = max(d.score, (self.diag or {}).get('consensus') or 0.0)
        ofi = (f.get('ofi_10s') or 0.0) * (1 if d.side == 'BUY' else -1)
        for thr, frac in p.cs_tiers:
            if score >= thr - 1e-9 and (frac < max(x[1] for x in p.cs_tiers) or ofi >= p.cs_super_ofi):
                out.update(tier=f'score>={thr:g}' + (f' & OFI>={p.cs_super_ofi:g}'
                                                    if frac == max(x[1] for x in p.cs_tiers) else ''), frac=frac)
                break
        if out['frac'] is None:
            return out
        wallet = self.account.get('wallet') or self.account.get('available') or 0.0
        notional = out['frac'] * wallet
        br = self.cs_breaker()
        if br is not None:
            out.update(clip=br, qty=None)                          # breaker: keep the normal size
            return out
        risk_pct = (base.sl_pct + (p.maker_bps + p.taker_bps + p.slip_bps) / 100.0) / 100.0
        free_risk = max(0.0, self.risk_cap() - sum(o.risk_at_stop() for o in self.open_peers()))
        free_margin = max(0.0, p.portfolio_margin_frac * wallet - sum(o.margin_used() for o in self.open_peers()))
        f_ = self.features or {}
        k_ = f_.get('ker')
        aligned = bool(self.flow.get('buy_ok' if d.side == 'BUY' else 'sell_ok'))
        if k_ is None or k_ < p.cs_chop_ker or not aligned:
            reg_cap, reg = p.cs_chop_notional, 'regime chop'
        elif k_ >= p.cs_trend_ker:
            reg_cap, reg = p.cs_trend_notional, 'regime trend'
        else:
            reg_cap, reg = p.cs_mid_notional, 'regime mid'
        out['regime'] = reg
        lims = {'risk cap': free_risk / risk_pct if risk_pct > 0 else notional,
                'margin cap': free_margin * p.leverage,
                reg: reg_cap,
                'notional ceiling': p.cs_max_notional if p.cs_max_notional else notional}
        for why, lim in lims.items():
            if lim < notional - 1e-9:
                notional, out['clip'] = lim, why
        q = floor_step(notional / price, self.spec['step'])
        out.update(notional=round(q * price, 4), qty=q if q >= self.min_qty(price) - 1e-12 else None)
        return out

    def cs_breaker(self) -> Optional[str]:
        """10-08z: net PnL of the last cs_breaker_window conviction-sized trades on this coin <= -cs_breaker_usdt
        -> 'breaker ...' (conviction sizing pauses), else None."""
        rows = [r for r in self.all_rows if r.get('symbol', self.p.symbol) == self.p.symbol
                and (r.get('conviction') or {}).get('qty') and r.get('pnl') is not None]
        rows = rows[-self.p.cs_breaker_window:]
        net = sum(float(r['pnl']) for r in rows)
        if rows and net <= -self.p.cs_breaker_usdt + 1e-12:
            return f'breaker {net:+.2f} U over {len(rows)} conviction trades'
        return None

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
        cap_m = self.p.portfolio_margin_frac * wallet if wallet else 0.0
        if wallet and self.micro_active():
            # 11-09: 20 % of a 5 U wallet is 1.0 U, below the 1.02-1.2 U margin of the smallest legal lot at 5x, so every
            # micro entry (and every entry of the shipped live profile: 0.77 U on 3.85 U) was refused. One position
            # (micro_max_positions) may use the margin of the largest lot of the notional window.
            # 11-09f: n slots = n lots of the window, and never more than micro_margin_frac of the (rehearsed) equity -
            # the 5 000 U Demo wallet must not be the limit of a 5 U bankroll rehearsal
            lot_m, eq = self.micro_window_hi() / self.p.leverage, self.micro_equity()
            cap_m = max(lot_m, min(self.max_positions_eff() * lot_m, self.p.micro_margin_frac * eq if eq else math.inf))
            if margin > cap_m + 1e-9:
                return f'PORTFOLIO MARGIN {margin:.2f} U > {cap_m:.2f} U (micro: {self.max_positions_eff()} lot(s) of the window)'
            return None
        if wallet and margin > cap_m + 1e-9:
            return f'PORTFOLIO MARGIN {margin:.2f} U > {self.p.portfolio_margin_frac * 100:.0f}% ví'
        return None

    def portfolio_view(self) -> dict:
        rows = [r for r in (o.position_row() for o in self.risk.engines) if r]
        return {'multi_slot': self.p.multi_slot, 'rows': rows, 'count': len(rows), 'max': self.max_positions_eff(),
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
        if self.risk.roll(datetime.now().strftime('%Y-%m-%d')):
            self.event('new day -> daily loss meter reset')
        if self.halted:
            r = (False, f'HALT: {self.halted}')
        elif self.day_pnl <= -self.daily_kill_eff():
            r = (False, f'daily loss {self.day_pnl:+.4f} <= -{self.daily_kill_eff():g} U')
        elif self.pos or abs(self.view.get('amt') or 0) > 0:
            r = (False, 'position open' if self.pos else 'EXTERNAL position on venue')
        elif self.busy:
            r = (False, 'order in flight')
        elif (why := self.slot_block()) is not None:
            r = (False, why)
        elif (halt := self.micro_halt_until()) is not None:
            r = (False, f'MICRO HALT: {self.p.max_consec_losses} losses in a row · {halt - now:.0f}s left')
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
        self.hlog_step(f)                             # 11-10 Phase 1: markout samples of the open harvest orders
        self.edge_shadow_step(f)                      # 11-09: the virtual NO EDGE probe moves on every tick
        if self.regime_clf is not None and f.get('price'):
            self.regime_state = self.regime_clf.update(self.clock(), float(f['price']))
        if self.pos and self.p.regime_crash_flatten and self.regime_crash() and not self.trend_owned:
            self.event(f'REGIME CRASH -> flatten {self.p.symbol}: {self.regime_state.reason}')
            await self.close('REGIME-CRASH')
        if self.pos:                                  # MARIN reacts on every WS tick, not every REST poll
            await self.manage()
        elif self.autonomy is not None and not self.busy:
            self.autonomy.refresh(self, f)            # 10-09 (cc): effective Profile while flat (never mid-trade)
        self.chop_locked = msig.chop_locked(f.get('ker'), self.chop_locked, self.sig_cfg)
        self.absorb = msig.absorption(f, self.sig_cfg)
        self.flow = msig.concordance(f, self.sig_cfg)
        self.tox = msig.toxicity(f, self.sig_cfg)
        if self.tox['toxic'] and self.p.vpin_gate:
            self.mark_toxic(self.tox['vpin'], self.tox.get('side'))
        self.macro = msig.macro_bias(f, self.sig_cfg)
        self.reg = msig.regime(f, self.sig_cfg)
        self.sig = msig.signals(f, self.sig_cfg, self.reg)
        g_now = self.gates(f)
        d, votes, diag = swarm.evaluate(self.sig, g_now, self.consensus, self.p.jev_min,
                                        self.p.min_confluence, self.p.edge_ratio_min, allow=self.reg['allow'],
                                        collinear_penalty=self.p.collinear_penalty)
        if self.autonomy is not None and d.allowed:   # 10-09 (cc): learned weights may only REMOVE a trade
            d = self.autonomy.demote(self, d, lambda sg: swarm.evaluate(sg, g_now, self.consensus, self.p.jev_min,
                                                                         self.p.min_confluence, self.p.edge_ratio_min,
                                                                         allow=self.reg['allow'],
                                                                         collinear_penalty=self.p.collinear_penalty)[0],
                                     swarm.ALPHA)
        swarm_ok = d.allowed
        scalp_only_block = bool(d.allowed and self.p.scalp_only and not self.observer)
        if scalp_only_block:
            d = replace(d, allowed=False, reason=f'ROUTER CHOP: scalp lane only · would be {d.side}')
        self.drift_probe = None
        self.blockers = []
        swarm_override = self.swarm_override_eligible(d, votes, diag)
        if d.allowed and not self.busy and not self.pos:
            ok, why = self.churn_gate(d, diag, swarm_override=swarm_override)
            if not ok:
                d = replace(d, allowed=False, reason=why)
        self.candidate = None
        stage = 'SCALP_ONLY' if scalp_only_block else None
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
            path = ('SWARM_OVERRIDE' if swarm_override else
                    'MR' if self.lane(d) == 'MR' else (diag.get('path') or 'SWARM'))
            await self.execute(d.side, d, path)
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
        p, gate = self.p, self.net_rr_eff()
        out = {}

        def row(state, rr_lo, rr_hi, why, g=None):
            g = gate if g is None else g                          # 11-08: SCALP passes its own floor
            feas = xf.rr_feasible(rr_hi, g) if state in ('ENABLED', 'SHADOW') else None
            return {'state': state, 'rr_min': None if rr_lo is None else round(rr_lo, 3),
                    'rr_max': None if rr_hi is None else round(rr_hi, 3), 'gate': g, 'feasible': feas,
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
                            f'TP {p.mr_tp_fixed} % / SL {p.mr_sl_fixed} % (fixed) + fees' if p.mr_bracket == 'fixed'
                            else f'TP clamped [{p.mr_tp_min}, {p.mr_tp_max}] %, SL {p.mr_sl_pct} % + fees')
        bs = self.bracket_plan('SCALP', {})                         # 11-08: effective bracket (SL floor, TP lift)
        sc, sg = bs.get('rr_net'), self.scalp_rr_min()
        st = {'on': 'ENABLED', 'shadow': 'SHADOW'}.get(p.chop_scalper, 'OFF')
        if p.scalp_net_rr and sg:
            out['SCALP'] = row(st, sc, sc, f'chop_scalper={p.chop_scalper}; TP {bs["tp_pct"]:g} % / SL '
                                           f'{bs["sl_pct"]:g} % + fees vs net RR {sg:g} (break-even win rate '
                                           f'{1 / (1 + sc) * 100 if sc else 100:.0f}%), replay holdout < 0 bps', sg)
        else:
            out['SCALP'] = {**row(st, sc, sc, f'chop_scalper={p.chop_scalper}; exempt from net RR (break-even win rate '
                                              f'{1 / (1 + sc) * 100 if sc else 100:.0f}%), replay holdout < 0 bps'),
                            'gate': None, 'feasible': True if st == 'ENABLED' else None}
        ov = self.bracket_plan('SWARM_OVERRIDE', {}).get('rr_net')
        ov_state = 'ENABLED' if p.mode == 'demo' and p.swarm_chop_override else 'OFF'
        out['SWARM_OVERRIDE'] = row(ov_state, ov, ov,
                                    f'demo-only; raw score >= {max(inv.SUPERMAJORITY, p.swarm_override_min_score):g}; '
                                    f'net RR {ov:.2f}' if ov is not None else 'override bracket unavailable')
        if self.observer:
            for k in out:
                out[k] = {**out[k], 'state': 'OBSERVER', 'feasible': None,
                          'why': f'{p.symbol} observe_only (config) · ' + out[k]['why']}
        return out

    def quality(self, d: Decision, diag: dict) -> float:
        """Slot priority: conviction, +0.05 when the regime trend agrees, +0.05 when all three flows agree."""
        if self.micro_active():
            return self.micro_quality(d, diag)
        q = self.conviction(d, diag)
        if (self.reg or {}).get('name') == ('UP' if d.side == 'BUY' else 'DOWN'):
            q += 0.05
        if self.flow.get('buy_ok' if d.side == 'BUY' else 'sell_ok'):
            q += 0.05
        return q

    def better_peer(self, q: float, max_age: float = 1.0) -> Optional[tuple]:
        """A peer whose fresh candidate beats ours (ties: SCAN_SYMBOLS order) -> (engine, quality)."""
        micro = self.micro_active()                              # 11-09: one position in total -> the best coin takes it
        if not self.p.slot_yield or (self.p.multi_slot and not micro):   # one slot per symbol: nobody has to yield
            return None
        now, ahead_all = self.clock(), []
        order = {s: i for i, s in enumerate(scan_universe(True, self.p.micro_symbols))}
        for o in self.risk.engines:
            c = o.candidate
            if o is self or c is None or now - c[0] > max_age or o.pos or o.busy:
                continue
            ahead = c[1] > q + 1e-9 or (abs(c[1] - q) <= 1e-9 and order.get(o.p.symbol, 9) < order.get(self.p.symbol, 9))
            if ahead:
                ahead_all.append((o, c[1]))
        # 11-09f: with n micro slots a coin yields only when the better-ranked candidates fill every FREE slot
        free = max(1, self.max_positions_eff() - len(self.open_peers())) if micro else 1
        if len(ahead_all) < free:
            return None
        return max(ahead_all, key=lambda x: x[1])

    def lane(self, d: Decision) -> str:
        """'MR' when the decision is the mean-reversion setup (MR signal on the decision side), else 'TREND'."""
        mr = (self.sig or {}).get('MR')
        return 'MR' if mr and mr['side'] == d.side else 'TREND'

    def mr_profile(self, f: Optional[dict] = None) -> Profile:
        return self.bracket_plan('MR', f)['profile']

    def bracket_plan(self, path: str = 'SWARM', f: Optional[dict] = None, route: str = 'gtx', harvest: bool = False) -> dict:
        """TP / SL / trail for the next entry + gross and fee-true reward:risk.
        MR: TP = |VWAP dev| clamped [mr_tp_min, mr_tp_max], SL mr_sl_pct, whole size.
        TREND with ATR known: SL = max(floor, 1.0 ATR), TP1 = max(2.5 ATR, 2.5 SL), trail 1.2 ATR after TP1.
        Else the static profile. rr_net counts maker/taker fees and slip: win = TP - in - maker out,
        loss = SL + in + taker out + slip; be_wr = loss / (win + loss)."""
        f = self.features if f is None else f
        f, p = f or {}, self.p
        atr_pct, trail = f.get('atr_pct'), None
        if path == 'SWARM_OVERRIDE':
            tp = p.swarm_override_tp_pct
            sl = min(p.swarm_override_sl_pct, p.swarm_override_sl_max_pct)
            src = 'SWARM_OVERRIDE'
            prof = replace(p, tp_pct=tp, sl_pct=sl, tp1_frac=1.0)
        elif path == 'SCALP' and harvest:                               # 11-09e chop harvester: fixed small bracket, no RR lift
            tp, sl, src = min(0.18, max(0.12, p.micro_harvest_tp_pct)), p.micro_harvest_sl_pct, 'HARVEST'   # TP guard 0.12-0.18 %
            prof = replace(p, tp_pct=tp, tp1_frac=1.0, **({'sl_pct': sl} if p.max_loss_usdt is None else {}))
        elif path == 'SCALP':
            tp, sl, src = p.scalp_tp_pct, max(p.scalp_sl_pct, p.scalp_sl_min_pct), 'SCALP'
            a15 = f.get('atr15_pct')
            if p.scalp_tp_mode == 'atr' and a15:                        # 11-09: reachable TP = mult x ATR 15 m, clamped
                tp = min(p.scalp_tp_max_pct, max(p.scalp_tp_min_pct, p.scalp_tp_atr_mult * a15))
            if p.scalp_net_rr and p.scalp_net_rr_min:                   # 11-08: TP lifted to clear net RR 1.35
                tp = max(tp, self.scalp_min_tp(sl, route))
            prof = replace(p, tp_pct=tp, tp1_frac=1.0, **({'sl_pct': sl} if p.max_loss_usdt is None else {}))
        elif path == 'MR':
            dev = abs(f.get('vwap_dev_pct') or 0.0) or p.mr_tp_pct
            if p.mr_bracket == 'fixed':                         # 10-08x: fee-true RR >= 1.35 bracket
                tp, sl, src = p.mr_tp_fixed, p.mr_sl_fixed, 'MR-FIXED'
            else:
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
        if p.max_loss_usdt is None and p.micro_bracket and self.micro_active() and src != 'HARVEST':   # 11-09d noise floor (live derives SL from the loss budget)
            sl2, tp2 = self.micro_bracket(sl, tp, f, route, scalp=path == 'SCALP')
            if (sl2, tp2) != (sl, tp):
                sl, tp = sl2, tp2
                prof = replace(prof, sl_pct=sl, tp_pct=tp)
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
        return self.kelly_of([float(r['pnl']) for r in self.all_rows
                              if r.get('symbol', self.p.symbol) == self.p.symbol and r.get('pnl') is not None])

    def kelly_of(self, rows: list) -> dict:
        """The Kelly statistics of a list of net results (real ledger rows, or the NO EDGE shadow path's virtual ones)."""
        window = self.p.kelly_window or None
        if window:
            rows = rows[-window:]
        n, wins, losses = len(rows), [x for x in rows if x > 0], [-x for x in rows if x <= 0]
        out = {'n': n, 'min_n': self.p.kelly_min_trades, 'lambda': self.p.kelly_lambda, 'p': None, 'b': None,
               'f_star': None, 'f': None, 'state': 'SAMPLE', 'window': window}
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

    def entry_edge_block(self, path: str, tag: str) -> Optional[str]:
        """11-09: reason an AUTO entry must not be sent at all, else None.
        1. Kelly state NO EDGE (f* <= 0 on the settled window): the old fallback traded the minimum size on negative
           expectancy. SAMPLE (< kelly_min_trades) is UNKNOWN, not zero edge: it stays open so the window fills.
           The block lifts only when the SHADOW path (virtual trades, edge_shadow_* in Profile) shows Kelly EDGE.
        2. JEV path whose RAW swarm score is under the 0.768 quorum (score padded by jev/10): refused; an unknown raw
           score on the JEV path cannot be shown to clear the quorum, so it is refused too."""
        if tag != 'AUTO':
            return None
        p = self.p
        if p.kelly and p.kelly_no_edge_gate and p.max_loss_usdt is None:
            k = self.kelly()
            fmt3 = lambda v, spec: '—' if v is None else format(v, spec)
            if k['state'] == 'NO EDGE' or (k.get('f_star') is not None and k['f_star'] <= 0):
                st, sh = self.edge_shadow, (self.edge_shadow_view() if p.edge_shadow else None)
                if sh is not None and sh['state'] == 'EDGE':
                    if not st['released']:
                        st['released'] = True
                        self.event(f'KELLY NO EDGE lifted by the shadow path: {sh["n"]} virtual trades, f* '
                                   f'{fmt3(sh["f_star"], "+.3f")} (real f* {fmt3(k.get("f_star"), "+.3f")}) -> '
                                   f'entries at the minimum size until the real window recovers')
                else:
                    if st['released']:
                        st['released'] = False
                        self.event(f'KELLY NO EDGE back: shadow f* {fmt3(sh and sh["f_star"], "+.3f")} over '
                                   f'{sh["n"] if sh else 0} virtual trades')
                    more = '' if sh is None else (f'; shadow path {sh["n"]}/{sh["min_n"]} virtual trades, f* '
                                                  f'{fmt3(sh["f_star"], "+.3f")}')
                    return (f'KELLY NO EDGE f* {fmt3(k.get("f_star"), "+.3f")} over {k["n"]} trades '
                            f'(p {fmt3(k.get("p"), ".2f")}, b {fmt3(k.get("b"), ".2f")}) -> no entry, no minimum-size '
                            f'fallback{more}')
            else:
                self.edge_shadow['released'] = False
        if p.raw_quorum_gate and jev_lift(path, (self.diag or {}).get('consensus')) is not False:
            raw = (self.diag or {}).get('consensus')
            return (f'RAW QUORUM {"—" if raw is None else f"{raw * 100:.1f}%"} < {inv.SUPERMAJORITY * 100:.1f}% '
                    f'(JEV lift refused)')
        return None

    # ------------------------------------------------------------------ NO EDGE shadow path (11-09)
    def _edge_shadow_load(self) -> list:
        """Settled virtual results of this symbol from engine_orders.jsonl (a restart keeps the evidence)."""
        out = []
        try:
            with open(self.ledger.parent / 'engine_orders.jsonl', encoding='utf-8') as fh:
                for line in fh:
                    if 'SHADOW_EDGE_CLOSE' not in line:
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if r.get('symbol') == self.p.symbol and r.get('net_pct') is not None:
                        out.append(float(r['net_pct']))
        except OSError:
            pass
        return out[-200:]

    def edge_shadow_rows(self) -> list:
        st = self.edge_shadow
        if st['pnls'] is None:
            st['pnls'] = self._edge_shadow_load()
        return st['pnls']

    def edge_shadow_view(self) -> dict:
        """Kelly statistics of the virtual trades + the open one, for the gate and the snapshot."""
        v = self.edge_shadow['pos']
        return {**self.kelly_of(self.edge_shadow_rows()),
                'open': None if v is None else {k: v[k] for k in ('side', 'state', 'quote', 'tp_pct', 'sl_pct')}}

    def edge_shadow_arm(self, side: str, path: str) -> None:
        """A NO EDGE refusal opens ONE virtual trade, never an order: the maker quote the real entry would have joined
        and the same TP / SL bracket. It fills only when the tape trades THROUGH the quote (queue not assumed won),
        exits at TP only when the tape trades through it, at the stop with slip, or at the touch after the hold."""
        st, p, f = self.edge_shadow, self.p, self.features or {}
        if not p.edge_shadow or st['pos'] is not None or self.clock() < st['next'] or not self.spec:
            return
        px = self.gtx_price(side, f)
        bp = self.bracket_plan(path, f) if px else None
        if not bp or not bp.get('tp_pct') or not bp.get('sl_pct'):
            return
        st['next'] = self.clock() + p.edge_shadow_every_s
        st['pos'] = {'side': side, 'path': path, 'quote': px, 't': self.clock(), 'state': 'PENDING',
                     'tp_pct': bp['tp_pct'], 'sl_pct': bp['sl_pct'], 'mfe': 0.0}
        self._shadow_row({'outcome': 'SHADOW_EDGE_OPEN', 'side': side, 'path': path, 'quote': px,
                          'tp_pct': bp['tp_pct'], 'sl_pct': bp['sl_pct'], 'why': 'KELLY NO EDGE'})

    def edge_shadow_step(self, f: dict) -> None:
        """Advance the virtual trade on this tick (cheap when none is open)."""
        st, p = self.edge_shadow, self.p
        v = st['pos']
        px = f.get('price')
        if v is None or not px:
            return
        now, q, d = self.clock(), v['quote'], (1 if v['side'] == 'BUY' else -1)
        tick = self.spec['tick'] if self.spec else 0.0
        if v['state'] == 'PENDING':
            if d * (px - q) <= -tick - 1e-12:                    # BUY: tape traded below our bid; SELL: above our ask
                v.update(state='OPEN', t_fill=now)
            elif now - v['t'] >= p.edge_shadow_ttl_s:
                st['pos'] = None
                self._shadow_row({'outcome': 'SHADOW_EDGE_NOFILL', 'side': v['side'], 'path': v['path'], 'quote': q})
            return
        fee_rt = 2 * p.maker_bps / 100.0                         # maker in + maker out (TP / time exit)
        tp_px, sl_px = q * (1 + d * v['tp_pct'] / 100.0), q * (1 - d * v['sl_pct'] / 100.0)
        v['mfe'] = max(v['mfe'], d * (px / q - 1) * 100.0)
        if d * (px - tp_px) >= tick - 1e-12:
            why, net = 'TP', v['tp_pct'] - fee_rt
        elif d * (px - sl_px) <= 0:                              # stop: taker + slip, at the print when it gapped past
            exit_px = px if d * (px - sl_px) < 0 else sl_px
            why = 'SL'
            net = d * (exit_px / q - 1) * 100.0 - (p.maker_bps + p.taker_bps + p.slip_bps) / 100.0
        elif now - v['t_fill'] >= p.edge_shadow_hold_s:
            exit_px = (f.get('bid') if d > 0 else f.get('ask')) or px
            why, net = 'TIME', d * (exit_px / q - 1) * 100.0 - fee_rt
        else:
            return
        rows = self.edge_shadow_rows()
        rows.append(round(net, 5))
        del rows[:-200]
        st['pos'] = None
        k = self.kelly_of(rows)
        self._shadow_row({'outcome': 'SHADOW_EDGE_CLOSE', 'side': v['side'], 'path': v['path'], 'quote': q,
                          'why': why, 'net_pct': round(net, 5), 'mfe_pct': round(v['mfe'], 4),
                          'hold_s': round(now - v['t_fill'], 1), 'shadow_n': k['n'], 'shadow_f_star': k['f_star']})
        fs = k['f_star']
        self.event(f'SHADOW-EDGE {v["side"]} {why} {net:+.3f}% net · {k["n"]} virtual trades, {k["state"]}'
                   + ('' if fs is None else f' f* {fs:+.3f}'))

    def tox_check(self, side: str) -> tuple:
        """Return the passive quote action from tape and own-fill markouts."""
        markouts = getattr(self.feed, 'markouts', None)
        result = tg.decide(side, self.feed.vpin_now(), markouts.view(side, 5.0) if markouts else None,
                           list(self.own_mk5), self.tox_policy)
        self.tox_last = (side, *result)
        return result

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

    def regime_crash(self) -> bool:
        return (self.p.regime_gate == 'on' and self.regime_state is not None
                and self.regime_state.state == rc.CRASH)

    def micro_jumpy_scalp(self) -> Optional[str]:
        """JUMPY + SCALP: None (allowed) in micro mode while the spread is under micro_jumpy_spread_bps and VPIN is known and
        under micro_jumpy_vpin; otherwise the freeze reason. Unknown spread / VPIN cannot show the condition, so they freeze.
        The JUMPY policy still applies its stop x1.5; its size x0.3 cannot go under the venue lot (see _execute)."""
        p, frozen = self.p, 'JUMPY: micro-scalping frozen'
        if not (p.micro_jumpy_scalp and self.micro_active()):
            return frozen
        sp, v = (self.features or {}).get('spread_bps'), self.feed.vpin_now()
        if sp is None or sp >= p.micro_jumpy_spread_bps:
            return f'{frozen} (spread {"—" if sp is None else f"{sp:.1f}"} bps, needs < {p.micro_jumpy_spread_bps:g})'
        if v is None or v >= p.micro_jumpy_vpin:
            return f'{frozen} (VPIN {"—" if v is None else f"{v:.2f}"}, needs known and < {p.micro_jumpy_vpin:g})'
        return None

    def regime_check(self, path: str, tag: str, decision: Optional[Decision] = None) -> tuple:
        """Return (refusal, policy) for AUTO entries; shadow logs the gate without blocking or resizing."""
        if tag != 'AUTO' or self.p.regime_gate not in ('shadow', 'on'):
            return None, None
        state = self.regime_state
        st = state.state if state is not None else rc.UNKNOWN
        reason = state.reason if state is not None else 'no classifier update yet'
        if st not in rc.STATES:
            reason, st = f'INVALID_INPUT: unknown regime state {st!r}', rc.UNKNOWN
        pol = rc.policy_for(st)
        regime_label = st
        if st == rc.CRASH:
            why = f'{st}: no directional entries ({reason})'
        elif path == 'SWARM_OVERRIDE':
            # Known non-crash regimes allow the override past style compatibility. During a clean cold-start
            # WARMUP only, require the same full demo quorum / RUNE agreement checks used by the override router.
            if st != rc.UNKNOWN:
                why = None
            elif (self.p.mode == 'demo' and state is not None and reason == 'WARMUP'
                  and self.swarm_override_eligible(decision or self.decision, self.votes, self.diag or {})):
                why, pol = None, None                # UNKNOWN's zero-size policy must not erase this qualified probe
                regime_label = 'WARMUP_OVERRIDE'
            else:
                why = f'{st}: no directional entries ({reason})'
        elif st == rc.UNKNOWN:
            why = f'{st}: no directional entries ({reason})'
        elif st == rc.JUMPY and path == 'SCALP':
            why = self.micro_jumpy_scalp()
            if why is None:
                regime_label = 'JUMPY·MICRO-SCALP'
        else:
            style = rc.MEAN_REVERSION if path in ('MR', 'SCALP') else rc.TREND
            why = None if style in pol.styles else f'{st}: {style} entries off'
        if getattr(self, '_xm', None) is not None:
            self._xm['regime_gate'] = regime_label if why is None else f'{st}: refuse'
        if self.p.regime_gate == 'shadow':
            if why and self.clock() - self._regime_evt_t >= 60.0:
                self._regime_evt_t = self.clock()
                self.event(f'(shadow) REGIME GATE would refuse {path}: {why}')
            return None, None
        return why, pol

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
        elif self.p.scalp_only:
            label = 'SCALP ONLY (router CHOP)'
        elif self.p.vfr_gate and vv['frozen'] and vv['vfr'] is not None:
            label = f'FROZEN VFR {vv["vfr"]:.1f}'
        elif self.drift_exempt(f):
            label = 'CHOP HARVEST'        # 11-10: the cockpit used to print DRIFT LOCK here, a label the chop lane never obeyed
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

    def churn_checks(self, d: Decision, diag: dict, swarm_override: bool = False) -> tuple:
        """Every evaluable churn gate for decision `d` (no early return) -> ([(gate, why), ...], drift_probe).
        Order = the hot-path priority; the first entry is what churn_gate returns.  Read-only (10-08v funnel)."""
        score, edge = self.conviction(d, diag), diag.get('edge_bps') or 0.0
        lane = self.lane(d)
        reg = self.reg or {}
        out, probe = [], None
        if self.toxic_blocks(d.side):
            out.append(('VPIN', f'VPIN TOXIC {(self.toxic_vpin or 0):.2f} {self.toxic_side} flow · cấm {d.side} '
                                f'{self.toxic_until - self.clock():.0f}s'))
        sh = self.cvd_shield(d.side)
        if sh:
            out.append(('CVD_SHIELD', sh))
        al = self.align3_check(d.side)
        self.align3_last = al
        if al and self.p.align3 == 'on':
            out.append(('ALIGN3', al))
        out.extend(self.micro_entry_block(d.side, self.features or {}))      # 11-09d (empty outside micro mode)
        if self.p.vfr_gate:
            vv = self.vfr_view()
            if vv['frozen']:
                out.append(('VFR', vv['why']))
        if self.p.drift_lock and reg.get('name') == 'DRIFT' and not self.drift_exempt():
            acc = self.drift_acceleration(d.side)
            if acc:
                probe = acc
            else:
                out.append(('DRIFT', f'DRIFT LOCK: {reg.get("label", "DRIFT")} · {reg.get("why", "")}'))
        self.ker_adapt = None
        if lane == 'TREND' and self.p.ker_gate and self.chop_locked and not (
                swarm_override and self.p.mode == 'demo' and self.p.swarm_chop_override):
            ad = self.adaptive_ker(d, diag)
            if ad['unlock'] and self.p.adaptive_ker == 'on':
                self.ker_adapt = ad                               # 10-08x: lock lowered for super-conviction
            else:
                out.append(('KER', self.chop_reason() + (f' · adaptive KER {ad["ker_eff"]:.3f} ({self.p.adaptive_ker})'
                                                         if ad['unlock'] else '')))
                if ad['unlock'] and self.p.adaptive_ker == 'shadow':
                    self.ker_adapt = ad
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
        rr_floor = self.net_rr_eff()
        if rr_floor and self.p.max_loss_usdt is None:               # live (loss-budget SL): checked in execute()
            bp = self.bracket_plan('MR' if lane == 'MR' else 'SWARM', self.features)
            rr = bp.get('rr_net')
            if rr is None or rr < rr_floor:
                out.append(('NET_RR', f'NET RR {"—" if rr is None else f"{rr:.2f}"} < {rr_floor:g} '
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
        if score < self.p.min_score and not (swarm_override and score >= max(
                inv.SUPERMAJORITY, self.p.swarm_override_min_score) - 1e-12):
            out.append(('SCORE', f'CHURN score {score * 100:.1f}% < {self.p.min_score * 100:.1f}%'))
        if edge < self.p.min_edge_bps:
            out.append(('EDGE', f'CHURN edge {edge:.1f} bps < {self.p.min_edge_bps:g} bps '
                                f'(phí maker 2 chiều {2 * self.p.maker_bps:g} bps)'))
        return out, probe

    def early_invalid_due(self, p: dict, now: float, f: dict, runner: bool = False) -> Optional[str]:
        """10-08z2: within early_inval_s of the fill, CVD velocity 10 s >= early_inval_vel_usd against the position
        or OFI L10 flipped to <= -early_inval_ofi -> reason, else None.
        11-10 (g) runner=True: the ARMED check of a runner position (OFI limit micro_runner_inval_ofi, 0 = off), whatever
        the profile-wide mode says."""
        lim = self.p.micro_runner_inval_ofi if runner else self.p.early_inval_ofi
        if runner:
            if not p.get('runner') or not lim:
                return None
        elif self.p.early_invalidation not in ('shadow', 'on') or p.get('tag') != 'AUTO' or p.get('path') == 'SCALP':
            return None
        if now - p['opened'] > self.p.early_inval_s:
            return None
        d = 1 if p['side'] == 'BUY' else -1
        vel = (f.get('cvd_vel_10s') or 0.0) * d
        if vel <= -self.p.early_inval_vel_usd:
            return f'CVD {vel / 1e3:+.0f}k/s against'
        ofi = f.get('ofi_10s')
        if ofi is not None and ofi * d <= -lim:
            return f'OFI flipped {ofi:+.2f}'
        return None

    def cvd_shield(self, side: str) -> Optional[str]:
        """10-08z2 hard macro CVD shield -> reason when `side` fights the macro flow, else None."""
        if self.p.cvd_shield != 'on' or side not in ('BUY', 'SELL'):
            return None
        f = self.features or {}
        c60, c3 = f.get('cvd_60s'), f.get('cvd_3m')
        sg = 1 if side == 'BUY' else -1
        bad = [(n, v, lim) for n, v, lim in (('CVD60s', c60, self.p.cvd_shield_60s_usd),
                                             ('CVD3m', c3, self.p.cvd_shield_3m_usd))
               if v is not None and sg * v < -lim]
        if not bad:
            return None
        return f'CVD SHIELD no {side}: ' + ' · '.join(f'{n} {v / 1e3:+.0f}k vs {"-" if sg > 0 else "+"}{lim / 1e3:.0f}k'
                                                       for n, v, lim in bad)

    def align3_check(self, side: str, force: bool = False, f: Optional[dict] = None) -> Optional[str]:
        """10-08z2 three layers with the trade: CVD 3 m and CVD 60 s (macro), CVD 15 s and OFI L10 (micro), OBI
        (book).  -> reason of the first layer that disagrees, else None.  A missing feature counts as disagreement.
        11-08: force=True (the 'on' scalp lock) checks even when align3 is 'off'/'shadow'; f = that tick's features."""
        if (not force and self.p.align3 not in ('shadow', 'on')) or side not in ('BUY', 'SELL'):
            return None
        f, sg = (self.features if f is None else f) or {}, (1 if side == 'BUY' else -1)
        for name in ('cvd_3m', 'cvd_60s', 'cvd_15s', 'ofi_10s', 'obi'):
            v = f.get(name)
            if v is None or sg * v <= 0:
                if name == 'cvd_3m' and v is not None and self.cvd3m_soft(side, f) is not None:
                    continue                  # 11-09e (micro): the lagging 3 m layer is a size penalty when OFI + 1 m move agree
                return f'ALIGN3 {name} {"—" if v is None else f"{v:+.3g}"} not with {side}'
        return None

    def adaptive_ker(self, d: Decision, diag: dict) -> dict:
        """10-08x form formula: ker_eff = max(floor, ker_lock - slope x (score - 0.90)) for score >= 0.95, CVD
        velocity 10 s >= 3 000 USD/s with the trade and confluence >= 3/3.  -> {'unlock', 'ker_eff', 'why', ...}."""
        p, f, c = self.p, self.features or {}, self.sig_cfg
        score = self.conviction(d, diag)
        k = f.get('ker')
        vel = (f.get('cvd_vel_10s') or 0.0) * (1 if d.side == 'BUY' else -1)
        conf = ((diag or {}).get('confluence') or {}).get('count') or 0
        ker_eff = max(p.adaptive_ker_floor, c.ker_lock - p.adaptive_ker_slope * (score - 0.90))
        out = {'unlock': False, 'ker_eff': ker_eff, 'ker': k, 'score': score, 'cvd_vel': vel, 'confluence': conf}
        if p.adaptive_ker not in ('shadow', 'on') or k is None:
            return out
        if score >= p.adaptive_ker_score - 1e-9 and vel >= p.adaptive_ker_cvd_vel_usd and \
                conf >= p.adaptive_ker_confluence and k >= ker_eff:
            out.update(unlock=True, why=f'ADAPTIVE KER {k:.2f} >= {ker_eff:.3f} (score {score * 100:.1f}% · CVD '
                                         f'{vel / 1e3:+.1f}k/s · confluence {conf}/3)')
        return out

    def churn_gate(self, d: Decision, diag: dict, swarm_override: bool = False) -> tuple:
        """Anti-churn (2026-10-07f): auto entries need score >= min_score AND expected edge >= min_edge_bps.
        10-08v: every blocker is evaluated (self.blockers); the first one is returned, as before."""
        override = (swarm_override and self.p.mode == 'demo' and self.p.swarm_chop_override)
        out, probe = self.churn_checks(d, diag, swarm_override=override)
        self.blockers = [k for k, _ in out]
        ad = self.ker_adapt
        if ad and self.p.adaptive_ker == 'shadow' and self.clock() >= getattr(self, 'ker_shadow_next', 0.0):
            self.ker_shadow_next = self.clock() + 20.0         # one row per 20 s per symbol
            self._shadow_row({'outcome': 'SHADOW_KER', 'side': d.side, 'path': 'TREND', 'why': ad.get('why'),
                              'ker': ad['ker'], 'ker_eff': round(ad['ker_eff'], 4), 'score': ad['score'],
                              'cvd_vel': ad['cvd_vel'], 'confluence': ad['confluence'],
                              'other_blockers': [k for k in self.blockers if k != 'KER'],
                              'px': (self.features or {}).get('price')})
        if ad and self.p.adaptive_ker == 'on' and not out:
            self.event(ad['why'])
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
        elif 'REGIME GATE' in err:
            outcome = 'REGIME_REFUSED'
        elif 'TOX HOLD' in err:
            outcome = 'TOX_HOLD'
        elif 'TOX PULL' in err:
            outcome = 'TOX_PULL'
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
               'regime_gate': xm.get('regime_gate'),
               'px_first': xm.get('px_first'), 'px_last': xm.get('px_last'), 'reprices': xm.get('reprices'),
               'touch_last': xm.get('touch_last'), 'touch_qty_last': xm.get('touch_qty_last'),
               'post_rejects': xm.get('post_rejects', 0), 'signal_mid': xm.get('signal_mid'),
               'venue_mid': xm.get('venue_mid'), 'basis_bps': basis_bps(xm.get('signal_mid'), xm.get('venue_mid')),
               'decide_to_send_ms': ms('t0', 't_send'), 'ack_ms': xm.get('ack_ms'),
               'fill_wait_ms': ms('t_ack', 't_fill'), 'total_ms': ms('t0', 't_done'),
               'score': decision.score, 'consensus': (self.diag or {}).get('consensus'),
               'jev_lift': jev_lift(xm.get('path'), (self.diag or {}).get('consensus')),
               'edge_bps': (self.diag or {}).get('edge_bps'), 'policy': policy_id(self.p), 'code': CODE_VERSION,
               'crossed': xm.get('crossed'), 'cross_check': xm.get('cross_check'), 'dyn_ttl_s': xm.get('dyn_ttl_s'),
               'micro_ttl_s': xm.get('micro_ttl_s'), 'ttl_cut': xm.get('ttl_cut'), 'micro_ioc': xm.get('micro_ioc'),
               'micro_floor': xm.get('micro_floor'), 'tp_lift': xm.get('tp_lift'),
               'harvest': xm.get('harvest'), 'cvd_soft': xm.get('cvd_soft'), 'macro_soft': xm.get('macro_soft'),
               'harvest_key': xm.get('hkey'),
               'bracket': xm.get('bracket'), 'micro_bracket': xm.get('micro_bracket'),
                'dyn_cut': xm.get('dyn_cut'), 'orphan': xm.get('orphan'), 'tox': xm.get('tox'),
               **{k: v for k, v in xm.items() if k.startswith('cs_')}, 'error': err[:160] or None}
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
        harvest = path == 'SCALP' and tag == 'AUTO' and bool(getattr(self, '_harvest_next', False))   # 11-09e chop harvester
        macro_soft = harvest and bool(getattr(self, '_harvest_macro', False))     # 11-09f: entered through the soft 5 m macro
        self._harvest_next = self._harvest_macro = False    # one-shot: set by chop_scalp right before execute()
        if self.observer:
            return {'ok': False, 'error': f'OBSERVER: {self.p.symbol} is observe-only (no entries)'}
        if self.busy or self.pos or self.slot_block(side):
            why = self.slot_block(side)
            return {'ok': False, 'error': 'busy or position open' if not why else f'SCANNER: {why}'}
        why_edge = self.entry_edge_block(path, tag)                    # 11-09: no edge / padded quorum -> no order
        if why_edge:
            if why_edge.startswith('KELLY NO EDGE'):
                self.edge_shadow_arm(side, path)                       # probe with a virtual trade, no order
            if self.clock() - getattr(self, '_edge_evt_t', -1e9) >= 60.0:
                self._edge_evt_t = self.clock()
                self.event(f'OKAPI refused {side} {path}: {why_edge}')
            return {'ok': False, 'error': why_edge}
        self.busy, self.phase = True, 'EXECUTING'
        sym, tick, step = self.p.symbol, self.spec['tick'], self.spec['step']
        t0 = time.perf_counter()
        xm = self._xm = {'t0': t0, 'side': side, 'path': path, 'tag': tag, **({'harvest': True} if harvest else {}),
                         **({'macro_soft': True} if macro_soft else {})}
        try:
            why_regime, rpol = self.regime_check(path, tag, decision)
            if why_regime:
                self.event(f'REGIME GATE refused {side} {path}: {why_regime}')
                return {'ok': False, 'error': f'REGIME GATE: {why_regime}'}
            await self.refresh_book(0.0)
            f = {**self.feed.features(self.clock()), **{k: v for k, v in self.book().items() if v is not None}}
            xm.update(signal_mid=self.feed.price(), venue_mid=self.book().get('price'))
            px = self.gtx_price(side, f)
            if px is None:
                return {'ok': False, 'error': 'no book'}
            blk = self.micro_entry_block(side, f) if tag == 'AUTO' and not harvest else []   # harvest = the CHOP lane by design
            if blk:                                              # 11-09d: KER + triple alignment, every AUTO path
                for gate, _ in blk:
                    self.funnel.outcome(gate)
                if self.clock() - getattr(self, '_align_evt_t', -1e9) >= 30.0:
                    self._align_evt_t = self.clock()
                    self.event(f'OKAPI refused {side} {path}: ' + ' | '.join(w for _, w in blk))
                return {'ok': False, 'error': blk[0][1]}
            route = self.route(decision, f) if tag == 'AUTO' and path != 'MR' and not harvest else 'gtx'   # harvest: maker only
            if route == 'gtx' and tag == 'AUTO' and path not in ('MR', 'SCALP'):
                route = self.micro_ioc_route(decision, f, side, path, xm)       # 11-09 hybrid IOC (micro mode only)
            skew = 0
            if route == 'gtx' and self.p.tox_guard in ('shadow', 'on'):
                action, ticks, tox_why = self.tox_check(side)
                xm['tox'] = None if action == tg.QUOTE else f'{action}: {tox_why}'
                if action != tg.QUOTE and self.p.tox_guard == 'shadow':
                    if self.clock() - self._tox_evt_t >= 60.0:
                        self._tox_evt_t = self.clock()
                        self.event(f'(shadow) TOX {action} {side}: {tox_why}')
                elif action == tg.PULL:
                    self.cooldown_until = max(self.cooldown_until, self.clock() + self.p.tox_hold_cooldown_s)
                    self.event(f'TOX HOLD {side}: {tox_why} -> no passive quote, retry in '
                               f'{self.p.tox_hold_cooldown_s:g}s')
                    return {'ok': False, 'error': f'TOX HOLD: {tox_why}'}
                elif action == tg.SKEW:
                    skew = ticks
                    px = round_tick(px - ticks * tick if side == 'BUY' else px + ticks * tick, tick,
                                    'down' if side == 'BUY' else 'up')
                    self.event(f'TOX SKEW {side}: {tox_why} -> quote {ticks} tick behind the touch at {fmt(px, tick)}')
            bp = self.bracket_plan(path, f, route, harvest=harvest)
            base = bp['profile']
            if rpol is not None and not harvest and base.max_loss_usdt is None and (
                    rpol.stop_mult != 1.0 or rpol.tp_mult != 1.0):
                new_sl = base.sl_pct * rpol.stop_mult
                new_tp = base.tp_pct * rpol.tp_mult
                if path == 'SCALP':                                 # 11-08: SIDEWAYS x0.75 stops at the floor
                    new_sl = max(new_sl, self.p.scalp_sl_min_pct)
                if path == 'SCALP' and self.p.scalp_net_rr:
                    # Maker TP costs 0.04%; maker entry + taker stop + slip cost 0.08%.
                    min_tp = self.scalp_min_tp(new_sl, route)       # (new_sl + 0.08) x scalp_rr_min() + 0.04
                    new_tp = max(new_tp, min_tp)
                elif (self.p.micro_tp_lift and path != 'SCALP' and self.micro_active() and self.net_rr_eff()
                      and not self.p.micro_bracket):
                    # 11-09c: the regime scales TP and SL together while the fees stay fixed (MR 0.29 / 0.10 x SIDEWAYS 0.75 =
                    # RR 1.15). Lift TP back until the floor holds, never above the unscaled TP (the regime may only tighten);
                    # if even that cannot reach the floor, the RR gate below refuses honestly.
                    min_tp = self.min_tp_for(new_sl, route, self.net_rr_eff())
                    if min_tp > new_tp + 1e-12 and base.tp_pct > new_tp + 1e-12:
                        lifted = min(min_tp, base.tp_pct)
                        xm['tp_lift'] = f'{new_tp:.4f}->{lifted:.4f} (floor TP {min_tp:.4f}, unscaled {base.tp_pct:.4f})'
                        new_tp = lifted
                if self.p.micro_bracket and self.micro_active():     # 11-09d: x0.75 (SIDEWAYS) may not undo the noise floor
                    sl3, tp3 = self.micro_bracket(new_sl, new_tp, f, route, scalp=path == 'SCALP')
                    if (sl3, tp3) != (new_sl, new_tp):
                        xm['micro_bracket'] = f'{new_sl:.3f}/{new_tp:.3f}->{sl3:.3f}/{tp3:.3f}'
                        new_sl, new_tp = sl3, tp3
                base = replace(base, sl_pct=new_sl, tp_pct=new_tp)
                bp.update(sl_pct=new_sl, tp_pct=new_tp, rr_gross=new_tp / new_sl if new_sl > 0 else None)
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
            boosted = tag == 'AUTO' and path not in ('MR', 'SCALP') and self.alt_boosted(decision, self.diag)
            cap = self.alt_cap(ref, boosted)
            if boosted and cap and cap > (kq or 0.0):
                kk = self.alt_kelly()
                self.event(f'ALPHA PRIORITY {sym.replace("USDT", "")} vote '
                           f'{max(decision.score, (self.diag or {}).get("consensus") or 0.0) * 100:.0f}% >= '
                           f'{self.p.alt_boost_score * 100:.0f}% -> size {fmt(kq or 0.0, step)} -> {fmt(cap, step)}'
                           f' (Kelly {kk["n"]} lệnh, f {0 if kk["f"] is None else kk["f"]:.3f}, '
                           f'scale {kk["scale"]:.2f})')
                kq = cap
            superc = tag == 'AUTO' and path not in ('MR', 'SCALP') and self.super_conviction(decision, self.diag, f)
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
            if tag == 'AUTO' and path not in ('MR', 'SCALP') and self.p.conviction_sizing in ('shadow', 'on'):
                cs = self.conviction_size(decision, f, ref, base)
                xm.update({f'cs_{k}': v for k, v in cs.items()})
                if self.p.conviction_sizing == 'on' and cs.get('qty'):
                    if cs['qty'] != kq:
                        self.event(f'CONVICTION SIZE {sym.replace("USDT", "")} {cs["tier"]} -> '
                                   f'{fmt(cs["qty"], step)} (${cs["qty"] * ref:,.0f}'
                                   + (f', clipped by {cs["clip"]}' if cs.get('clip') else '') + ')')
                    kq = cs['qty']
            probe_mult = (self.p.scalp_size_mult if path == 'SCALP' else
                          self.p.drift_probe_mult if (tag == 'AUTO' and self.drift_probe) else 1.0)
            if probe_mult < 1.0:
                kq = floor_step(max(kq or base.qty or 0.0, self.min_qty(ref)) * probe_mult, step)
                kq = max(kq, self.min_qty(ref))                    # never below what the venue accepts
            if rpol is not None and rpol.size_mult < 1.0:
                q_regime = floor_step((kq or base.qty or self.min_qty(ref)) * rpol.size_mult, step)
                minimum = self.min_qty(ref)
                if q_regime < minimum - 1e-12 and path == 'SCALP' and self.micro_active():
                    xm['micro_floor'] = f'{rpol.state} size x{rpol.size_mult:g} -> venue lot'   # the micro lot IS the minimum;
                    q_regime = None                                                           # micro_check bounds the loss
                elif q_regime < minimum - 1e-12:
                    msg = (f'REGIME GATE: {rpol.state} size x{rpol.size_mult:g} = {fmt(q_regime, step)} '
                           f'is below the venue minimum {fmt(minimum, step)}')
                    self.event(f'OKAPI refused {side}: {msg}')
                    return {'ok': False, 'error': msg}
                if q_regime is not None:
                    kq = q_regime
            sr_mult = None                                      # 11-08 owner: scalp size vs the 0.15 % stop floor
            if path == 'SCALP' and not harvest and self.p.scalp_floor_resize and base.max_loss_usdt is None:
                raw = self.p.scalp_sl_pct * (rpol.stop_mult if rpol is not None else 1.0)   # stop without the floor
                e_in = (self.p.taker_bps + self.p.slip_bps) if route == 'ioc' else self.p.maker_bps
                lf = (e_in + self.p.taker_bps + self.p.slip_bps) / 100.0                 # plan() fees_pct
                sr_mult = min(1.0, (raw + lf) / (base.sl_pct + lf))
                if sr_mult < 1.0 - 1e-12:
                    q_risk = floor_step((kq or base.qty or self.min_qty(ref)) * sr_mult, step)
                    minimum = self.min_qty(ref)
                    if q_risk < minimum - 1e-12 and self.micro_active():
                        xm['micro_floor'] = f'scalp stop floor size x{sr_mult:.2f} -> venue lot'
                        q_risk = None
                    elif q_risk < minimum - 1e-12:
                        msg = (f'SCALP SIZE: stop {raw:.3f}% -> floor {base.sl_pct:.3f}% gives size x{sr_mult:.3f} = '
                               f'{fmt(q_risk, step)}, below the venue minimum {fmt(minimum, step)}')
                        self.event(f'OKAPI refused {side}: {msg}')
                        return {'ok': False, 'error': msg}
                    if q_risk is not None:
                        kq = q_risk
            if self.micro_active():                             # 11-09: on a small wallet the venue minimum sets the size
                msz = self.micro_sizer()
                fees_m = ((self.p.maker_bps if route == 'gtx' else self.p.taker_bps + self.p.slip_bps)
                          + self.p.taker_bps + self.p.slip_bps) / 100.0
                cap_n = msz.risk_notional(base.sl_pct if base.sl_pct else self.p.min_sl_pct, fees_m)
                if self.p.equity_cap is not None:
                    cap_n = min(cap_n, self.p.equity_cap * self.p.leverage) if cap_n else self.p.equity_cap * self.p.leverage
                q_micro, why_micro = msz.qty(ref, step, self.spec['min_qty'], self.spec['min_notional'],
                                             equity=self.micro_equity(), leverage=self.p.leverage, risk_notional=cap_n,
                                             pnl=self.micro_pnl())
                if q_micro is None:
                    self.event(f'OKAPI refused {side}: {why_micro}')
                    return {'ok': False, 'error': why_micro}
                kq = q_micro
                soft = self.cvd3m_soft(side, f) if tag == 'AUTO' else None
                if soft:                                        # 11-09e: lagging CVD 3 m = size penalty, not a block
                    q_min, _ = msz.qty(ref, step, self.spec['min_qty'], self.spec['min_notional'], equity=None,
                                       leverage=self.p.leverage, risk_notional=cap_n)     # equity unknown -> smallest window lot
                    q_soft = floor_step(q_micro * self.p.micro_cvd_soft_mult, step)
                    kq = max(q_soft, q_min) if q_min else q_soft
                    xm['cvd_soft'] = f'{soft} | lot {fmt(q_micro, step)} -> {fmt(kq, step)}' + (
                        ' (at the venue lot: no size left to cut)' if kq >= q_micro - 1e-12 else '')
            mkpl = lambda q: plan(side, px if route == 'gtx' else touch, self.spec,
                                  replace(base, entry='gtx' if route == 'gtx' else 'market'),
                                  self.account.get('available'), qty=q)
            pl = mkpl(kq)
            if not pl['ok']:
                self.event(f'OKAPI refused {side}: {pl["why"]}')
                return {'ok': False, 'error': pl['why']}
            xm['bracket'] = [round(pl['sl_pct'], 4), round(pl['tp_pct'], 4)]
            why_micro = self.micro_check(pl) if self.micro_active() else None
            if why_micro:
                self.event(f'OKAPI refused {side}: {why_micro}')
                return {'ok': False, 'error': why_micro}
            rr = self.plan_rr(pl, route)
            bp.update(tp_pct=pl['tp_pct'], sl_pct=pl['sl_pct'],
                      rr_gross=pl['tp_pct'] / pl['sl_pct'] if pl['sl_pct'] > 0 else None,
                      rr_net=rr, be_wr=1.0 / (1.0 + rr) if rr is not None and rr > -1.0 else None)
            rr_gate = self.scalp_rr_min() if path == 'SCALP' else self.net_rr_eff()    # 11-08: SCALP floor 1.35
            if (tag == 'AUTO' and not harvest and (path != 'SCALP' or self.p.scalp_net_rr) and rr_gate
                    and (rr is None or rr < rr_gate - 1e-12)):
                msg = (f'ZEPHR NET RR {"—" if rr is None else f"{rr:.2f}"} < {rr_gate:g} '
                       f'(TP {pl["tp_pct"]:.3f}% / SL {pl["sl_pct"]:.3f}% + fees {pl["fees_pct"]:.3f}%)')
                self.event(f'OKAPI refused {side}: {msg}')
                return {'ok': False, 'error': msg}
            probe = None
            if tag == 'AUTO' and self.p.alt_boost and not self.is_alt() and self.p.btc_probe_qty and not self.micro_active():
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
            if harvest:
                self.hlog_post(xm, side, px, f, decision)
            intent = MicroIntent(sym, side, pl['qty_str'], route, tag, int(self.clock() * 1000))
            token = self.authority.issue(intent, decision)
            self.authorized += 1
            self.auth_at = self.clock()
            self.authority.consume(token, intent)      # VESKA spends the one-use token right before routing
            cid = f'gk{tag[0].lower()}-' + uuid.uuid4().hex[:26]
            posted_ms = int(self.clock() * 1000)       # entry fill can land any time in the GTX window
            px0, reprices, toxic_abort, tox_abort = px, 0, None, None
            waited = None
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
                st, deadline = {}, time.monotonic() + (self.p.micro_harvest_ttl_s if harvest else self.p.gtx_ttl_s)
                peg = self.p.peg and decision.score >= self.p.peg_min_score and path != 'MR'
                last_post = t_post = time.monotonic()
                auto = tag == 'AUTO' and path not in ('MR', 'SCALP')
                cross_mode = self.p.super_cross if auto else 'off'
                dyn_mode = self.p.dyn_ttl if auto else 'off'
                cross_at = t_post + self.p.super_cross_after_s if cross_mode in ('shadow', 'on') else None
                do_cross, dyn_logged, mid0 = False, False, self.feed.price()
                micro_ttl_on = (
                    self.micro_active() and self.p.micro_ttl and tag == 'AUTO' and path != 'MR'
                )
                micro_next = 0.0
                while True:
                    now_m = time.monotonic()
                    if now_m >= deadline and micro_ttl_on:
                        # 11-09 adaptive TTL: a clean book earns micro_ttl_s (then +1 s steps to micro_ttl_max_s)
                        if now_m - t_post >= self.p.micro_ttl_max_s - 1e-9:
                            break
                        keep, why_keep = await self.micro_keep(side, px0, mid0, now_m - t_post)
                        if not keep:
                            xm['ttl_cut'] = why_keep
                            break
                        deadline = (t_post + self.p.micro_ttl_s if xm.get('micro_ttl_s') is None
                                    else min(now_m + 1.0, t_post + self.p.micro_ttl_max_s))
                        xm['micro_ttl_s'] = round(deadline - t_post, 1)
                    elif now_m >= deadline:
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
                    elif micro_ttl_on and xm.get('micro_ttl_s') and now_m >= micro_next:
                        micro_next = now_m + 0.5
                        keep, why_keep = await self.micro_keep(side, px0, mid0, now_m - t_post)
                        if not keep:                              # adverse / toxic / drifted: pull it at once
                            xm['ttl_cut'] = why_keep
                            break
                    elif harvest and now_m >= micro_next:
                        micro_next = now_m + 0.5                  # 11-09e: rests up to 45 s, only the HARD cuts apply (age 0 =
                        keep, why_keep = await self.micro_keep(side, px0, mid0, 0.0)   # inside the floor): adverse / spread / drift
                        if not keep:
                            xm['ttl_cut'] = why_keep
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
                    if self.p.tox_guard == 'on':
                        action_r, _, tox_why = self.tox_check(side)
                        if action_r == tg.PULL:
                            tox_abort = tox_why
                            xm['tox'] = f'PULL: {tox_why}'
                            break
                    if (peg and time.monotonic() - last_post >= self.p.peg_interval_s
                            and (not micro_ttl_on or reprices < self.p.micro_max_reprices)):
                        # Dynamic pegged maker: follow the touch while the book walks away, never cross it,
                        # never further than peg_max_drift_pct from the first quote (bounded chase).
                        await self.refresh_book(0.0)
                        new = self.gtx_price(side, self.book())
                        if new is not None and skew:
                            new = round_tick(new - skew * tick if side == 'BUY' else new + skew * tick, tick,
                                             'down' if side == 'BUY' else 'up')
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
                if micro_ttl_on:
                    waited = round(time.monotonic() - t_post, 1)
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
                self.hlog_close(xm)
                self.cooldown_until = self.clock() + self.p.unfilled_cooldown_s
                self.phase = 'SCANNING'
                if tox_abort is not None:
                    self.event(f'GTX {side} {pl["qty_str"]} @ {fmt(px, tick)} cancelled: TOX PULL {tox_abort}')
                    return {'ok': False, 'error': f'TOX PULL: {tox_abort}', 'latency_ms': ms, 'route': 'GTX'}
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
                ttl_txt = waited if waited else self.p.gtx_ttl_s
                self.event(f'GTX {side} {pl["qty_str"]} @ {fmt(px, tick)} not filled in {ttl_txt:g}s '
                           f'-> cancelled (peg drift cap {self.p.peg_max_drift_pct}%), rescan in {self.p.unfilled_cooldown_s:g}s')
                return {'ok': False, 'error': f'GTX không khớp trong {ttl_txt:g}s (peg tối đa '
                                              f'{self.p.peg_max_drift_pct}%) - đã huỷ, quét lại sau {self.p.unfilled_cooldown_s:g}s',
                        'latency_ms': ms, 'route': 'GTX'}
            qty = floor_step(filled, step)
            qty_str = fmt(qty, step)
            sl, tp = brackets(side, fill, pl, tick)
            half = floor_step(qty * base.tp1_frac, step)
            split = half >= self.spec['min_qty'] and qty - half >= self.spec['min_qty'] - 1e-12
            tp_plan = tp
            if (self.p.tp1_scaled_pct and split and tag == 'AUTO' and path not in ('MR', 'SCALP')
                    and self.p.tp1_scaled_pct < pl['tp_pct']):          # 10-08z: 50 % maker TP1 closer, runner trails
                d1 = 1 if side == 'BUY' else -1
                tp1p = (max(self.p.tp1_scaled_pct, self.p.tp1_scaled_big_pct)
                        if qty * fill >= self.p.cs_big_notional - 1e-9 else self.p.tp1_scaled_pct)
                tp1p = min(tp1p, pl['tp_pct'])
                tp = round_tick(fill * (1 + d1 * tp1p / 100.0), tick, 'up' if d1 > 0 else 'down')
            runner = self.runner_grade(path, tag, decision)
            if runner and self.p.micro_runner_tp_pct > pl['tp_pct']:    # 11-10 (e): the TP order is a far cap, the trail exits
                d1 = 1 if side == 'BUY' else -1
                tp = round_tick(fill * (1 + d1 * self.p.micro_runner_tp_pct / 100.0), tick, 'up' if d1 > 0 else 'down')
            opp = 'SELL' if side == 'BUY' else 'BUY'
            self.pos = {'symbol': sym, 'side': side, 'qty': qty, 'qty_str': qty_str, 'entry': fill,
                        'opened': self.clock(), 'opened_ms': posted_ms - 1000, 'sl': sl, 'tp': tp,
                        'sl0': sl, 'sl_cid': 'gs-' + uuid.uuid4().hex[:28], 'tp_cid': 'gt-' + uuid.uuid4().hex[:28],
                        'stop_kind': 'SL', 'tp_qty': half if split else qty, 'split': split, 'tp1_filled': False,
                        'be_locked': False, 'mbe_locked': False, 'trail_on': False, 'peak': fill, 'mfe_pct': 0.0,
                        'adverse_usd': 0.0, 'regime': (self.reg or {}).get('name'),
                        'trigger_usd': abs(f.get('cvd_exit_win') or 0.0), 'flow_limit': self.flow_limit(f),
                        'edge_bps': (self.diag or {}).get('edge_bps'),
                        'consensus': (self.diag or {}).get('consensus'),
                        'jev_lift': jev_lift(path, (self.diag or {}).get('consensus')), 'ker': f.get('ker'),
                        'atr_pct': bp['atr_pct'], 'atr_trail_pct': bp['trail_pct'], 'bracket_src': bp['src'],
                        'rr_gross': bp['rr_gross'], 'rr_net': bp['rr_net'], 'be_wr': bp['be_wr'],
                        'vpin': f.get('vpin'), 'ofi': f.get('ofi_10s'), 'kelly_f': self.kelly().get('f'),
                        'quote_px': px, 'size_mult': mult, 'alt_boost': boosted, 'probe': probe, 'super_size': superc,
                        'tp_plan': tp_plan, 'runner': runner,
                        'conviction': {k[3:]: v for k, v in xm.items() if k.startswith('cs_')} or None,
                        'agents': {v.agent: [v.side, v.confidence] for v in (self.votes or [])
                                   if v.agent in swarm.ALPHA} or None,
                        **(self.autonomy.entry_tags(self) if self.autonomy is not None else {}),
                        'align3': (self.align3_last or 'OK') if self.p.align3 in ('shadow', 'on') else None,
                        'drift_probe': self.drift_probe if tag == 'AUTO' else None,
                        **({'eject_s': self.p.scalp_be_s, 'eject_hard_s': self.p.scalp_hard_s,
                            'eject_mfe': 1e9, 'scalp_risk_mult': sr_mult} if path == 'SCALP' else {}),
                        **({'eject_s': self.p.micro_patient_be_s, 'eject_hard_s': self.p.micro_patient_be_s, 'patient': True,
                            'patient_mfe': self.p.micro_patient_be_mfe_pct}
                           if xm.get('harvest') and self.p.micro_exit_policy == 'patient' else {}),
                        **({'eject_s': self.p.micro_runner_stale_s, 'eject_mfe': self.p.micro_runner_stale_mfe_pct,
                            'eject_hard_s': self.p.micro_runner_stale_s + max(self.p.eject_hard_s - self.p.eject_s, 0.0)}
                           if runner and self.p.micro_runner_stale_s > 0 else {}),     # 11-10 (g) stale-runner scratch
                        'px_vel_15s': f.get('px_vel_15s'), 'macro': self.macro.get('name'),
                        'tag': tag, 'path': path, 'score': decision.score, 'reason': decision.reason,
                        'micro': self.micro_active(),
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
                        'markout_bps': {}, 'harvest_key': xm.get('hkey')}
            self.hlog_fill(xm, fill)
            self.trade_times.append(self.clock())
            how = (f'IOC TAKER super-edge, cons {(self.diag or {}).get("consensus", 0) * 100:.1f}% JEV '
                   f'{(self.diag or {}).get("jev", 0):.1f}' if route == 'ioc' else 'POST-ONLY GTX maker')
            self.event(f'VESKA {tag} {side} {qty_str} {sym} @ {fill:g} ({how}, {ms:.0f} ms) · {path} '
                       f'{decision.score * 100:.1f}% · TP1 {tp:g} x{fmt(self.pos["tp_qty"], step)} · SL {sl:g}'
                       + (f' · RUNNER (TP is a cap, planned {tp_plan:g}; '
                          + (f'stale eject {self.pos["eject_s"]:.0f}s under +{self.pos["eject_mfe"]:g}% MFE'
                             if self.pos.get('eject_s') else 'no time eject')
                          + f'; trail from +{self.p.trail_activate_pct}%)' if runner else ''))
            try:
                await self.place_entry_stop(sym, opp, fmt(sl, tick), self.pos['sl_cid'])
            except Exception as exc:
                self.event(f'MARIN stop rejected ({type(exc).__name__}: {exc}) -> closing at market')
                self.pos['stop_reject'] = f'{type(exc).__name__} {getattr(exc, "code", "")}'.strip()[:80]   # 10-09 cc
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
        off = self.cost.stop_out_pct() + self.p.be_offset_hard_pct  # maker in + taker stop + slip + 0.02 %
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
        """Maker-exit breakeven: entry + maker in + maker out + offset (+0.12 % on VIP0 since 11-09: 0.04 + 0.08)."""
        d = 1 if p['side'] == 'BUY' else -1
        off = (p.get('entry_fee_bps', self.p.maker_bps) + self.p.maker_bps) / 100.0 + self.be_offset_eff()
        return round_tick(p['entry'] * (1 + d * off / 100.0), self.spec['tick'], 'up' if d > 0 else 'down')

    def shield_price(self, p: dict) -> float:
        """Fee shield: the better of entry + fee_shield_pct and the fee-true maker breakeven."""
        d = 1 if p['side'] == 'BUY' else -1
        fixed = round_tick(p['entry'] * (1 + d * self.p.fee_shield_pct / 100.0), self.spec['tick'],
                           'up' if d > 0 else 'down')
        be = self.soft_be_price(p)
        return max(fixed, be) if d > 0 else min(fixed, be)

    def be_offset_eff(self) -> float:
        """11-09: SOFT BE offset, never below the fee-aware floor of this coin (0.08 %, SOL 0.065 %): a configured
        value under it (or a negative one) cannot bring back a breakeven target that loses money once the maker exit
        escalates to a taker order."""
        return max(self.p.be_offset_pct, BE_OFFSET_FLOOR.get(self.p.symbol, BE_OFFSET_FLOOR_DEFAULT))

    def be_level_pct(self, p: dict, soft: bool = True) -> float:
        """Favourable % at which the BE exit level sits: shield_price (soft maker exit) or be_price (hard stop)."""
        d = 1 if p['side'] == 'BUY' else -1
        px = self.shield_price(p) if soft else self.be_price(p)
        return d * (px / p['entry'] - 1.0) * 100.0

    def be_arm_pct(self, p: dict, soft: bool = True) -> float:
        """MFE % needed before a BE tier may be chosen: be_trigger_pct, and never below the level it would lock."""
        return max(self.p.be_trigger_pct, self.be_level_pct(p, soft))

    def fee_hurdle_covered(self, p: dict, soft: bool = True) -> bool:
        """True once the best excursion reached the BE level, i.e. the fee hurdle is paid: the only time be_locked
        may be declared (11-09: a soft target chosen below it was a fake breakeven)."""
        return p['mfe_pct'] >= self.be_level_pct(p, soft) - 1e-9

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
        if p.get('sl'):                        # 10-08z2: never a maker exit worse than the exchange stop (#51)
            want = max(want, p['sl']) if opp == 'SELL' else min(want, p['sl'])
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
                if k == '5s':
                    self.own_mk5.append(mk[k][0])
        if p.get('exiting'):
            x = p['exiting']
            if self.soft_exit_due(p, x, mark):
                await self.escalate_exit(p, x, mark)
                return
            hard_eject_s = p.get('eject_hard_s', self.p.eject_hard_s)
            if (x.get('floor') and self.clock() - p['opened'] >= hard_eject_s
                    and self.eject_escalate_ok(p, f, fav, hard_eject_s)):
                x.update(floor=None, reason='EJECT')
                self.event(f'MARIN EJECT escalate: BE order unfilled after {hard_eject_s:.0f}s -> '
                           f'maker at the touch ({fav:+.3f}%, hard SL {p["sl"]:g} stays)')
            await self.work_soft_exit()
            return
        now = self.clock()
        why_early = self.early_invalid_due(p, now, f)
        if why_early and not p.get('early_invalid'):
            p['early_invalid'] = {'t': round(now - p['opened'], 1), 'fav_pct': round(fav, 4), 'why': why_early,
                                  'mode': self.p.early_invalidation}
            if self.p.early_invalidation == 'on':
                self.event(f'MARIN EARLY INVALIDATION {why_early} at {fav:+.3f}% -> post-only reduce at the touch '
                           f'(fee hurdle bypassed, hard SL {p["sl"]:g} stays)')
                await self.start_soft_exit('EARLY-INVALID')
                return
        why_run = self.early_invalid_due(p, now, f, runner=True)
        if why_run:                              # 11-10 (g): a runner has no time eject, so this one acts ('armed' beside the shadow record)
            p['early_invalid'] = {**(p.get('early_invalid') or {}),
                                  'armed': {'t': round(now - p['opened'], 1), 'fav_pct': round(fav, 4), 'why': why_run}}
            self.event(f'MARIN RUNNER EARLY INVALIDATION {why_run} at {fav:+.3f}% -> post-only reduce at the touch '
                       f'(fee hurdle bypassed, hard SL {p["sl"]:g} stays)')
            await self.start_soft_exit('EARLY-INVALID')
            return
        if self.eject_fast_due(p, now, f):
            self.event(f'MARIN FAST EJECT: {now - p["opened"]:.0f}s >= {self.p.eject_fast_s:.0f}s, MFE '
                       f'{p["mfe_pct"]:+.3f}% < +{self.p.eject_fast_mfe_pct}%, 15s velocity '
                       f'{(f.get("px_vel_15s") or 0):+.3f}% weak -> post-only reduce at the touch')
            await self.start_soft_exit('EJECT-FAST')
            return
        if self.eject_due(p, now):
            px = self.eject_floor(p)
            if p.get('runner'):                  # 11-10 (g): the runner's own schedule (micro_runner_stale_s / _mfe_pct)
                self.event(f'MARIN RUNNER STALE EJECT: {now - p["opened"]:.0f}s >= {p["eject_s"]:.0f}s, MFE '
                           f'{p["mfe_pct"]:+.3f}% < +{p["eject_mfe"]:g}% -> post-only reduce at BE {px:g} '
                           f'(entry {"+" if d > 0 else "-"}fees), at the touch after {p["eject_hard_s"]:.0f}s')
            else:
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
        breath_s = 5.0 if self.micro_active() else self.p.flow_breath_s
        p['breathing'] = (now - p['opened'] < breath_s) and (adverse < 25_000.0)
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
        if p.get('runner') and own is None:                           # 11-10 (e): no chop eject for a runner; (g) its own stale
            return False                                              # schedule, set at the fill, runs through the lines below
        if p.get('patient'):
            # 11-10 patient harvest: the BE scratch is for a trade that already RAN >= patient_mfe and then stalled; a trade that
            # never moved is not scratched (it keeps its TP and its exchange SL)
            return (self.p.soft_exits and not p.get('tp1_filled') and now - p['opened'] >= own
                    and p['mfe_pct'] >= p['patient_mfe'])
        return ((self.p.eject or own is not None) and self.p.soft_exits and not p.get('tp1_filled')
                and now - p['opened'] >= (own or self.p.eject_s)
                and p['mfe_pct'] < p.get('eject_mfe', self.p.eject_mfe_pct))

    def eject_fast_due(self, p: dict, now: float, f: dict) -> bool:
        """Low-velocity time decay: >= 120 s, MFE never reached +0.08 %, |px_vel_15s| < 0.025 % (unknown -> no)."""
        vel = f.get('px_vel_15s')
        return (self.p.eject_fast and self.p.soft_exits and p.get('eject_s') is None and not p.get('tp1_filled')
                and not p.get('runner')
                and now - p['opened'] >= self.p.eject_fast_s and p['mfe_pct'] < self.p.eject_fast_mfe_pct
                and vel is not None and abs(vel) < self.p.eject_fast_vel_pct)

    def adverse_momentum(self, p: dict, f: dict) -> Optional[str]:
        """11-09: confirmed momentum against the position -> reason, else None: CVD velocity 10 s <= -early_inval_vel_usd,
        OFI L10 <= -early_inval_ofi, price velocity 15 s <= -eject_fast_vel_pct (all signed with the trade), or toxic
        VPIN. A missing feature is unknown, never adverse."""
        d = 1 if p['side'] == 'BUY' else -1
        vel = (f.get('cvd_vel_10s') or 0.0) * d
        if vel <= -self.p.early_inval_vel_usd:
            return f'CVD {vel / 1e3:+.0f}k/s against'
        ofi = f.get('ofi_10s')
        if ofi is not None and ofi * d <= -self.p.early_inval_ofi:
            return f'OFI {ofi:+.2f} against'
        pv = f.get('px_vel_15s')
        if pv is not None and d * pv <= -self.p.eject_fast_vel_pct:
            return f'15 s velocity {pv:+.3f}% against'
        if (getattr(self, 'tox', None) or {}).get('toxic'):
            return 'toxic flow'
        return None

    def eject_escalate_ok(self, p: dict, f: dict, fav: float, hard_s: float) -> bool:
        """11-09: may the EJECT-BE scratch order drop its floor and exit at the touch? Not while gross is below the fee
        hurdle (entry fee + maker exit: a touch exit there books a net loss) unless adverse momentum / toxic flow is
        confirmed; after eject_defer_max_s past hard_s the old behaviour returns, so the time stop stays bounded."""
        if p.get('patient'):
            return self.eject_toxic(p, f, fav)
        if not self.p.eject_guard:
            return True
        hurdle = (p.get('entry_fee_bps', self.p.maker_bps) + self.p.maker_bps) / 100.0
        if fav >= hurdle or self.clock() - p['opened'] >= hard_s + self.p.eject_defer_max_s:
            return True
        why = self.adverse_momentum(p, f)
        if why:
            if not p.get('eject_adverse'):
                p['eject_adverse'] = why
                self.event(f'MARIN EJECT escalate on {why} ({fav:+.3f}% < fee hurdle {hurdle:.3f}%)')
            return True
        if not p.get('eject_deferred'):
            p['eject_deferred'] = round(self.clock() - p['opened'], 1)
            self.event(f'MARIN EJECT held: {fav:+.3f}% < fee hurdle {hurdle:.3f}%, no adverse momentum -> scratch order '
                       f'stays up to {self.p.eject_defer_max_s:.0f}s more (hard SL {p["sl"]:g} stays)')
        return False

    def eject_toxic(self, p: dict, f: dict, fav: float) -> bool:
        """11-10 patient exits: may a scratch order that is still unfilled drop its floor and leave at the touch?  Only when the
        touch exit is not a loss (gross already covers entry + maker exit fee) or informed flow is verified: VPIN >= micro_patient_vpin
        (known), adverse aggressor flow >= the trade's flow limit, or the CVD regime flipped against it.  Time alone never does."""
        hurdle = (p.get('entry_fee_bps', self.p.maker_bps) + self.p.maker_bps) / 100.0
        if fav >= hurdle:
            return True
        v = f.get('vpin')
        why = (f'VPIN {v:.2f} >= {self.p.micro_patient_vpin:g}' if v is not None and v >= self.p.micro_patient_vpin else
               f'adverse flow {self.adverse_flow(p) / 1e3:.0f}k >= limit' if self.adverse_flow(p) >= (p.get('flow_limit') or self.flow_limit({}))
               else 'regime flipped' if self.regime_flipped(p, f) else None)
        if why and not p.get('eject_adverse'):
            p['eject_adverse'] = why
            self.event(f'MARIN EJECT escalate on {why} ({fav:+.3f}% < fee hurdle {hurdle:.3f}%)')
        return why is not None

    def eject_floor(self, p: dict) -> float:
        """Fee-true scratch: entry +/- (entry fee + maker exit fee) -> net PnL ~ 0 when the reduce order fills.
        Patient positions add one tick, so the scratch nets a strictly positive amount (never below the fees)."""
        d = 1 if p['side'] == 'BUY' else -1
        off = (p.get('entry_fee_bps', self.p.maker_bps) + self.p.maker_bps) / 1e4
        tick = self.spec['tick']
        return round_tick(p['entry'] * (1 + d * off) + (d * tick if p.get('patient') else 0.0), tick, 'up' if d > 0 else 'down')

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
        if p['mfe_pct'] >= self.be_arm_pct(p, soft=True):           # 11-09: MFE must have paid the level it locks
            cands.append((self.shield_price(p), 'BE'))
        if p['mfe_pct'] >= self.p.lock_trigger_pct:
            cands.append((self.plock_price(p), 'PROFIT'))
        if self.p.pre_stop_maker and p.get('sl') and d * (p['sl'] - p['entry']) > 0:
            # 10-08z: exchange stop in profit -> meet the retrace with a maker exit before the taker stop
            cands.append((round_tick(p['sl'] * (1 + d * self.p.pre_stop_gap_pct / 100.0), self.spec['tick'],
                                     'up' if d > 0 else 'down'), 'PRESTOP'))
        gaps = []
        if p['mfe_pct'] >= self.p.trail_activate_pct:
            gaps.append(self.p.trail_tight_gap_pct if p['mfe_pct'] >= self.p.trail_tight_pct else self.p.trail_gap_pct)
        if p.get('atr_trail_pct') and p.get('tp1_filled'):         # TP2: runner trails 1.2 x ATR after TP1
            gaps.append(p['atr_trail_pct'])
        if gaps:
            gap = min(gaps)
            if p.get('runner'):                                     # 11-10 (e): room for the move, never tighter than the plain trail
                gap = max(self.p.trail_gap_pct, self.p.micro_runner_trail_atr * (p.get('atr_pct') or 0.0))
            cands.append((round_tick(p['peak'] * (1 - d * gap / 100.0), self.spec['tick'], 'down' if d > 0 else 'up'),
                          'TRAIL'))
            p['trail_gap'] = round(gap, 4)
        for px, k in cands:
            if level is None or d * (px - level) > 0:
                if k != kind:
                    self.event({'LOCK': f'MARIN T1 MICRO-LOCK at {fav:+.3f}% -> exit level {px:g} (entry +'
                                        f'{self.p.microbe_lock_pct}%, loss <= fees)',
                                'BE': f'MARIN T2 FEE-SHIELD at {fav:+.3f}% -> exit level {px:g} (>= entry +'
                                      f'{self.p.fee_shield_pct}% and entry + fees + {self.be_offset_eff():g}%)',
                                'PROFIT': f'MARIN T2b PROFIT LOCK at {fav:+.3f}% -> exit level {px:g} (entry +'
                                          f'{self.p.lock_pct}%)',
                                'TRAIL': f'MARIN T4 TRAIL {p.get("trail_gap")}% behind peak {p["peak"]:g} -> {px:g}',
                                'PRESTOP': f'MARIN PRE-STOP MAKER exit level {px:g} = exchange stop {p["sl"]:g} '
                                           f'+ {self.p.pre_stop_gap_pct}% (maker before the taker stop)'}[k])
                level, kind = px, k
        if kind in ('BE', 'PROFIT', 'TRAIL', 'PRESTOP') and self.fee_hurdle_covered(p, soft=True):
            p['be_locked'] = True                                   # 11-09: only once MFE has paid the fee hurdle
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
        if not p['be_locked'] and p['mfe_pct'] >= self.be_arm_pct(p, soft=False):
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
                    self.event(f'MARIN BREAKEVEN LOCK at +{fav:.3f}% -> stop {target:g} (net >= +{self.p.be_offset_hard_pct}%)')
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
                and self.spec and not self.trend_owned):
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
        if (reason or '').split('+')[-1] in ('SOFT-BE', 'SOFT-LOCK', 'SOFT-PRESTOP', 'SOFT-PROFIT', 'EJECT-BE') \
                and parts.get('gross') is not None and parts['gross'] < 0:
            reason = f'{reason}-LOSS'          # 10-08z2: a 'break-even' exit below the entry is labelled as a loss
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
               'tag': p['tag'], 'path': p['path'], 'score': p['score'], 'consensus': p.get('consensus'),
               'jev_lift': p.get('jev_lift'), 'edge_bps': p.get('edge_bps'),
               'ker': p.get('ker'), 'flow_limit': p.get('flow_limit'), 'macro': p.get('macro'),
               'bracket_src': p.get('bracket_src'), 'atr_pct': p.get('atr_pct'), 'rr_gross': p.get('rr_gross'),
               'rr_net': p.get('rr_net'), 'be_wr': p.get('be_wr'), 'vpin': p.get('vpin'), 'ofi': p.get('ofi'),
               'kelly_f': p.get('kelly_f'), 'size_mult': p.get('size_mult'), 'ratchet': bool(p.get('ratchet')),
               'micro': bool(p.get('micro')),
               'scalp_risk_mult': p.get('scalp_risk_mult'),          # 11-08: SCALP size vs the stop floor
               'exit_policy': 'patient' if p.get('patient') else None,   # 11-10
               'runner': True if p.get('runner') else None, 'tp_plan': p.get('tp_plan'),   # 11-10 (e): planned TP vs the runner cap
               'alt_boost': bool(p.get('alt_boost')), 'eject_floor': (x or {}).get('floor0'),
               'probe': p.get('probe'), 'super_size': bool(p.get('super_size')), 'drift_probe': p.get('drift_probe'),
               'conviction': p.get('conviction'), 'align3': p.get('align3'), 'early_invalid': p.get('early_invalid'),
               'agents': p.get('agents'), 'arms': p.get('arms'), 'agent_w': p.get('agent_w'),
               'stop_reject': p.get('stop_reject'),
               'schema': LEDGER_SCHEMA, 'policy': policy_id(self.p), 'code': CODE_VERSION,
               'signal_mid': p.get('signal_mid'), 'venue_mid': p.get('venue_mid'), 'basis_bps': p.get('basis_bps'),
               'basis_exit_bps': basis_bps(self.feed.price(), self.book().get('price')),
               'ack_ms': p.get('ack_ms'), 'fill_wait_ms': p.get('fill_wait_ms'), 'markout_bps': p.get('markout_bps'),
               'harvest_key': p.get('harvest_key'),
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
        if self.autonomy is not None:
            try:
                self.autonomy.on_settle(self, row)
            except Exception as exc:                  # learning must never break settlement
                self.event(f'autonomy settle error {type(exc).__name__}: {exc}'[:200])
        self.cooldown_until = self.clock() + self.p.cooldown_s
        if pnl < 0 and p['tag'] == 'AUTO':
            self.consec_losses += 1
            if self.consec_losses >= self.p.max_consec_losses:
                self.pause_until = self.clock() + (self.p.micro_loss_pause_s if self.micro_active()
                                                   else self.p.loss_pause_s)
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
        now, rs, clf = self.clock(), self.regime_state, self.regime_clf
        regime_warming = (self.p.regime_gate in ('on', 'shadow') and
                          (rs is None or (rs.state == rc.UNKNOWN and rs.reason.startswith('WARMUP'))))
        regime_warmup_elapsed = (max(0.0, now - clf.warmup_since) if clf and clf.warmup_since is not None
                                 else 0.0 if regime_warming else None)
        regime_warmup_required = (max(clf.cfg.er_min_span_s, float(clf.cfg.garch_warmup))
                                  if clf and regime_warming else None)
        return {'ofi': f.get('ofi_10s'), 'ofi_n': f.get('ofi_n'), 'vpin': f.get('vpin'), 'vpin_n': f.get('vpin_n'),
                'vpin_side': self.toxic_side if self.clock() < self.toxic_until else None, 'symbol': self.p.symbol,
                'tox': self.tox.get('state'), 'toxic_left': max(0.0, self.toxic_until - self.clock()),
                'tox_guard': self.p.tox_guard, 'tox_last': self.tox_last, 'own_mk5_n': len(self.own_mk5),
                'maker_markout': f.get('maker_markout'),
                'regime_gate': self.p.regime_gate,
                'regime_state': rs.as_dict() if rs is not None else None,
                'regime_warmup_elapsed_s': regime_warmup_elapsed,
                'regime_warmup_required_s': regime_warmup_required,
                'atr_pct': f.get('atr_pct'), 'yz_pct': f.get('yz_pct'), 'sigma_bps': f.get('sigma_bps'),
                'bars_n': f.get('bars_n'), 'brackets': bp, 'kelly': self.kelly(),
                'edge_shadow': self.edge_shadow_view(),
                'micro_capital': {**self.micro_sizer().view(self.micro_wallet(), self.p.micro_capital),
                                  'equity': self.micro_equity(), 'window_hi': round(self.micro_window_hi(), 3),
                                  'scores': dict(self.micro_scores()) if self.micro_active() else {},
                                  'ioc': self.p.micro_ioc, 'ttl': self.p.micro_ttl,
                                  'lane': self.micro_lane(f) if self.micro_active() else None,      # 11-09e dual mode
                                  'harvester': self.p.micro_harvester,
                                  'harvest_score': self.harvest_score(f) if self.micro_active() else None,
                                  'pnl': self.micro_pnl() if self.micro_active() else None,          # 11-09f
                                  'slots': self.max_positions_eff(), 'open': len(self.open_peers()) + (1 if self.pos else 0),
                                  'accel_k': self.p.micro_accel_k, 'risk_cap': self.risk_cap() if self.micro_active() else None}}

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
                        'realized': self.day_pnl, 'kill': self.daily_kill_eff(),
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
            'flow': {**self.flow, 'gate': self.p.concordance_gate, 'drift_lock': self.p.drift_lock and not self.drift_exempt(),
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
# 10-09 (ff) network resilience for the public REST market data (log: "_ssl.c:990: The handshake operation timed
# out" on the 4H fetch).  Each host gets REST_TRIES attempts (timeout 12 s, back-off 0.5 / 1 / 2 s); a definitive
# HTTP 4xx (451 geo-block, 400) is not retried.  Hosts: the official USD-M REST only - fapi.binance.vision does not
# resolve and fapi1/2/3.binance.com answer 302 -> www.binance.com (checked 10-09), so they are not API mirrors.
# When every attempt fails: the last good rows of the same (symbol, interval, limit) from KLINE_CACHE (a stale bar_t
# just means no new action), else a cold-start SPOT proxy (data-api.binance.vision /api/v3/klines, same row format):
# perp-like prices but SPOT volume, so its rows carry no volume / CVD (UNKNOWN -> the trend gate opens nothing).
REST_TIMEOUT_S = 12.0
REST_BACKOFF_S = (0.5, 1.0, 2.0)
REST_HOSTS = ('https://fapi.binance.com',)
SPOT_PROXY = 'https://data-api.binance.vision/api/v3/klines'
KLINE_CACHE: dict = {}                    # (symbol, interval, limit) -> rows
KLINE_SOURCE: dict = {}                   # (symbol, interval) -> 'fapi' | 'cache' | 'spot-proxy'


def rest_json(urls, timeout: float = REST_TIMEOUT_S, backoff=REST_BACKOFF_S, sleep=time.sleep, opener=None):
    """GET the first URL that answers; retries with back-off on network errors / 429 / 5xx."""
    import urllib.error
    import urllib.request
    opener = opener or urllib.request.urlopen
    last = None
    for url in urls:
        for k in range(len(backoff)):
            try:
                with opener(url, timeout=timeout) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code < 500 and exc.code not in (418, 429):
                    break                                       # definitive answer: next host
            except (urllib.error.URLError, OSError, ValueError) as exc:     # SSL handshake / DNS / timeout / reset
                last = exc
            if k < len(backoff) - 1:
                sleep(backoff[k])
    raise last if last else RuntimeError('no URL')


def fetch_klines(symbol: str, interval: str = '1m', limit: int = 30, timeout: float = REST_TIMEOUT_S,
                 get=rest_json, end_ms: Optional[int] = None) -> list:
    """Closed candles [(open_s, o, h, l, c, close_s, quote_vol, taker_buy_quote, base_vol)] from Binance USD-M
    mainnet public REST (no key); end_ms pages back in history.  Network failure -> cached rows, else spot-proxy
    rows WITHOUT the volume fields."""
    q = f'symbol={symbol.upper()}&interval={interval}&limit={int(limit)}' + (f'&endTime={int(end_ms)}' if end_ms else '')
    key = (symbol.upper(), interval, int(limit), end_ms)
    now_ms = time.time() * 1000
    try:
        rows = get([f'{h}/fapi/v1/klines?{q}' for h in REST_HOSTS], timeout)
        out = [(k[0] / 1000.0, float(k[1]), float(k[2]), float(k[3]), float(k[4]), k[6] / 1000.0,
                float(k[7]), float(k[10]), float(k[5])) for k in rows if k[6] < now_ms]
        # + quote volume, taker-buy quote, base volume (10-10 b intraday VWAP)
        KLINE_CACHE[key] = out
        KLINE_SOURCE[key[:2]] = 'fapi'
        return out
    except Exception:
        if KLINE_CACHE.get(key):
            KLINE_SOURCE[key[:2]] = 'cache'
            return KLINE_CACHE[key]
        rows = get([f'{SPOT_PROXY}?{q}'], timeout)                          # raises if this fails too
        KLINE_SOURCE[key[:2]] = 'spot-proxy'
        return [(k[0] / 1000.0, float(k[1]), float(k[2]), float(k[3]), float(k[4]), k[6] / 1000.0)
                for k in rows if k[6] < now_ms]


def fetch_server_offset(timeout: float = REST_TIMEOUT_S, get=rest_json) -> float:
    """Binance USD-M server time minus the local clock, seconds (10-10 b: the intraday lane must not admit the
    still-open 1m candle when the PC clock runs fast)."""
    t0 = time.time()
    st = get([f'{h}/fapi/v1/time' for h in REST_HOSTS], timeout)
    return float(st['serverTime']) / 1000.0 - (t0 + time.time()) / 2.0


def fetch_funding(symbol: str, limit: int = 30, timeout: float = REST_TIMEOUT_S, get=rest_json) -> list:
    """Last funding prints [(t_s, rate)] from Binance USD-M mainnet public REST (no key)."""
    q = f'symbol={symbol.upper()}&limit={int(limit)}'
    rows = get([f'{h}/fapi/v1/fundingRate?{q}' for h in REST_HOSTS], timeout)
    return sorted((int(x['fundingTime']) / 1000.0, float(x['fundingRate'])) for x in rows)


TREND_BAR_LIMIT = {'1d': 150, '4h': 500, '1h': 200}
TREND_DIR = ROOT / 'data'


def trend_fetch(symbols, bar: str, klines=None, funding=None) -> dict:
    """Closed bars + recent funding for the trend lane (public REST; injectable for tests)."""
    klines = klines or fetch_klines
    funding = funding or fetch_funding
    bars, fund = {}, {}
    for s in symbols:
        rows = klines(s, bar, TREND_BAR_LIMIT.get(bar, 300))
        bars[s] = {'t': [r[0] for r in rows], 'o': [r[1] for r in rows], 'h': [r[2] for r in rows],
                   'l': [r[3] for r in rows], 'c': [r[4] for r in rows]}
        if rows and all(len(r) >= 8 for r in rows):            # 10-09 (ee) volume + CVD for the regime gate
            bars[s]['v'] = [r[6] for r in rows]
            bars[s]['tb'] = [r[7] for r in rows]
        try:
            fund[s] = funding(s)
        except Exception:
            fund[s] = []
    return {'bars': bars, 'funding': fund}


def fetch_klines_5m(symbol: str, limit: int = 40, timeout: float = REST_TIMEOUT_S) -> list:
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
        self.carry_state = None
        self.carry_sim_state = None

    def profile(self, mode: str) -> Profile:
        p = replace(PROFILES[mode], **micro_capital_env(self.env))      # 11-09: MICRO_CAPITAL_MODE / _EQUITY_MAX ...
        if p.micro_capital == 'on' and p.trend_lane != 'off':
            p = replace(p, trend_lane='off')            # the 4H lane sizes by % of a 5 000 U wallet: not a micro account
        sym = self.symbols.get(mode)
        if sym and sym != p.symbol:
            p = replace(p, symbol=sym, qty=SYMBOL_QTY.get(sym, 0.0) if mode == 'demo' else p.qty)
        if mode != self.start_mode:                       # CLI overrides belong to the mode they were typed for
            return p
        return replace(p, **{k: v for k, v in self.overrides.items() if v is not None})

    def scan_syms(self) -> tuple:
        """Symbols of the demo scanner: BTC / SOL / DOGE, plus the micro coins when the micro rotation is forced on."""
        p = self.profile(self.mode)
        return scan_universe(p.micro_capital == 'on' and p.micro_rotation, p.micro_symbols)

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
        syms = self.scan_syms()
        feeds = {s: msig.MicroFeed(s, msig.config_for(s)) for s in syms}
        profiles = {s: self.profile_for(mode, s) for s in syms}
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
                snap['trend_lane'] = getattr(self, 'trend_state', None)
                snap['burst_lane'] = {s_: ln.last for s_, ln in (getattr(self, 'burst', None) or {}).items()}
                snap['intraday_lane'] = {s_: ln.last for s_, ln in (getattr(self, 'intraday', None) or {}).items()}
                snap['stat_arb_lane'] = self.stat_arb.view() if getattr(self, 'stat_arb', None) is not None else None
                snap['funding_carry'] = getattr(self, 'carry_state', None)
                snap['carry_sim'] = getattr(self, 'carry_sim_state', None)     # 11-09f: accumulated simulated funding yield
                snap['autonomy'] = self.autonomy.view() if getattr(self, 'autonomy', None) is not None else None
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
        if any(x.p.trend_lane in ('paper', 'active') for x in self.engines.values()):
            self.tasks.append(asyncio.create_task(self.trend_loop()))
        if any(x.p.trend_lane == 'active' for x in self.engines.values()):
            from core.trend_executor import TrendExecutor
            self.trend_exec = TrendExecutor(self.engines, ROOT / 'data' / 'trend_active.json',
                                            ROOT / 'data' / 'trend_trades.jsonl')
            for s_, x in self.engines.items():
                x.trend_owned = s_ in self.trend_exec.state
            self.tasks.append(asyncio.create_task(self.trend_active_loop()))
        else:
            self.trend_exec = None
        self.burst = {}
        for s_, x in self.engines.items():                    # 10-10 lane 2: sub-minute burst scalper
            if x.p.burst_lane != 'off' and s_ in x.p.burst_symbols:
                from core.burst_lane import BurstLane
                self.burst[s_] = BurstLane(x, ROOT / 'data')
                self.tasks.append(asyncio.create_task(self.burst_loop(self.burst[s_])))
        self.intraday = {}
        for s_, x in self.engines.items():                    # 10-10 b lane 3: intraday 5m / 15m micro-swing
            if x.p.intraday_lane != 'off' and s_ in x.p.intraday_symbols:
                from core.intraday_lane import IntradayLane
                self.intraday[s_] = IntradayLane(x, ROOT / 'data')
                self.tasks.append(asyncio.create_task(self.intraday_loop(self.intraday[s_])))
        self.stat_arb = None
        if any(getattr(x.p, 'stat_arb_lane', 'off') != 'off' for x in self.engines.values()) and len(self.engines) >= 2:
            from core.stat_arb_lane import StatArbLane
            sa_mode = next((x.p.stat_arb_lane for x in self.engines.values() if x.p.stat_arb_lane != 'off'), 'shadow')
            self.stat_arb = StatArbLane(self.engines, ROOT / 'data', mode=sa_mode)
            self.tasks.append(asyncio.create_task(self.stat_arb_loop(self.stat_arb)))

        self.carry_state = None
        if any(getattr(x.p, 'funding_shadow', False) for x in self.engines.values()):
            self.tasks.append(asyncio.create_task(self.funding_loop()))

        if any(getattr(x.p, 'autonomy', 'off') != 'off' for x in self.engines.values()):
            from core.autonomy import Autonomy
            self.autonomy = Autonomy(ROOT / 'data', ROOT / 'config', LEDGER)
            self.autonomy.attach(self.engines)
            self.tasks.append(asyncio.create_task(self.autonomy_loop()))
        else:
            self.autonomy = None
        for x in self.engines.values():
            self.tasks += [asyncio.create_task(self.seed_macro(x)), asyncio.create_task(self.seed_bars(x)),
                           asyncio.create_task(self.sync_loop(x)),
                           asyncio.create_task(self.vbook_loop(x)), asyncio.create_task(self.decide_loop(x))]
        return e

    async def burst_loop(self, lane, once: bool = False) -> dict:
        """10-10 lane 2: one BurstLane pass every burst_every_s; errors are reported, never fatal."""
        while True:
            try:
                await lane.step()
            except Exception as exc:
                lane.e.event(f'BURST error {type(exc).__name__}: {exc}'[:200])
            if once:
                return lane.last
            await asyncio.sleep(lane.e.p.burst_every_s)

    async def intraday_loop(self, lane, once: bool = False) -> dict:
        """10-10 b lane 3: one IntradayLane pass every intraday_every_s (it fetches only after a new 1m close)."""
        while True:
            try:
                await lane.step()
            except Exception as exc:
                lane.e.event(f'INTRADAY error {type(exc).__name__}: {exc}'[:200])
            if once:
                return lane.last
            await asyncio.sleep(lane.e.p.intraday_every_s)

    async def stat_arb_loop(self, lane, once: bool = False) -> dict:
        """Stat-Arb Cointegration loop across active pairs."""
        while True:
            try:
                await lane.step()
            except Exception as exc:
                if self.engines:
                    next(iter(self.engines.values())).event(f'STAT_ARB error {type(exc).__name__}: {exc}'[:200])
            if once:
                return lane.last
            await asyncio.sleep(getattr(lane, 'every_s', 1.0))

    async def funding_loop(self, once: bool = False, fetch=None, fetch_prints=None) -> Optional[dict]:
        """10-16 (b): every funding_every_s refresh the shadow carry book from public funding quotes; errors are kept
        in the snapshot (stale quotes are never shown as current), never fatal."""
        from core import funding_executor as fx
        fetch = fetch or fx.fetch_quotes
        while True:
            e0 = next(iter(self.engines.values()), None)
            try:
                quotes = await asyncio.to_thread(fetch)
                now = time.time()
                # 10-16 (c): size from the synced wallet every pass (the first pass ran before the account sync and
                # froze the book at mock_balance: 116.67 U/leg instead of ~574); mock only until the wallet is known
                acct = (e0.account or {}) if e0 else {}
                wallet = float(acct.get('wallet') or 0.0) or float((e0.p.mock_balance if e0 else 0.0) or 0.0)
                book = getattr(self, 'carry_book', None)
                if book is None:
                    book = self.carry_book = fx.ShadowCarryBook(wallet)
                    self.carry_t = now
                book.capital = wallet or book.capital
                book.accrue(now - getattr(self, 'carry_t', now))
                self.carry_t = now
                intents = book.step(quotes)
                self.carry_state = fx.carry_view(book, quotes, intents, now)
                for it in intents:
                    if e0:
                        e0.event(f'CARRY SHADOW {it["kind"]} {it["coin"]} short {it["short"]} / long {it["long"]} '
                                 f'{it["spread_8h_bps"]:+.2f} bps/8h · {it["why"]}')
                if e0 and getattr(e0.p, 'carry_sim', False):
                    from research.carry_lane import CarrySim
                    sim = getattr(self, 'carry_sim', None)
                    persist = getattr(e0.p, 'carry_sim_persist', False)
                    if sim is None:
                        # 11-10 (e): funding paid at the venues' settlement instants (HL hourly, BN 8 h), net-APR entry floor
                        # 11-10 (h): no rotation before a pair's break-even hours; (i): a flip must last before it closes
                        sim = self.carry_sim = CarrySim(wallet or 1000.0, min_net_apr_pct=e0.p.carry_min_net_apr_pct,
                                                        settlement='discrete', hold_breakeven=e0.p.carry_hold_breakeven,
                                                        flip_persist_s=e0.p.carry_flip_persist_s)
                        saved = load_carry_state(getattr(e0, 'ledger', None)) if persist else None
                        if saved is not None:                             # 11-10 (k): the book of the previous process
                            prints = await asyncio.to_thread(carry_gap_prints, CarrySim.offline_windows(saved, time.time()),
                                                             fetch_prints or fx.fetch_funding_prints)
                            sim.restore(saved, prints)
                    self.carry_sim_state = await sim.step(quotes)
                    if persist:
                        save_carry_state(getattr(e0, 'ledger', None), sim.snapshot())
                    if getattr(e0.p, 'carry_sim_log', False):            # 11-10 (j): rows a restart does not erase
                        write_carry_rows(getattr(e0, 'ledger', None), sim.records)
                    for message in self.carry_sim_state.get('new_events', ()):
                        e0.event(message)
                else:
                    self.carry_sim_state = None
            except Exception as exc:
                self.carry_state = dict(self.carry_state or {}, error=f'{type(exc).__name__}: {exc}'[:160],
                                        error_t=time.time())
            if once:
                return self.carry_state
            await asyncio.sleep(e0.p.funding_every_s if e0 else 300.0)

    async def autonomy_loop(self, every_s: float = 5.0, threaded: bool = True) -> None:
        """10-09 (cc): every 5 s poll config/live_tunables.json (mtime), run the capital router; every 15 min or
        3 new closed trades run one dreaming pass (ledger read-only) whose directives come back through the file."""
        a = self.autonomy
        while True:
            try:
                a.poll()
                a.route()
                if a.dream_due():
                    if threaded:
                        await asyncio.to_thread(a.dream)
                    else:
                        a.dream()
                    a.poll()
            except Exception as exc:
                e0 = next(iter(self.engines.values()), None)
                if e0 is not None:
                    e0.event(f'autonomy loop error {type(exc).__name__}: {exc}'[:200])
            await asyncio.sleep(every_s)

    async def trend_active_loop(self, fetch=None, once: bool = False) -> dict:
        """10-09 (dd): the 4H trend lane on the DEMO venue (core/trend_executor.py); acts once per new closed bar."""
        from core.trend_executor import ACTIVE_BAR
        ex = self.trend_exec
        e0 = next(iter(self.engines.values()))
        syms = [s for s in scan_universe(True, e0.p.micro_symbols) if s in self.engines] or [e0.p.symbol]
        self.trend_state = getattr(self, 'trend_state', {}) or {}
        while True:
            try:
                data = await asyncio.to_thread(fetch or trend_fetch, syms, ACTIVE_BAR)
                if e0.p.trend_conv_alloc_pct:                 # 10-09 (ff) conviction needs closed 1H + 1D bars
                    data['tf'] = {}
                    for b in ('1h', '1d'):
                        try:
                            data['tf'][b] = (await asyncio.to_thread(fetch or trend_fetch, syms, b))['bars']
                        except Exception as exc:              # UNKNOWN -> base size, never a bigger one
                            e0.event(f'TREND {b} fetch failed ({type(exc).__name__}) -> no conviction size')
                await ex.step(data)
                self.trend_state['4h_active'] = ex.view()
            except Exception as exc:
                self.trend_state['4h_active'] = {**ex.view(), 'error': f'{type(exc).__name__}: {exc}'[:160]}
                e0.event(f'TREND 4H error {type(exc).__name__}: {exc}'[:200])
            if once:
                return self.trend_state
            await asyncio.sleep(e0.p.trend_active_every_s)

    async def trend_loop(self, fetch=None, once: bool = False, root: Optional[Path] = None) -> dict:
        """10-09 PAPER trend lane next to the micro engine: every trend_every_s, rebalance a paper book per bar
        size on the last CLOSED bar.  Never touches the venue.  State: self.trend_state (telemetry 'trend_lane')."""
        from core import trend_lane as tl
        root = root or TREND_DIR
        e0 = next(iter(self.engines.values()))
        syms = [s for s in scan_universe(True, e0.p.micro_symbols) if s in self.engines] or [e0.p.symbol]
        cfgs = {b: tl.TrendConfig(bar=b) for b in e0.p.trend_bars
                if not (e0.p.trend_lane == 'active' and b == '4h')}          # 10-09 dd: 4h trades for real
        books = {b: tl.PaperBook.load(root / f'trend_paper_{b}.json', e0.p.trend_equity) for b in cfgs}
        self.trend_state = getattr(self, 'trend_state', {}) or {}
        while True:
            for b, cfg in cfgs.items():
                try:
                    data = await asyncio.to_thread(fetch or trend_fetch, syms, b)
                    res = tl.paper_step(books[b], data['bars'], data['funding'], cfg)
                    root.mkdir(parents=True, exist_ok=True)
                    (root / f'trend_paper_{b}.json').write_text(books[b].to_json(), encoding='utf-8')
                    if res['fills']:
                        with open(root / f'trend_paper_{b}.jsonl', 'a', encoding='utf-8') as fh:
                            for f in res['fills']:
                                fh.write(json.dumps({**f, 'bar': b, 'code': CODE_VERSION}) + '\n')
                    self.trend_state[b] = {'mode': 'PAPER', 'equity': res['equity'], 'fees': books[b].fees,
                                           'targets': {s: round(v['w'], 4) for s, v in res['targets'].items()},
                                           'fills': len(res['fills']), 'updated': time.time(), 'error': ''}
                    for f in res['fills']:
                        e0.event(f'TREND {b} PAPER {f["side"]} {f["symbol"]} {f["qty"]:.4g} @ {f["price"]:.6g} '
                                 f'-> weight {f["target_w"]:+.3f} (no order sent)')
                except Exception as exc:
                    self.trend_state[b] = {**self.trend_state.get(b, {}), 'mode': 'PAPER',
                                           'error': f'{type(exc).__name__}: {exc}'[:160]}
            if once:
                return self.trend_state
            await asyncio.sleep(e0.p.trend_every_s)

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
