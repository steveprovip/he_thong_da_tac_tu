@echo off
chcp 65001 >nul
setlocal
:menu
cls
echo ==============================================================================
echo       GROKTOPUS 2.0 - BANG DIEU KHIEN HE THONG DA TAC TU QUANT SWARM
echo ==============================================================================
echo   1. Kiem tra truoc khi chay (Pre-flight offline: sizing / stop / gate)
echo   2. Chay Gia Lap Khop Lenh (Paper Trading tren so lenh L2 that)
echo   3. Chay Gia Lap Offline (Synthetic feed - khong can mang)
echo   4. Chay Thu Nghiem San Binance Testnet (bootstrap, 20 giay)
echo   5. Chay Binance Testnet lien tuc (Ctrl+C de dung)
echo   6. Khoi Dong Web UI Dashboard (http://127.0.0.1:8765)
echo   7. Kiem Tra Ket Noi Mang ^& So Du Vi Testnet
echo   8. Chay Toan Bo Bo Kiem Thu Tu Dong (60 tests)
echo   9. Mainnet ADVISORY - chi quan sat, KHONG dat lenh
echo   0. Thoat
echo ==============================================================================
echo   Mainnet that su dat lenh chi chay thu cong: python run_concurrent_live.py --arm
echo ==============================================================================
set "choice="
set /p choice="Nhap lua chon cua ban [0-9]: "

if "%choice%"=="1" (python tools\preflight_testnet.py & pause & goto menu)
if "%choice%"=="2" (python run_concurrent_paper.py & pause & goto menu)
if "%choice%"=="3" (python run_concurrent_paper.py --offline --seconds 25 & pause & goto menu)
if "%choice%"=="4" (python run_binance_testnet.py --bootstrap --seconds 20 & pause & goto menu)
if "%choice%"=="5" (python run_binance_testnet.py --bootstrap & pause & goto menu)
if "%choice%"=="6" (python core\web_ui_server.py & pause & goto menu)
if "%choice%"=="7" (python tools\check_connection.py & pause & goto menu)
if "%choice%"=="8" (python -m pytest -v & pause & goto menu)
if "%choice%"=="9" (python run_concurrent_live.py --seconds 60 & pause & goto menu)
if "%choice%"=="0" goto end
echo Lua chon khong hop le.
timeout /t 2 >nul
goto menu
:end
endlocal
