@echo off
REM run_weekly_digest.bat — Generate XAUUSD Weekly Digest
REM
REM Usage:
REM   scripts\run_weekly_digest.bat             :: 7d digest (default)
REM   scripts\run_weekly_digest.bat --window-days 14  :: 14d digest
REM   scripts\run_weekly_digest.bat --dry-run        :: preview only, no file write
REM
REM This script is called by Windows Task Scheduler (not by Streamlit / dashboard).
REM Schedule example: every Sunday at 20:00 HKT
REM

setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%.." >NUL
set "PROJECT_ROOT=%CD%"
popd >NUL

cd /d "%PROJECT_ROOT%"

REM --- Python interpreter ---
where uv >NUL 2>&1
if %ERRORLEVEL% EQU 0 (
    set "PYTHON_BIN=uv run python"
) else if exist ".venv\Scripts\python.exe" (
    set "PYTHON_BIN=.venv\Scripts\python.exe"
) else (
    set "PYTHON_BIN=python"
)

echo [%DATE% %TIME%] Weekly digest starting
echo   Python: !PYTHON_BIN!
echo   Project: !PROJECT_ROOT!

REM Pass all arguments through to the script
call !PYTHON_BIN! "%PROJECT_ROOT%\scripts\generate_weekly_digest.py" %*

set "EXIT_CODE=%ERRORLEVEL%"

echo [%DATE% %TIME%] Weekly digest finished with exit code !EXIT_CODE!

endlocal & exit /b %EXIT_CODE%