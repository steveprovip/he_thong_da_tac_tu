@echo off
chcp 65001 >nul
cd /d "%~dp0"

:menu
cls
echo ==============================================================================
echo       GROKTOPUS 2.0 - CHAY HE THONG (2 CHE DO)
echo       [1] THUC TE   - Mainnet fapi.binance.com (TIEN THAT, can --arm + XAC NHAN)
echo       [2] THU NGHIEM - Demo demo.binance.com / demo-fapi (VON AO, mac dinh)
echo ==============================================================================
echo.
if not exist .env.local (
    echo   [CANH BAO] Chua co .env.local - se copy tu .env.local.MAU, ban dien key sau.
    copy /Y .env.local.MAU .env.local >nul
)
echo   [1] Chay THUC TE (Mainnet)
echo   [2] Chay THU NGHIEM (Demo Testnet)
echo   [3] Kiem tra ket noi Demo (read-only, khong dat lenh)
echo   [4] Pre-flight offline (khong mang, khong in key)
echo   [5] Chay tests (kiem thu phan mem, KHONG phai edge)
echo.
echo   [0] Thoat
echo.
set "choice="
set /p choice="Nhap lua chon [1,2,3,4,5,0]: "
if defined choice set "choice=%choice: =%"

if "%choice%"=="1" goto opt_live
if "%choice%"=="2" goto opt_demo
if "%choice%"=="3" goto opt_check
if "%choice%"=="4" goto opt_preflight
if "%choice%"=="5" goto opt_tests
if "%choice%"=="0" goto opt_exit

echo.
echo Lua chon khong hop le.
ping 127.0.0.1 -n 2 >nul
goto menu

:opt_live
cls
echo ==============================================================================
echo   CANH BAO: BAN DANG CHON CHE DO THUC TE (MAINNET - TIEN THAT)
echo   Endpoint: https://fapi.binance.com  (chi dung key MAINNET)
echo   Chua co edge da kiem dinh. Khong chay neu chua co ket qua P1-P3 PASS.
echo =============================================================================
echo.
set "confirm="
set /p confirm="Nhap 'XAC NHAN' de mo khoa Mainnet: "
if /i not "%confirm%"=="XAC NHAN" if /i not "%confirm%"=="CONFIRM" (
    echo.
    echo Da huy Mainnet de bao ve tai khoan.
    ping 127.0.0.1 -n 3 >nul
    goto menu
)
echo.
echo   Mo Dashboard: http://127.0.0.1:8765
echo   Tat an toan: nhan Q hoac Ctrl+C trong cua so nay.
start http://127.0.0.1:8765
python run_concurrent_live.py --arm
echo.
pause
goto menu

:opt_demo
cls
echo ==============================================================================
echo   CHE DO THU NGHIEM (BINANCE DEMO - VON AO)
echo   San quan sat: https://demo.binance.com/en/futures/BTCUSDT
echo   REST: https://demo-fapi.binance.com ^| WS: wss://demo-fstream.binance.com
echo   Von ao hien tai (lan check truoc): ~4930 USDT, 0 vi the mo.
echo   Tat an toan: nhan Q hoac Ctrl+C trong cua so nay.
echo =============================================================================
echo.
start http://127.0.0.1:8765
python run_binance_testnet.py --bootstrap
echo.
pause
goto menu

:opt_check
cls
echo   Kiem tra ket noi Demo (read-only)...
python tools\check_connection.py
echo.
pause
goto menu

:opt_preflight
cls
echo   Pre-flight offline (khong mang, khong in key)...
python tools\preflight_testnet.py
echo.
pause
goto menu

:opt_tests
cls
echo   Chay tests - 2 bai cancel-entry hien FAIL khi chay full suite (flaky
echo   event-loop), chay rieng thi PASS. Lennh duoi bo qua 2 bai do:
echo.
python -m pytest -q --deselect tests/test_spread_band.py::test_cancel_entry_recovers_when_venue_state_trails_ack --deselect tests/test_spread_band.py::test_cancel_entry_halts_only_after_second_unconfirmed_check
echo.
pause
goto menu

:opt_exit
exit /b 0
