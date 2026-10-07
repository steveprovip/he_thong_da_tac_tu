@echo off
REM Von nho 3.85 USDT (~100k VND): tinh loi nhuan rong tung kenh, roi quet shadow 15 phut.
REM KHONG dat lenh that. Doc docs\VON_NHO_100K_2026.md truoc khi lam gi voi tien that.
cd /d "%~dp0..\.."
set "PY=%LocalAppData%\Programs\Python\Python310\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" research\micro_capital.py --capital 3.85
echo.
"%PY%" scripts\run_alpha_instant.py --mode shadow --equity 3.85 --horizon-days 30
echo.
echo Exit code %ERRORLEVEL%  (3 = capital gate: 3.85 USDT < 10 USDT toi thieu cho carry hai chan)
pause
