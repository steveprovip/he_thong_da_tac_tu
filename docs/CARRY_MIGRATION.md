# Spot–perp migration / M0

Baseline: `python -m pytest -q`: **272 passed (32.46s)**. Existing dirty
working-tree edits are preserved. Scope is a separate REST carry runtime;
the legacy 0.5s pipeline and its research interfaces remain compatible.

## Checklist

- M1: new `live/binance_spot_client.py`, pair gateway in
  `live/execution_gateway.py`, deterministic exchange fault tests.
- M2: pair models and HMAC authority, strict eight-vote carry policy,
  funding planner with actual settlement intervals and both-leg costs.
- M3: `core/carry_runtime.py`, opt-in `--carry` dispatch in `core/runtime.py`,
  REST 60s scan, private fill events, 30s isolated-margin watchdog.
- M4: attributed funding + both-leg cashflow/wallet reconciliation,
  weekly report, >=15 real behavior tests.
- M5: launcher entries, full regression run, 60s public shadow smoke.

## Accounting conventions

`basis = spot - perp` in USDT per coin. For matched quantity q:
`basis_pnl = q * (basis_exit - basis_entry)`. Defining basis as perp-spot
would require the opposite sign. Funding is summed from actual settlements;
current annualized funding is a scenario, never a guaranteed return.

Perp fills first. With BNB commission, net spot coins equal executed coins.
With base-asset commission, a spot buy of q yields less than q: gross up by
`1/(1-fee)` then reconcile actual commissions and reduce residual futures
exposure where necessary. Never silently declare mismatched legs hedged.
The 100ms objective measures fill receipt to hedge dispatch; network completion
cannot be guaranteed. A 5s hedge deadline initiates emergency unwind.

## Rollback

Pre-migration copies (including pre-existing uncommitted changes) are in
`artifacts/carry_migration_baseline/`, manifest included. Stop the carry
runtime and verify both venue legs closed before restoring these exact eight
files. Remove only migration-created files listed in the final diff if wanted.
Do not use `git reset --hard`: it would discard the user's earlier work.
Retain execution journals and reconciliation reports. Default shadow sends
no signed order or wallet mutation. Mainnet requires both explicit `--arm`
and the exact confirmation text; environment arming alone is ignored.

## Windows operations

From PowerShell:

```powershell
Set-Location 'F:\he_thong_da_tac_tu_2'
python -m pytest -q
python -m core.runtime --carry --mode shadow --seconds 60
python -m core.runtime --carry --mode shadow
python -m core.runtime --carry --mode testnet --notional 100 --leverage 1
.\run.bat
```

Testnet requires four existing environment/.env.local entries:
`BINANCE_TESTNET_API_KEY`, `BINANCE_TESTNET_SECRET_KEY`,
`BINANCE_SPOT_TESTNET_API_KEY`, `BINANCE_SPOT_TESTNET_SECRET_KEY`.
Spot Testnet and Futures Testnet have separate wallets and credentials.
Fund both test wallets, enable BNB fee payment where supported, and retain
enough BNB for twice the estimated round-trip spot fees. Unavailable commission
or BNB support blocks entry; the runtime does not fabricate a successful check.
The default 100 USDT cap may be below the exchange minimum after lot rounding;
that produces a BLOCK, not an automatic increase in capital.

Mainnet credentials use `BINANCE_MAINNET_API_KEY`,
`BINANCE_MAINNET_SECRET_KEY`, `BINANCE_SPOT_MAINNET_API_KEY`, and
`BINANCE_SPOT_MAINNET_SECRET_KEY`. The CLI additionally requires `--arm` and
`--confirm "I UNDERSTAND SPOT PERP MAINNET RISK"`. No mainnet order was sent
during this migration. Environment variable ARM_LIVE_EXECUTION is ignored
by the carry runtime. There is no Mainnet launcher menu option.

Ctrl+C cooperatively cancels entries and closes both owned legs. Never use the
legacy futures-only kill switch for a carry pair. On restart, the pair journal
reconstructs venue orders and closes previous carry exposure before new entries.
Run only one carry process per mode; an OS file lock rejects duplicates.
Execution failures that cannot be reconciled remain UNWINDING and block entry;
they are not reported as CLOSED. Retain the journal until venue reconciliation.

## Outputs and limits

- `data/carry/<mode>.sqlite`: order intents, acknowledgements, pair state,
  attribution, account reconciliation, private execution identities. No L2/ticks.
- `data/carry/<mode>-weekly.md`: realized complete cycles grouped by UTC closing
  week; open positions show liquidation MTM separately. Unmatched wallets and
  incomplete histories cannot qualify for realized-profit totals.
- `artifacts/carry_migration.diff`: complete patch against the saved M0 worktree,
  excluding unrelated pre-existing changes. Source ZIP contains complete files.
- REST market scans every 60 seconds, bounded market-data batch timeout 50s;
  each new-entry snapshot must be younger than 60s. Account watchdog every 30s
  after its preceding check. Emergency execution may delay the next check.
- Futures V3 balances join accountConfig and symbolConfig for permissions,
  isolated mode and leverage. The watchdog adds only free Futures wallet cash,
  capped at 25% of the pair's entry notional; otherwise closes the pair.
- Funding histories normalize 1h/4h/8h intervals and tolerate at most 1s timestamp
  jitter. Three negative *settlements* trigger exit, not three repeated polls.
- Fees use account endpoints with conservative baseline floors. Third-asset
  commissions use a documented REST 1m-open USDT valuation: these conversions
  are estimates, while native fee quantities and wallet reconciliation are exact.
- The <100ms hedge-dispatch target is instrumented from fill receipt, including
  journaling. Local deterministic tests verify ordering and failure handling;
  they do not prove Internet/venue execution under 100ms. A 5s hedge timeout
  initiates cancel-and-unwind; venue/network outages can prevent completion.
- This delivery proves software behavior and public Shadow operation. It does
  not prove positive realized alpha, private Testnet settlement, or Mainnet
  readiness under real exchange/network faults. No fabricated fills are used.

## API references checked

- [Spot commission and BNB](https://github.com/binance/binance-spot-api-docs/blob/master/faqs/commission_faq.md)
- [Spot authenticated WebSocket](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/ws-api/user-data-stream)
- [Futures V3 account, accountConfig, symbolConfig](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account)

## Acceptance evidence

| Requirement | Implementation | Evidence |
|---|---|---|
| Preserve 272 tests | Legacy interfaces retained; new opt-in dispatch | Final full suite: 335 passed, including 63 carry tests |
| Coin matching and BNB | Actual commissionAsset/qty, gross-up fallback, exact residual check | Base fee, BNB fee, missing commissions, tiny partial, delta accounting tests |
| Perp-first GTX and partial fills | PairExecutionGateway and private order event callback | No Spot before fill; .4 + .6 fills; duplicate fill replay |
| Retry/unwind/restart | Journal-before-POST; known-ID recovery; cancel race reconciliation | Three IOC retries; lost buy/sell ACK; timeout; restart; stop reduction |
| Strict consensus and pair HMAC | PairIntent, PairAuthToken, eight carry_votes | 76.8% policy; tampered symbols/replay denied; basis/BNB/margin veto |
| Funding economics | SpotPerpCarryPlan, actual intervals, all-leg fee floors | APR, horizon, OI, spread, net hurdle, timestamp jitter tests |
| Isolated margin 1x–2x | V3/accountConfig/symbolConfig join; capped top-up | V3 schema test; >65% unwind; bounded rebalance test |
| No L2 carry loop | core.carry_runtime; only REST public snapshots and private account events | Public Shadow log, dispatch test, source inspection |
| Exit conditions | Distinct negative settlements, horizon, basis, reconciled profit target | Negative-poll dedup and wallet-gated profit target tests |
| Cashflow reconciliation | PairReconciler, pair_cashflow, wallet_reconciliation | Attributed order/income integration; FX/missing settlement/external deposit tests |
| Weekly reporting | Separate realized cycles and open liquidation MTM | Weekly grouping test; Shadow report explicitly has zero realized cycles |
| Windows launcher | run.bat + Shadow/Testnet menu | Command/arming/batch-path tests |

Full test log: `artifacts/carry_tests_final.log` (**335 passed in 32.90s**).
Shadow evidence: `artifacts/carry_shadow_final.log`. The earlier successful
smoke completed in 60.02s with zero positions; the final smoke revalidates the
last freshness checks. No signed Testnet/Mainnet trade was executed here.
