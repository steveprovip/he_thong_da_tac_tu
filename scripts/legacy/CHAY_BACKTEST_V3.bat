@echo off
rem ==============================================================================
rem CHAY_BACKTEST_V3.bat - backtest doi soat v1 vs v3 tren CUNG du lieu Binance that
rem   30 cap thanh khoan nhat x ~21 ngay nen 5m. Chi doc, khong dat lenh.
rem   Ket qua: artifacts\radar_grid\<gio>.md   (tham so: --symbols 40 --pages 6 --refresh)
rem   Chi dua cau hinh len demo khi OOS mean ^> 0 VA L95 ^> 0.
rem ==============================================================================
cd /d "%~dp0..\.."
set "PY=%LocalAppData%\Programs\Python\Python310\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" tools\backtest_radar_grid.py %*
echo.
pause
