@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0.runtime-deps;%PYTHONPATH%"
set "PYTHONW="
for /f "delims=" %%P in ('where pythonw 2^>nul') do if not defined PYTHONW set "PYTHONW=%%P"
if not defined PYTHONW set "PYTHONW=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"
if not exist "%PYTHONW%" (
    echo Python runtime not found. Please install Python and add it to PATH.
    pause
    exit /b 1
)
start "" "%PYTHONW%" "%~dp0main.py"
