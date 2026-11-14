@echo off
setlocal EnableExtensions
rem ==============================================================================
rem GHI_BANG_L2.bat - Ghi bang tick L2 mainnet CONG KHAI cho replay / sandbox (D5, ARCH-2026-10-24)
rem   Chi doc stream cong khai (depth@100ms, aggTrade, bookTicker, forceOrder + snapshot REST):
rem   KHONG key, KHONG dat lenh, KHONG chay engine.  Ctrl+C de dung; tu noi lai khi rot mang.
rem   Ra: data\recordings\<SYMBOL>\<YYYYMMDD-HH>.jsonl.gz  (git ignore).  Can ~14 ngay cho H2/sandbox.
rem   Doi symbol:  scripts\GHI_BANG_L2.bat SOLUSDT,DOGEUSDT
rem ==============================================================================
set "PY=%LocalAppData%\Programs\Python\Python310\python.exe"
if not exist "%PY%" set "PY=python"
cd /d "%~dp0.."
set "SYMS=%~1"
if "%SYMS%"=="" set "SYMS=SOLUSDT,DOGEUSDT,BTCUSDT"
echo  Ghi bang L2 cong khai: %SYMS%  -^>  data\recordings  (Ctrl+C de dung)
"%PY%" tools\record_market.py --symbols %SYMS% --out data\recordings
pause
exit /b %ERRORLEVEL%
