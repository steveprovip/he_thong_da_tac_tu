@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem ==============================================================================
rem CHAY_ALPHA_RADAR.bat - 1-Click: Web UI + Daily Alpha Radar (CHI DOC, KHONG DAT LENH)
rem   R1: Binance volume surge >= 3x + breakout + RSI  ->  top 3 + ke hoach 3.85 USDT
rem   R2: Polymarket arbitrage negRisk / model edge crypto / macro odds
rem   Mac dinh quet lai moi 5 phut. Tham so:  --loop 0 (quet 1 lan)  --no-events  --surge 2.5
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
    echo [CANH BAO] Web UI chua len sau 10 giay.
) else (
    echo [OK] Web UI da chay. Neu khong thay bang R1/R2: TAT_GIAO_DIEN.bat roi mo lai file nay.
)
:UI_READY
echo.
echo ==============================================================================
echo   DAILY ALPHA RADAR  -  %URL%
echo   Bao cao: artifacts\alpha_radar\   Du lieu: data\alpha_radar.json
echo   Lan dau chay backtest 20 cap x 5 ngay (~1 phut), sau do cache 6 gio.
echo ==============================================================================
start "" "%URL%"
set "ARGS=%*"
if "%ARGS%"=="" set "ARGS=--loop 300"
"%PY%" -m research.radar_runner %ARGS%
echo.
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
