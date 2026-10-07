# archive_hft — 1m/tick scalping research (retired 2026-09-27)

Code is **indexed here, not moved**: 20 importers (tests, `CHAY_DOGE_1M.bat`,
`CHAY_MICRO_ALPHA.bat`, `run_launcher.py`, `research/bridge.py`, `tools/replay_micro_scanner.py`)
reference these paths; moving them would break the 649-test gate for zero behavioural gain.
New work must not build on them.

| Module | What it did | Verdict (evidence) |
|---|---|---|
| `research/flush_shadow.py` (+ `flush_regime.py`, `flush_risk.py`) | DOGE 1m flush, 3 stop branches | 0 trades in 30+ min live: 1 asset x bar-close trigger never fires in a 0.02 % range |
| `research/micro_scanner.py` | 8-asset OBI/CVD/lead-lag, GTX entry, TP +35 net / SL -20 / 180 s | Replay 3.94 h real L2 (BTC/SOL/BNB 2026-09-08): default 4 fills, mean **-22 bps**; 1-vote 61 fills, mean **-11 bps**, TP 1/60 |
| `research/l2_recorder.py`, `phan_tich/markout_l2.py` | L2 capture, maker markout | maker markout **-0.7..-1.2 bps**; nothing > 9 bps (fees 6-9 bps) |

Root cause: a 0.35 % target is the same order as round-trip cost (fees + slippage +
adverse selection on maker fills). Successor: `research/alpha_radar.py`
(targets +2 %/+5 %, cost ~0.12 %) and `research/event_radar.py` (Polymarket).
Replay artefacts: `artifacts/micro_replay/`.
