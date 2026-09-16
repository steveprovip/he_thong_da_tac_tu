@echo off
rem Carry Shadow/Testnet menu. No Mainnet order is armed by this launcher.
set "PY=%LocalAppData%\Programs\Python\Python310\python.exe"
if not exist "%PY%" set "PY=python"
cd /d "%~dp0"
"%PY%" run_launcher.py
pause
