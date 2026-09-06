@echo off
chcp 65001 >nul
cd /d "%~dp0"

:menu
cls
echo ==============================================================================
echo       GROKTOPUS 2.0 - BANG DIEU KHIEN HE THONG GIAO DICH DA TAC TU
echo ==============================================================================
echo.
echo   [1] CHAY THUC TE  - Giao dich Mainnet truc tiep (Co xac nhan an toan 2 buoc)
echo   [2] CHAY GIA LAP  - Giao dich Testnet / Demo (Von ao $4930 USDT + So lenh that)
echo   [3] SIGNAL LAB    - Do luong Edge & Kiem tra Alpha tu du lieu da ghi
echo   [4] HFT BACKTEST  - Mo phong vi cau truc hang doi (hftbacktest 90 phut)
echo.
echo   [0] Thoat
echo.
echo ==============================================================================
set "choice="
set /p choice="Nhap lua chon cua ban [1, 2, 3, 4, 0]: "

if "%choice%"=="1" goto opt_live
if "%choice%"=="2" goto opt_demo
if "%choice%"=="3" goto opt_lab
if "%choice%"=="4" goto opt_backtest
if "%choice%"=="0" goto opt_exit

echo.
echo Lua chon khong hop le. Vui long nhap 1, 2, 3, 4 hoac 0.
ping 127.0.0.1 -n 2 >nul
goto menu

:opt_live
cls
echo ==============================================================================
echo   CANH BAO AN TOAN: BAN DANG CHON CHAY THUC TE (MAINNET LIVE)
echo   * Tien that tren tai khoan Binance se duoc su dung.
echo   * Khuyen nghi: Chi kich hoat khi Signal Lab chung minh co Edge duong.
echo ==============================================================================
echo.
set "confirm="
set /p confirm="Nhap 'XAC NHAN' (hoac 'CONFIRM') de mo khoa Mainnet: "
if /i not "%confirm%"=="XAC NHAN" if /i not "%confirm%"=="CONFIRM" (
    echo.
    echo Da huy kich hoat Mainnet de bao ve an toan tai khoan.
    ping 127.0.0.1 -n 3 >nul
    goto menu
)

cls
echo ==============================================================================
echo   DANG KHOI CHAY CHAY THUC TE (MAINNET LIVE)
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

:opt_lab
cls
echo ==============================================================================
echo   DANG KHOI CHAY SIGNAL LAB - CALIBRATE EDGE
echo ==============================================================================
echo.
echo 1. Do tren du lieu Testnet:
python tools\calibrate_edge.py --mode testnet
echo.
echo 2. Do tren du lieu Paper:
python tools\calibrate_edge.py --mode paper
echo.
pause
goto menu

:opt_backtest
cls
echo ==============================================================================
echo   DANG KHOI CHAY HFT BACKTEST (MO PHONG HANG DOI & VI CAU TRUC)
echo   * Su dung hftbacktest Numba JIT + Queue Model
echo   * Mo phong 90 phut synthetic tape
echo ==============================================================================
echo.
python tools\run_backtest.py --synthetic --minutes 90
echo.
pause
goto menu

:opt_exit
exit /b 0
