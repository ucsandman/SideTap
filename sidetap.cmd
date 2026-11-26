@echo off
rem SIDETAP_PYTHON: the Python the installer set up (3.13 or later); else python on PATH.
set "SIDETAP_PY=%SIDETAP_PYTHON%"
if not defined SIDETAP_PY set "SIDETAP_PY=python"
rem Double-click entry point: starts the viewer + phone link (same as `python launch.py`).
cd /d "%~dp0"
"%SIDETAP_PY%" launch.py
if errorlevel 1 (
    echo.
    echo SideTap exited with an error. Read the message above.
    pause
)
