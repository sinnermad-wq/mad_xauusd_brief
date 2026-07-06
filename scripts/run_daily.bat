@echo off
REM run_daily.bat — 手動觸發每日黃金簡報，或由 Task Scheduler 喚醒。
REM
REM Usage:
REM   scripts\run_daily.bat                :: 預設 full pipeline
REM   scripts\run_daily.bat --dry-run      :: 不送 Telegram
REM   scripts\run_daily.bat --send-only    :: 不重抓資料，從 cache 重發
REM   scripts\run_daily.bat --no-send      :: 只生成 MD，不送 Telegram
REM
REM 環境變數：
REM   PYTHON  - Python interpreter（預設：uv run python）

setlocal enableextensions enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%.." >NUL
set "PROJECT_ROOT=%CD%"
popd >NUL

cd /d "%PROJECT_ROOT%"

if not exist ".env" (
    echo [WARN] "%PROJECT_ROOT%\.env" not found; main.py 會 raise >&2
)

REM --- Python interpreter ---
where uv >NUL 2>&1
if %ERRORLEVEL% EQU 0 (
    if defined PYTHON (
        set "PYTHON_BIN=%PYTHON%"
    ) else (
        set "PYTHON_BIN=uv run python"
    )
) else if exist ".venv\Scripts\python.exe" (
    if defined PYTHON (
        set "PYTHON_BIN=%PYTHON%"
    ) else (
        set "PYTHON_BIN=.venv\Scripts\python.exe"
    )
) else (
    set "PYTHON_BIN=python"
)

echo [%DATE% %TIME%] run_daily starting (args: %*)
echo   Python:  !PYTHON_BIN!
echo   Project: !PROJECT_ROOT!

call !PYTHON_BIN! -m daily_xauusd_brief.main %*
set "EXIT_CODE=%ERRORLEVEL%"

echo [%DATE% %TIME%] run_daily finished with exit code !EXIT_CODE!

REM --- 08:30 daily journal hook (only after successful pipeline run) ---
if !EXIT_CODE! EQU 0 (
    if exist "%PROJECT_ROOT%\scripts\assemble_journal.py" (
        echo [%DATE% %TIME%] assembling daily journal for HKT today
        call !PYTHON_BIN! "%PROJECT_ROOT%\scripts\assemble_journal.py"
    )
)

endlocal & exit /b %EXIT_CODE%
