@echo off
chcp 65001 >nul
cd /d "%~dp0"

:menu
cls
echo ==============================================================================
echo       GROKTOPUS 2.0 - BANG DIEU KHIEN HE THONG GIAO DICH DA TAC TU
echo ==============================================================================
echo.
echo   [1] CHAY THUC TE  - Giao dich Mainnet truc tiep (Maker Post-Only GTX)
echo   [2] CHAY GIA LAP  - Giao dich Testnet / Demo (Von ao $4930 USDT + So lenh that)
echo.
echo   [0] Thoat
echo.
echo ==============================================================================
set "choice="
set /p choice="Nhap lua chon cua ban [1, 2, 0]: "

if "%choice%"=="1" goto opt_live
if "%choice%"=="2" goto opt_demo
if "%choice%"=="0" goto opt_exit

echo.
echo Lua chon khong hop le. Vui long nhap 1, 2 hoac 0.
ping 127.0.0.1 -n 2 >nul
goto menu

:opt_live
cls
echo ==============================================================================
echo   DANG KHOI CHAY CHAY THUC TE (MAINNET)
echo   * Web Dashboard: http://127.0.0.1:8765
echo ==============================================================================
start http://127.0.0.1:8765
python run_concurrent_live.py --arm
echo.
pause
goto menu

:opt_demo
cls
echo ==============================================================================
echo   DANG KHOI CHAY CHAY GIA LAP (BINANCE TESTNET / DEMO)
echo   * So du vi thu nghiem: $4,930.88 USDT
echo   * Web Dashboard: http://127.0.0.1:8765
echo   * San truc quan: https://demo.binance.com/en/futures/BTCUSDT
echo ==============================================================================
start http://127.0.0.1:8765
python run_binance_testnet.py --bootstrap
echo.
pause
goto menu

:opt_exit
exit /b 0
