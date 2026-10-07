@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem ==============================================================================
rem MO_GIAO_DIEN.bat - Bat Web UI (neu chua chay) va mo trinh duyet
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

echo ==============================================================================
echo   DANG KHOI DONG GIAO DIEN WEB OBSERVATORY (CONG %PORT%)...
echo ==============================================================================

call :IS_UP
if not "!UP!"=="1" (
    start "GROKTOPUS_UI" /min "%PY%" "%ROOT%core\web_ui_server.py" --port %PORT%
    for /l %%i in (1,1,10) do (
        timeout /t 1 /nobreak >nul
        call :IS_UP
        if "!UP!"=="1" goto :READY
    )
    echo [LOI] Web UI khong len sau 10 giay. Kiem tra cong %PORT% co bi chiem khong.
    pause
    exit /b 1
)
:READY

echo [OK] Dang mo trinh duyet den %URL% ...
start "" "%URL%"
echo ==============================================================================
echo   [HOAN TAT] GIAO DIEN REAL-TIME DANG CHAY: %URL%
echo   De tat: chay TAT_GIAO_DIEN.bat
echo ==============================================================================
timeout /t 4
exit /b 0

:IS_UP
set "UP="
for /f "tokens=2" %%a in ('netstat -ano -p tcp ^| findstr /r /c:"LISTENING"') do (
    if "%%a"=="0.0.0.0:%PORT%" set "UP=1"
    if "%%a"=="127.0.0.1:%PORT%" set "UP=1"
    if "%%a"=="[::]:%PORT%" set "UP=1"
)
exit /b 0
