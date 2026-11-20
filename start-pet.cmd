@echo off
setlocal
cd /d "%~dp0"

rem ---- locate python: local venv -> .venv -> PATH ----
set "PY="
if exist "venv\Scripts\python.exe" set "PY=%CD%\venv\Scripts\python.exe"
if not defined PY if exist ".venv\Scripts\python.exe" set "PY=%CD%\.venv\Scripts\python.exe"
if not defined PY for %%I in (python.exe) do if not defined PY set "PY=%%~$PATH:I"

if not defined PY goto nopython

rem ---- one instance only: launcher.py checks the bridge port first ----
"%PY%" "launcher.py"
if errorlevel 1 (
  echo.
  echo [hint] Run this to see the traceback in this window:
  echo        "%PY%" pet.py
  pause
)
exit /b 0

:nopython
echo.
echo Python not found.
echo 1^) Install Python 3.11+ from https://www.python.org/downloads/windows/
echo 2^) Tick "Add python.exe to PATH" during setup
echo 3^) Double click setup.cmd once, then run this file again
echo.
pause
exit /b 1
