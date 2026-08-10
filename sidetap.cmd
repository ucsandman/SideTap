@echo off
rem Double-click entry point: starts the viewer + phone link (same as `python launch.py`).
cd /d "%~dp0"
python launch.py
if errorlevel 1 (
    echo.
    echo sidetap exited with an error. Read the message above.
    pause
)
