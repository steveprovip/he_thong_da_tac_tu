@echo off
setlocal EnableExtensions
rem ============================================================================
rem run.bat - GROKTOPUS 2-MODE LAUNCHER
rem   run.bat demo  -> Web UI + bot giao dich tai khoan DEMO Binance Futures
rem                    (key BINANCE_TESTNET_* trong .env.local; khong co key = MOCK gia live)
rem   run.bat live  -> Web UI + bot giao dich MAINNET - phai GO "LIVE" de xac nhan
rem   run.bat test  -> python -m pytest -q
rem   Web UI: http://127.0.0.1:8765  (nut BAN LENH TEST, doi che do, dong vi the)
rem ============================================================================
set "PY=%LocalAppData%\Programs\Python\Python310\python.exe"
if not exist "%PY%" set "PY=python"
cd /d "%~dp0"
set "GROKTOPUS_ARM_LIVE="
if /i "%~1"=="demo" goto :DEMO
if /i "%~1"=="live" goto :LIVE
if /i "%~1"=="test" goto :TEST

:MENU
echo.
echo  ==========================================================================
echo   GROKTOPUS 2.0  ^|  CHI CON 2 CHE DO
echo  ==========================================================================
echo   [1] DEMO  - Binance Demo Futures (BTCUSDT 0.001, Cross 5x)  [an toan]
echo   [2] LIVE  - Binance Mainnet (DOGEUSDT, von ^<= 3.85 U, lo ^<= 0.05 U/lenh)
echo   [3] Chay kiem thu (pytest)
echo   [0] Thoat
echo  ==========================================================================
set "C="
set /p "C=Chon: "
if "%C%"=="1" goto :DEMO
if "%C%"=="2" goto :LIVE
if "%C%"=="3" goto :TEST
if "%C%"=="0" exit /b 0
goto :MENU

:DEMO
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
