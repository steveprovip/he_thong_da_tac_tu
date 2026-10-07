@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem ==============================================================================
rem CHAY_AUTO_TRADER.bat - 1-Click: Web UI + Alpha Radar Auto-Trader
rem   Mac dinh: --mode demo  -> DAT LENH THAT tren tai khoan Binance DEMO
rem             (demo-fapi.binance.com, key BINANCE_TESTNET_* trong .env.local)
rem   Khong bao gio cham mainnet. Chay mo phong: CHAY_AUTO_TRADER.bat shadow
rem   v3: 1 vi the - ky quy <= 2.6 U - SL 2.0xATR, lo toi da 0.15 U/lenh - KILL 0.15 U/ngay
rem   Dung: Ctrl+C hoac nut EMERGENCY STOP -> huy lenh + dong vi the.
rem ==============================================================================
set "ROOT=%~dp0..\..\"
cd /d "%ROOT%"
set "PORT=8765"
set "URL=http://127.0.0.1:%PORT%"
set "PY=%LocalAppData%\Programs\Python\Python310\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" --version >nul 2>&1
if errorlevel 1 (
    echo [LOI] Khong tim thay Python. Cai Python 3.10+ roi chay lai file nay.
    pause
    exit /b 1
)
set "MODE=demo"
if /i "%~1"=="shadow" (
    set "MODE=shadow"
    shift
) else if /i "%~1"=="demo" (
    shift
)
set "EXTRA="
:ARGS
if "%~1"=="" goto :ARGS_DONE
set "EXTRA=!EXTRA! %1"
shift
goto :ARGS
:ARGS_DONE

call :IS_UP
if not "!UP!"=="1" (
    echo [HE THONG] Dang khoi dong Web UI Server tren cong %PORT%...
    start "GROKTOPUS_UI" /min "%PY%" "%ROOT%core\web_ui_server.py" --port %PORT%
    for /l %%i in (1,1,10) do (
        timeout /t 1 /nobreak >nul
        call :IS_UP
        if "!UP!"=="1" goto :UI_READY
    )
    echo [CANH BAO] Web UI chua len sau 10 giay.
) else (
    echo [OK] Web UI da chay. Neu nut EMERGENCY STOP khong dung duoc bot: TAT_GIAO_DIEN.bat roi mo lai.
)
:UI_READY
echo.
echo ==============================================================================
echo   RADAR AUTO-TRADER  ^|  MODE: %MODE%  ^|  %URL%
echo   Quet moi 2 phut: surge ^>= 5x + taker ^>= 60%% + pha dinh 24h + 1H/4H tren EMA50, RSI 55-75
echo   Trend: chi LONG khi 1H va 4H tren EMA50 (SHORT nguoc lai)
echo   Vao MARKET -^> SL = 2.0xATR5m (lo ^<= 0.15 U, neu rong hon thi BO QUA) -^> TP1 ^>= 2.5R (4.5%%) / TP2 8%% -^> BE sau TP1
echo   Che do cu: CHAY_AUTO_TRADER.bat demo --sl-mode money --max-loss 0.05 --surge 3
echo   Nhat ky: data\auto_trader_ledger.jsonl
if /i "%MODE%"=="demo" echo   Xem lenh: https://demo.binance.com/en/futures  (tab Positions / Open Orders)
echo ==============================================================================
start "" "%URL%"
"%PY%" -m live.radar_auto_trader --mode %MODE% !EXTRA!
echo.
echo [DA DUNG] Bot thoat. Web UI van chay - dung TAT_GIAO_DIEN.bat de tat han.
pause
exit /b 0

:IS_UP
set "UP="
for /f "tokens=2" %%a in ('netstat -ano -p tcp ^| findstr /r /c:"LISTENING"') do (
    if "%%a"=="0.0.0.0:%PORT%" set "UP=1"
    if "%%a"=="127.0.0.1:%PORT%" set "UP=1"
    if "%%a"=="[::]:%PORT%" set "UP=1"
)
exit /b 0
