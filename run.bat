@echo off
chcp 65001 >nul
setlocal
:menu
cls
echo ==============================================================================
echo       GROKTOPUS 2.0 - BẢNG ĐIỀU KHIỂN HỆ THỐNG GIAO DỊCH ĐA TÁC TỬ
echo ==============================================================================
echo.
echo   [1] CHẠY THỰC TẾ  - Giao dịch Mainnet trực tiếp (Khớp lệnh Post-Only GTX)
echo   [2] CHẠY GIẢ LẬP  - Giao dịch Testnet / Demo ($4,930.88 USDT ảo + Sổ lệnh thật)
echo.
echo   [0] Thoát
echo.
echo ==============================================================================
set "choice="
set /p choice="Nhập lựa chọn của bạn [1, 2, 0]: "

if "%choice%"=="1" (
    cls
    echo ==============================================================================
    echo   ⚡ ĐANG KHỞI CHẠY CHẾ ĐỘ THỰC TẾ (MAINNET LIVE)
    echo   * Web Dashboard: http://127.0.0.1:8765
    echo   * Lệnh bảo vệ: Maker Post-Only (GTX) + Exchange STOP_MARKET
    echo ==============================================================================
    start http://127.0.0.1:8765
    python run_concurrent_live.py --arm
    pause
    goto menu
)

if "%choice%"=="2" (
    cls
    echo ==============================================================================
    echo   🧪 ĐANG KHỞI CHẠY CHẾ ĐỘ GIẢ LẬP (BINANCE TESTNET / DEMO)
    echo   * Số dư ví thử nghiệm: $4,930.88 USDT
    echo   * Web Dashboard: http://127.0.0.1:8765
    echo   * Sàn trực quan: https://demo.binance.com/en/futures/BTCUSDT
    echo ==============================================================================
    start http://127.0.0.1:8765
    python run_binance_testnet.py --bootstrap
    pause
    goto menu
)

if "%choice%"=="0" goto end
echo Lựa chọn không hợp lệ. Vui lòng nhập 1, 2 hoặc 0.
timeout /t 2 >nul
goto menu
:end
endlocal
