@echo off
cd /d "%~dp0"
python -m ai_agent.replay
if errorlevel 1 pause
