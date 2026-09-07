@echo off
setlocal
cd /d "%~dp0"
python "%~dp0scripts\run_autodev.py" %*
set "result=%errorlevel%"
if "%~1"=="" pause
exit /b %result%
