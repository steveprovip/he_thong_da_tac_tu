@echo off
rem Thin shim only - all logic lives in run_launcher.py (single entry point).
set "PY=%LocalAppData%\Programs\Python\Python310\python.exe"
if not exist "%PY%" set "PY=python"
cd /d "%~dp0"
"%PY%" run_launcher.py
pause