@echo off
setlocal
cd /d "%~dp0"
python "%~dp0scripts\run_overnight.py" %*
exit /b %errorlevel%
