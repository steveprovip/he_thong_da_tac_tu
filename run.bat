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
echo   [2] CHAY GIA LAP  - Du lieu that + So lenh that tren san Demo
echo                       (demo.binance.com - Von ao $4930 USDT)
echo   [3] TAT HE THONG  - KILL SWITCH: Dung bot + dong moi vi the + huy moi lenh
echo.
echo   [0] Thoat
echo.
echo ==============================================================================
set "choice="
set /p choice="Nhap lua chon cua ban [1, 2, 3, 0]: "
if defined choice set "choice=%choice: =%"

if "%choice%"=="1" goto opt_live
if "%choice%"=="2" goto opt_demo
if "%choice%"=="3" goto opt_kill
if "%choice%"=="0" goto opt_exit

echo.
echo Lua chon khong hop le. Vui long nhap 1, 2, 3 hoac 0.
ping 127.0.0.1 -n 2 >nul
goto menu

:opt_live
cls
echo ==============================================================================
echo   CANH BAO AN TOAN: BAN DANG CHON CHAY THUC TE (MAINNET LIVE)
echo   * Tien that tren tai khoan Binance se duoc su dung.
echo   * Khuyen nghi: Chi kich hoat khi da kiem dinh ky luong.
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
echo   * TAT NGAY AN TOAN: nhan Q trong cua so nay hoac Ctrl+C - bot se tu
echo     dong huy lenh + dong moi vi the + go stop tren san truoc khi thoat.
echo ==============================================================================
start http://127.0.0.1:8765
python run_concurrent_live.py --arm
echo.
pause
goto menu

:opt_demo
cls
echo ==============================================================================
echo   DANG KHOI CHAY CHAY GIA LAP (BINANCE DEMO - demo.binance.com)
echo   * Du lieu thi truong THAT + lenh THAT tren san Demo (von ao $4930 USDT)
echo   * PHIEN DO LUONG (Phien 1 va 2): chay DU 90 phut de lay toi da du lieu.
echo     Kich thuoc 1%% equity (~49 USDT). Bang ATR: chan duoi tat, tran 20 bps.
echo     Bao cao ghi vao data\testnet-mission-report.json khi ket thuc.
echo   * Doc tieu chi PASS/FAIL trong trang tham chieu TRUOC khi bam Enter.
echo   * Xem tai khoan/lenh tai: https://demo.binance.com/en/futures/BTCUSDT
echo   * Web Dashboard: http://127.0.0.1:8765
echo   * TAT NGAY AN TOAN: nhan Q trong cua so nay hoac Ctrl+C - bot se tu
echo     dong huy lenh + dong moi vi the + go stop tren san truoc khi thoat.
echo ==============================================================================
start http://127.0.0.1:8765
python run_binance_testnet.py --bootstrap --bootstrap-fraction 0.01 --quote-ttl 8 --horizon 300 --seconds 5400 --mission-target 0 --max-atr-bps 20 --maker-exit-grace 8 --flatten-on-exit
echo.
pause
goto menu

:opt_kill
cls
echo ==============================================================================
echo   TAT HE THONG HOAN TOAN (KILL SWITCH)
echo   * Buoc 1: Gui lenh tat den bot dang chay (neu co) - bot tu dong
echo     huy lenh, dong vi the, go stop truoc khi thoat.
echo   * Buoc 2: Quet san lan cuoi: huy moi lenh dang loi + dong moi vi the.
echo   * Mac dinh: TESTNET (von ao). Go M roi Enter de flatten MAINNET.
echo ==============================================================================
echo.
set "mode="
set /p mode="Chon san [Enter = Testnet / M = Mainnet]: "
if /i not "%mode%"=="M" goto kill_testnet
set "confirm="
set /p confirm="Nhap 'XAC NHAN' de flatten MAINNET: "
if /i "%confirm%"=="XAC NHAN" goto kill_mainnet
if /i "%confirm%"=="CONFIRM" goto kill_mainnet
echo.
echo Da huy flatten Mainnet de bao ve an toan.
pause
goto menu

:kill_mainnet
python tools\shutdown_system.py --mode mainnet --arm
echo.
pause
goto menu

:kill_testnet
python tools\shutdown_system.py --mode testnet
echo.
pause
goto menu

:opt_exit
exit /b 0
