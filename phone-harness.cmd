@echo off
rem SIDETAP_PYTHON: the Python the installer set up (3.13 or later); else python on PATH.
set "SIDETAP_PY=%SIDETAP_PYTHON%"
if not defined SIDETAP_PY set "SIDETAP_PY=python"
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
"%SIDETAP_PY%" -m phone_harness %*
