@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem ==============================================================================
rem TAT_GIAO_DIEN.bat - Dong dut diem Web UI tren cong 8765, khong de lai zombie
rem ==============================================================================
set "PORT=8765"
cd /d "%~dp0"

echo ==============================================================================
echo   DANG DONG GIAO DIEN WEB OBSERVATORY (CONG %PORT%)...
echo ==============================================================================

set "FOUND="
rem Loc theo cot dia chi CUC BO + trang thai LISTENING.
rem Ban cu dung "findstr :8765" nen co the bat trung mot ket noi CO CONG NGUON
rem la 8765 va ban PID hoan toan khac.
for /f "tokens=2,5" %%a in ('netstat -ano -p tcp ^| findstr /r /c:"LISTENING"') do (
    set "ADDR=%%a"
    if "!ADDR!"=="0.0.0.0:%PORT%" call :KILL %%b
    if "!ADDR!"=="127.0.0.1:%PORT%" call :KILL %%b
    if "!ADDR!"=="[::]:%PORT%" call :KILL %%b
)

rem Quet du phong: tien trinh treo o trang thai khac van giu cua so GROKTOPUS_UI
taskkill /f /t /fi "WINDOWTITLE eq GROKTOPUS_UI*" >nul 2>&1

rem Xac minh cong da that su dong thay vi bao thanh cong mu quang
timeout /t 1 /nobreak >nul
set "STILL="
for /f "tokens=2" %%a in ('netstat -ano -p tcp ^| findstr /r /c:"LISTENING"') do (
    if "%%a"=="0.0.0.0:%PORT%" set "STILL=1"
    if "%%a"=="127.0.0.1:%PORT%" set "STILL=1"
    if "%%a"=="[::]:%PORT%" set "STILL=1"
)

echo ==============================================================================
if defined STILL (
    echo   [CANH BAO] CONG %PORT% VAN CON MO. Mo Task Manager, ket thuc python.exe
    echo              con lai roi chay lai file nay.
) else if defined FOUND (
    echo   [OK] DA TAT HOAN TOAN GIAO DIEN. CONG %PORT% DA DONG.
) else (
    echo   [OK] KHONG CO TIEN TRINH NAO DANG NGHE CONG %PORT%. Khong can lam gi.
)
echo ==============================================================================
timeout /t 3
exit /b 0

:KILL
rem /t ket thuc ca cay tien trinh con - thieu co nay chinh la nguon goc zombie
echo [INFO] Dang dong PID %1 (ca tien trinh con)...
taskkill /f /t /pid %1 >nul 2>&1
set "FOUND=1"
exit /b 0
