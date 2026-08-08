@echo off
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
python -m phone_harness %*
