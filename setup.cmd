@echo off
setlocal
cd /d "%~dp0"

set "PY="
if exist "venv\Scripts\python.exe" set "PY=%CD%\venv\Scripts\python.exe"
if not defined PY if exist ".venv\Scripts\python.exe" set "PY=%CD%\.venv\Scripts\python.exe"
if not defined PY for %%I in (python.exe) do if not defined PY set "PY=%%~$PATH:I"

if not defined PY goto nopython

if exist "venv\Scripts\python.exe" goto install

echo [1/3] creating virtual environment .\venv ...
"%PY%" -m venv venv
if errorlevel 1 goto venvfail

:install
echo [2/3] upgrading pip ...
"venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -U pip

echo [3/3] installing requirements ...
"venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto pipfail

echo.
echo Done. Now double click start-pet.cmd
pause
exit /b 0

:venvfail
echo.
echo Failed to create venv. Try: "%PY%" -m venv venv
pause
exit /b 1

:pipfail
echo.
echo pip install failed. Check your network or proxy, then run setup.cmd again.
pause
exit /b 1

:nopython
echo.
echo Python not found. Install Python 3.11+ and tick "Add python.exe to PATH".
echo https://www.python.org/downloads/windows/
pause
exit /b 1
