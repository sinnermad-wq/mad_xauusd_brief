@echo off
REM setup_task_windows.bat — Windows 上裝 / 移除 每日 XAUUSD 簡報 Task Scheduler job。
REM
REM Usage:
REM   scripts\setup_task_windows.bat                       :: 安裝（08:30 daily）
REM   scripts\setup_task_windows.bat uninstall             :: 移除
REM   scripts\setup_task_windows.bat status                :: 查看狀態
REM   scripts\setup_task_windows.bat dry-run               :: 預覽安裝 line

setlocal enableextensions enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "PS_SCRIPT=%SCRIPT_DIR%setup_task_windows.ps1"

set "ACTION=install"
if /I "%1"=="uninstall" set "ACTION=uninstall"
if /I "%1"=="status"    set "ACTION=status"
if /I "%1"=="dry-run"   set "ACTION=dry-run"

set "PS_ARGS=-%ACTION%"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%" %PS_ARGS%
set "RC=%ERRORLEVEL%"

if not %RC% EQU 0 (
    echo [ERROR] script exited with code %RC% 1>&2
)

endlocal & exit /b %RC%
