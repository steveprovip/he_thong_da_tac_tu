@echo off
title GrokTOPUS - 8 Agents, 8 Brains, 1 Body
cd /d "%~dp0"

:: Launch GrokTOPUS via Python (clean ASCII wrapper, 100% immune to Windows CMD encoding bugs)
python run_launcher.py %*

if errorlevel 1 (
    echo.
    echo System exited with an error.
    pause
)
