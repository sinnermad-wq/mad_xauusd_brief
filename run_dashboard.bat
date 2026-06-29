@echo off
echo Starting XAUUSD Dashboard...
cd /d %~dp0
call .venv\Scripts\activate
python -m daily_xauusd_brief.dashboard
pause
