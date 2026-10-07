@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem ==============================================================================
rem CHAY_DOGE_1M.bat - 1-Click: bat Web UI (neu chua chay) roi chay Shadow Harvester
rem   Khong dat bat ky lenh that nao len san. Ctrl+C de dung bat ky luc nao.
rem ==============================================================================
set "ROOT=%~dp0..\..\"
cd /d "%ROOT%"
set "PORT=8765"
set "URL=http://127.0.0.1:%PORT%"

rem --- 1. Tim Python ---
set "PY=%LocalAppData%\Programs\Python\Python310\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" --version >nul 2>&1
if errorlevel 1 (
    echo [LOI] Khong tim thay Python. Cai Python 3.10+ roi chay lai file nay.
    pause
    exit /b 1
)

rem --- 2. Bat Web UI neu cong chua LISTENING ---
call :IS_UP
if not "!UP!"=="1" (
    echo [HE THONG] Dang khoi dong Web UI Server tren cong %PORT%...
    start "GROKTOPUS_UI" /min "%PY%" "%ROOT%core\web_ui_server.py" --port %PORT%
    rem Cho toi da 10 giay cho tien trinh thuc su lang nghe, khong doan mo bang timeout co dinh
    for /l %%i in (1,1,10) do (
        timeout /t 1 /nobreak >nul
        call :IS_UP
        if "!UP!"=="1" goto :UI_READY
    )
    echo [CANH BAO] Web UI chua len sau 10 giay. Engine van chay; mo TAT_GIAO_DIEN.bat roi thu lai.
) else (
    echo [OK] Web UI da chay san tren cong %PORT%.
)
:UI_READY

echo.
echo ==============================================================================
echo   SHADOW HARVESTER - DOGE 1m FLUSH  ^(VON 100K VND / STOP 5 CENTS^)
echo.
echo   GIAO DIEN REAL-TIME:  %URL%
echo.
echo   3 nhanh chay song song tren cung diem vao, chi khac quy tac stop:
echo     BASELINE   SL co dinh -20.44 bps
echo     BE_15_10   MFE +15 bps  -^> doi SL len +10 bps
echo     BE_20_10   MFE +20 bps  -^> doi SL len +10 bps
echo ==============================================================================
echo.
start "" "%URL%"
"%PY%" -m research.flush_shadow %*
echo.
echo [DA DUNG] Engine thoat. Web UI van chay - dung TAT_GIAO_DIEN.bat de tat han.
pause
exit /b 0

:IS_UP
rem Chi khop cot dia chi CUC BO dang LISTENING, tranh bat nham ket noi den tu cong %PORT%
set "UP="
for /f "tokens=2,4" %%a in ('netstat -ano -p tcp ^| findstr /r /c:"LISTENING"') do (
    if "%%a"=="0.0.0.0:%PORT%" set "UP=1"
    if "%%a"=="127.0.0.1:%PORT%" set "UP=1"
    if "%%a"=="[::]:%PORT%" set "UP=1"
)
exit /b 0
