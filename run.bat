@echo off
setlocal EnableExtensions
rem ============================================================================
rem run.bat - GROKTOPUS 2-MODE LAUNCHER
rem   run.bat demo  -> Web UI + bot giao dich tai khoan DEMO Binance Futures
rem                    (key BINANCE_TESTNET_* trong .env.local; khong co key = MOCK gia live)
rem   run.bat live  -> Web UI + bot giao dich MAINNET - phai GO "LIVE" de xac nhan
rem   run.bat test  -> python -m pytest -q
rem   run.bat micro -> Web UI + bot tren tai khoan DEMO chay logic vi 5 U (MICRO_CAPITAL_MODE=on): xoay von giua
rem                    DOGE/XRP/ADA/1000PEPE (1 vi the, BTC/SOL chi quan sat), lenh 5.1-6.0 U, lo <= 0.05 U/lenh,
rem                    IOC lai (MICRO_IOC=on) - tien ao, khong cham mainnet
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
echo   [4] DEMO MICRO - Binance Demo logic vi 5 U (xoay DOGE/XRP/ADA/PEPE, 5.1-6.0 U, lo ^<= 0.05 U)
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
set "MICRO_HARVESTER=on"
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
