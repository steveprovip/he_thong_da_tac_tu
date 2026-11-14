@echo off
setlocal EnableExtensions
rem ============================================================================
rem run.bat - GROKTOPUS 2-MODE LAUNCHER
rem   run.bat demo  -> Web UI + bot giao dich tai khoan DEMO Binance Futures
rem                    (key BINANCE_TESTNET_* trong .env.local; khong co key = MOCK gia live)
rem   run.bat live  -> Web UI + bot giao dich MAINNET - phai GO "LIVE" de xac nhan
rem   run.bat test  -> python -m pytest -q
rem   run.bat micro -> Web UI + bot tren tai khoan DEMO chay logic vi nho (MICRO_CAPITAL_MODE=on, bankroll gia lap 15 U): xoay von
rem                    giua DOGE/XRP/ADA/1000PEPE (BTC/SOL chi quan sat), lenh ~12-17 U, lo <= 0.05 U/lenh, IOC lai (MICRO_IOC=on),
rem                    harvester chop chi ghi shadow, runner cho lenh trend diem >= 80 %% - tien ao, khong cham mainnet
rem   Web UI: http://127.0.0.1:8765  (nut BAN LENH TEST, doi che do, dong vi the)
rem ============================================================================
set "PY=%LocalAppData%\Programs\Python\Python310\python.exe"
if not exist "%PY%" set "PY=python"
cd /d "%~dp0"
set "GROKTOPUS_ARM_LIVE="
if /i "%~1"=="demo" goto :DEMO
if /i "%~1"=="live" goto :LIVE
if /i "%~1"=="test" goto :TEST
if /i "%~1"=="micro" goto :MICRO

:MENU
echo.
echo  ==========================================================================
echo   GROKTOPUS 2.0  ^|  CAC CHE DO GIAO DICH
echo  ==========================================================================
echo   [1] DEMO       - Binance Demo Futures (BTC/SOL/DOGE, von ao 5000 U)
echo   [2] LIVE       - Binance Mainnet (DOGEUSDT, von ^<= 3.85 U, lo ^<= 0.05 U/lenh)
echo   [3] Chay kiem thu (pytest)
echo   [4] DEMO MICRO - Binance Demo logic vi nho 15 U (xoay DOGE/XRP/ADA/PEPE, lo ^<= 0.05 U, harvester shadow)
echo   [0] Thoat
echo  ==========================================================================
set "C="
set /p "C=Chon: "
if "%C%"=="1" goto :DEMO
if "%C%"=="2" goto :LIVE
if "%C%"=="3" goto :TEST
if "%C%"=="4" goto :MICRO
if "%C%"=="0" exit /b 0
goto :MENU

:DEMO
call scripts\TAT_GIAO_DIEN.bat >nul 2>&1
start "" "http://127.0.0.1:8765"
"%PY%" -m core.engine --mode demo
pause
exit /b %ERRORLEVEL%

:MICRO
rem Demo account only (--mode demo): the 5 U small-account rules forced on a big virtual wallet. Scanner mode (no
rem --no-scan): BTC/SOL observe, DOGE/XRP/ADA/1000PEPE rotate, one position. MICRO_IOC=on = hybrid IOC rehearsal
rem (invariant I5 prints an INVARIANT WARN on demo; an armed live engine refuses it).
set "MICRO_CAPITAL_MODE=on"
set "MICRO_IOC=on"
rem 11-09e: MICRO_HARVESTER=on = chop-lane maker harvester (KER < 0.28, TP 0.15 / SL 0.15 %) rehearsal; Demo only (a live
rem profile with it on is an invariant error). Replay of the sister scalper is negative: delete this line to go back to shadow.
rem 11-10 (b): set back to shadow - the mainnet-tape study (plan_micro_adverse_selection.md section 9) found -6..-7 bps per fill, no filter, no bracket.
rem MICRO_EXIT_POLICY below only acts on harvest positions, so it is dormant while this is shadow. Use "on" only to rehearse the plumbing.
rem 11-10 (e): shadow. No harvest order is sent; every would-be harvest still writes its quote markouts to data\harvest_markouts.jsonl.
set "MICRO_HARVESTER=shadow"
rem 11-13: MICRO_NET_EV_GATE=on = a micro AUTO entry is sent only when the micro lane's NET bps per trade has LCB95 > 0 over
rem >= 30 settled real (or virtual edge_shadow) trades. Micro ledger 2026-11-07..10: 21 trades, -5.5 +- 3.4 bps (< 30 = UNPROVEN),
rem so it refuses and runs virtual trades instead (docs/research_upgrade/AUTOPSY_REPORT_2026.md). Delete this line for shadow (log only).
set "MICRO_NET_EV_GATE=on"
rem 11-10 (e): MICRO_SNIPER_RUNNER=on = a swarm trend / override entry with raw score and consensus >= 80 %% is managed as a runner:
rem TP order at +2.5 %% (a cap), no 120 s / 180 s time-decay eject, trail max(0.20 %%, 2 x ATR 1m) behind the peak from +0.50 %%.
rem Entry gates, exchange stop, RATCHET / RATCHET2 and the 0.05 U loss cap are unchanged. Unvalidated: no ledger trade has reached
rem MFE +0.5 %% (0 of 129, max 0.441 %%). Delete this line to go back to the bracket TP and the eject timers.
set "MICRO_SNIPER_RUNNER=on"
rem 11-10 (e): rehearsed account of 15 U (was the 5 U default): lot = 15 U x (1 + 5 x realized micro PnL), capped by the 0.05 U loss
rem at the stop (about 17 U at SL 0.22 %% with a maker entry, 15.6 U with an IOC entry), daily kill 0.15 U, 3-loss / 24 h halt unchanged.
rem Virtual Demo money only; the live profile never reads this (it sizes from the real wallet). Delete this line to go back to 5 U.
set "MICRO_CAPITAL_BANKROLL=15"
rem 11-10: MICRO_EXIT_POLICY=patient = harvest positions keep their TP (BE timer 180 s, scratch only after MFE >= 0.09 %, a negative
rem touch only on toxic flow); unvalidated (docs/harness/plan_micro_asymmetry_cure.md). Delete this line to go back to the 45 s scratch.
set "MICRO_EXIT_POLICY=patient"
rem 11-11: liberate entry gates; prevent 90-min turnover freeze while keeping flow alignment
rem 11-13 owner decision: MICRO_OFI_MIN=0.45 rejected, the profile floor OFI >= 0.65 stays (the parser now refuses values under 0.65;
rem the 10 MICRO ALIGN refusals under 0.65 moved -36 +- 21 bps against the signal in 60 min, AUTOPSY_REPORT_2026.md).
set "MICRO_MIN_EDGE_BPS=7.0"
call scripts\TAT_GIAO_DIEN.bat >nul 2>&1
start "" "http://127.0.0.1:8765"
"%PY%" -m core.engine --mode demo
pause
exit /b %ERRORLEVEL%

:LIVE
echo.
echo  [CANH BAO] CHE DO LIVE DAT LENH BANG TIEN THAT tren fapi.binance.com
echo  Gioi han cung: ky quy ^<= 3.85 U, lo toi da 0.05 U/lenh, kill 0.15 U/ngay, Post-Only GTX.
set "CONFIRM="
set /p "CONFIRM=Go chu LIVE (in hoa) de xac nhan, Enter de huy: "
if not "%CONFIRM%"=="LIVE" (
    echo  Da huy. Khong co lenh nao duoc gui.
    pause
    exit /b 1
)
set "GROKTOPUS_ARM_LIVE=%CONFIRM%"
call scripts\TAT_GIAO_DIEN.bat >nul 2>&1
start "" "http://127.0.0.1:8765"
"%PY%" -m core.engine --mode live
pause
exit /b %ERRORLEVEL%

:TEST
"%PY%" -m pytest -q
if /i "%~1"=="test" exit /b %ERRORLEVEL%
pause
goto :MENU
