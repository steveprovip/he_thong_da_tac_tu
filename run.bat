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
echo   [2] CHAY GIA LAP  - Phien dai 11 GIO tu dong (demo.binance.com)
echo                       Tu ngat bot cu ^> Tu backup ^> Chay ^> Tu audit ^> Tu cham P1-P3
echo                       (Von ao $4930 USDT - KHONG can nhan gi cho den khi xong)
echo.
echo   [0] Thoat
echo.
echo ==============================================================================
set "choice="
set /p choice="Nhap lua chon cua ban [1, 2, 0]: "
if defined choice set "choice=%choice: =%"

if "%choice%"=="1" goto opt_live
if "%choice%"=="2" goto opt_demo
if "%choice%"=="3" goto opt_kill
if "%choice%"=="6" goto opt_l2
if "%choice%"=="0" goto opt_exit

echo.
echo Lua chon khong hop le. Vui long nhap 1, 2, 0.
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
echo   PHIEN DAI 11 GIO - CHE DO TU DONG HOAN TOAN (BINANCE DEMO)
echo   Buoc 1/5: Ngat bot cu neu con dang chay (an toan, co flatten)
echo   Buoc 2/5: Backup ledger + audit + journal
echo   Buoc 3/5: Chay phien 11 gio (cau hinh giong het Phien 1, chi doi thoi luong)
echo             Kich thuoc 1%% equity. Bang ATR: chan duoi tat, tran 20 bps.
echo   Buoc 4/5: Audit doi soat sau phien (ledger_gaps PHAI rong)
echo   Buoc 5/5: Cham diem P1-P3 gop du lieu
echo   * Web Dashboard: http://127.0.0.1:8765
echo   * TAT GIUA CHUNG: nhan Q trong cua so nay - bot tu dong huy lenh +
echo     dong vi the + go stop tren san truoc khi thoat.
echo ==============================================================================
echo.

echo [1/5] Kiem tra va ngat bot cu (neu co)...
python tools\shutdown_system.py --mode testnet 2>nul
timeout /t 3 /nobreak >nul

echo [2/5] Backup du lieu...
if not exist data\backup-truoc-phien-dai mkdir data\backup-truoc-phien-dai
copy /Y data\testnet-settled-v2.json data\backup-truoc-phien-dai\ >nul
copy /Y data\execution-audit.json data\backup-truoc-phien-dai\ >nul
copy /Y data\testnet-journal.sqlite data\backup-truoc-phien-dai\ >nul
echo       Da backup 3 file vao data\backup-truoc-phien-dai\
echo.
echo       Khoi dong L2 recorder song song (mainnet public, khong key)...
start "L2-recorder" /min python research\l2_recorder.py --symbols SOLUSDT,BTCUSDT,BNBUSDT,LINKUSDT --seconds 39600
echo       L2 recorder dang chay o cua so thu nho - data ghi vao data\l2\
echo.

echo [3/5] Khoi dong phien dai 11 gio... (KHONG nhan gi, cho bot tu chay)
for /f %%i in ('python -c "import time; print(int(time.time()))"') do set EPOCH=%%i
echo       Cau hinh PHIEN 3 nhanh A: bo loc F4 dong bang, verdict theo regime tu %EPOCH%
start http://127.0.0.1:8765
python run_binance_testnet.py --bootstrap --bootstrap-fraction 0.01 --quote-ttl 8 --horizon 300 --seconds 39600 --mission-target 0 --min-atr-bps 6 --max-atr-bps 20 --min-conviction 0.3575 --edge-regime-start %EPOCH% --maker-exit-grace 8 --flatten-on-exit

echo.
echo [4/5] Phien ket thuc (tu dong flatten). Chay audit doi soat...
python tools\audit_execution.py

echo.
echo [5/5] Cham diem P1-P3 gop du lieu...
python phan_tich\pool_phien.py --since 0

echo.
echo ==============================================================================
echo   XONG. Doc ket qua P1-P3 o tren. Neu ledger_gaps khac rong - bao ngay.
echo ==============================================================================
pause
goto menu

:opt_l2
cls
echo ==============================================================================
echo   GHI DU LIEU L2 THUC (MAINNET PUBLIC WEBSOCKET - KHONG KEY, KHONG LENH)
echo   * Ghi bookTicker + depth@100ms kem local-timestamp vao data\l2\
echo   * Muc dich: thay du lieu cong khai khong co L2 de backtest chuan sau 3-7 ngay
echo   * Chay doc lap voi bot - co the chay dong luc phien dai
echo ==============================================================================
echo.
set "l2sec="
set /p l2sec="Thoi luong gio [Enter = 11]: "
if not defined l2sec set "l2sec=11"
start "L2-recorder" /min python research\l2_recorder.py --symbols SOLUSDT,BTCUSDT,BNBUSDT,LINKUSDT --seconds %l2sec%000
echo.
echo   Da khoi dong L2 recorder (%l2sec% gio) trong cua so thu nho "L2-recorder".
echo   Data ghi vao data\l2\<symbol>\<YYYYMMDD>.jsonl - de dung lai, tat cua so do.
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
