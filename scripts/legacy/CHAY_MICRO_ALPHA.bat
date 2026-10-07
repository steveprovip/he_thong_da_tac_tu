@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem ==============================================================================
rem CHAY_MICRO_ALPHA.bat - 1-Click: Web UI + Multi-Asset Micro-Alpha SHADOW Scanner
rem   8 cap: DOGE 1000PEPE SOL XRP SUI BNB (+ BTC ETH lam tin hieu dan dat)
rem   Trigger tick-level: OBI L5 >= 0.55 / CVD 60s burst / Lead-lag BTC-ETH, can 2/3
rem   KHONG dat bat ky lenh that nao. Ctrl+C hoac nut EMERGENCY STOP de dung.
rem   Tham so tuy chon, vi du:  CHAY_MICRO_ALPHA.bat --seconds 900
rem                             CHAY_MICRO_ALPHA.bat --venue demo
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

call :IS_UP
if not "!UP!"=="1" (
    echo [HE THONG] Dang khoi dong Web UI Server tren cong %PORT%...
    start "GROKTOPUS_UI" /min "%PY%" "%ROOT%core\web_ui_server.py" --port %PORT%
    for /l %%i in (1,1,10) do (
        timeout /t 1 /nobreak >nul
        call :IS_UP
        if "!UP!"=="1" goto :UI_READY
    )
    echo [CANH BAO] Web UI chua len sau 10 giay. Engine van chay.
) else (
    echo [OK] Web UI da chay san tren cong %PORT%. Neu nut EMERGENCY STOP khong dung duoc engine,
    echo      chay TAT_GIAO_DIEN.bat roi mo lai file nay de nap ban server moi.
)
:UI_READY

echo.
echo ==============================================================================
echo   MULTI-ASSET MICRO-ALPHA SHADOW  (VON 3.85 USDT / KILL 0.05 USDT / 2x-3x)
echo   GIAO DIEN: %URL%
echo   Vao: GTX post-only tai bid/ask  ^|  Ra: TP +35 net / SL -20 / BE 15-20 -^> +1 / 180s
echo   Nhat ky lenh: data\micro_scanner_trades.jsonl
echo ==============================================================================
echo.
start "" "%URL%"
"%PY%" -m research.micro_scanner %*
echo.
echo [DA DUNG] Engine thoat. Web UI van chay - dung TAT_GIAO_DIEN.bat de tat han.
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
