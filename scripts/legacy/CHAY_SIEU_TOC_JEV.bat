@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem ==============================================================================
rem CHAY_SIEU_TOC_JEV.bat - 1-Click: Web UI + FAST LANE (SHADOW, KHONG DAT LENH)
rem   F1: luong thanh ly Binance !forceOrder@arr -> cascade >= $50k/5s + rau nen
rem       -> lenh SHADOW nguoc chieu, TP +1%%, lo toi da 0.05 U, giu toi da 60s
rem   F2: Polymarket 15m Up/Down: gia tri hop ly tu Binance vs so lenh, sau phi taker
rem   Tham so: --cascade 30000 --tp 1.2 --min-wick 0.5 --seconds 900 --no-pm
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
    echo [OK] Web UI da chay. Khong thay bang F1/F2: TAT_GIAO_DIEN.bat roi mo lai file nay.
)
:UI_READY
start "" "%URL%"
"%PY%" -m research.fast_runner %*
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
