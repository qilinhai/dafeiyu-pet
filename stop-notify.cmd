@echo off
setlocal
cd /d "%~dp0"

set "PY="
if exist "venv\Scripts\python.exe" set "PY=%CD%\venv\Scripts\python.exe"
if not defined PY if exist ".venv\Scripts\python.exe" set "PY=%CD%\.venv\Scripts\python.exe"
if not defined PY for %%I in (python.exe) do if not defined PY set "PY=%%~$PATH:I"

if not defined PY goto nopython

"%PY%" "notify_stop.py"
pause
exit /b 0

:nopython
echo Python not found. Run setup.cmd first.
pause
exit /b 1
