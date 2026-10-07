@echo off
REM One-click: quet REST <60s -> chon kenh edge cao nhat (G > 3C) -> hanh dong -> tu thoat <=15 phut.
REM Mac dinh SHADOW (khong dat lenh). Testnet: CHAY_SINH_LOI_NGAY.bat testnet
cd /d "%~dp0..\.."
set "PY=%LocalAppData%\Programs\Python\Python310\python.exe"
if not exist "%PY%" set "PY=python"
set MODE=%1
if "%MODE%"=="" set MODE=shadow
"%PY%" scripts\run_alpha_instant.py --mode %MODE%
echo.
echo Exit code %ERRORLEVEL%  (0 = acted/reported, 2 = khong co co hoi, 3 = capital gate, 4 = mang)
pause
