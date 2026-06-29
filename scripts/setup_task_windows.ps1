# setup_task_windows.ps1 — 在 Windows 上註冊每日 XAUUSD 簡報到 Task Scheduler。
#
# Usage (PowerShell):
#   .\scripts\setup_task_windows.ps1                                 # 安裝（每天 08:30）
#   .\scripts\setup_task_windows.ps1 -Uninstall                      # 移除
#   .\scripts\setup_task_windows.ps1 -Status                         # 查看
#   .\scripts\setup_task_windows.ps1 -Hour 8 -Minute 30 -DryRun      # 預覽不安裝
#
# 須以 administrator 身份執行 PowerShell。

[CmdletBinding()]
param(
    [switch]$Uninstall,
    [switch]$Status,
    [switch]$DryRun,
    [int]$Hour = 8,
    [int]$Minute = 30,
    [string]$TaskName = "DailyXauusdBrief"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $PSCommandPath
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
$Runner = Join-Path $ProjectRoot "scripts\run_daily.bat"
$LogDir = Join-Path $ProjectRoot "logs"
$LogFile = Join-Path $LogDir "cron.log"

function Ensure-Dir([string]$Path) {
    if (-not (Test-Path $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Run-Cmd([string]$Cmd) {
    cmd.exe /c $Cmd
    return $LASTEXITCODE
}

function Install-Task {
    Ensure-Dir $LogDir
    if (-not (Test-Path $Runner)) {
        Write-Error "Runner not found: $Runner"
        exit 2
    }

    # 先刪舊的（如果有）
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false | Out-Null
        Write-Host "[INFO] removed previous task: $TaskName"
    }

    $action = New-ScheduledTaskAction `
        -Execute $Runner `
        -WorkingDirectory $ProjectRoot `
        -Argument ''   # no args = full pipeline

    $trigger = New-ScheduledTaskTrigger -Daily -At ([string]$Hour + ":" + ("{0:D2}" -f $Minute))

    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest

    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)

    if ($DryRun) {
        Write-Host "[dry-run] Would register scheduled task:"
        Write-Host "  Name:      $TaskName"
        Write-Host "  Trigger:   Daily at $($Hour):$("{0:D2}" -f $Minute)"
        Write-Host "  Action:    $Runner"
        Write-Host "  Workdir:   $ProjectRoot"
        Write-Host "  Log file:  $LogFile"
        return
    }

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description "Daily XAUUSD brief — fetch prices, indicators, news, write markdown, send to Telegram." `
        | Out-Null

    Write-Host "[OK] registered scheduled task: $TaskName"
    Write-Host "  Trigger: Daily at $($Hour):$("{0:D2}" -f $Minute)"
    Write-Host "  Action:  $Runner"
    Write-Host ""
    Write-Host "View:   powershell -File scripts\setup_task_windows.ps1 -Status"
    Write-Host "Remove: powershell -File scripts\setup_task_windows.ps1 -Uninstall"
}

function Uninstall-Task {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false | Out-Null
        Write-Host "[OK] removed scheduled task: $TaskName"
    } else {
        Write-Host "[INFO] no scheduled task named $TaskName"
    }
}

function Get-Status {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "Scheduled task EXISTS: $TaskName"
        Get-ScheduledTaskInfo -TaskName $TaskName | Format-List
        Write-Host ""
        Get-ScheduledTask | Where-Object { $_.TaskName -eq $TaskName } | Select-Object * | Format-List
    } else {
        Write-Host "Scheduled task NOT registered: $TaskName"
    }
}

if ($Uninstall) {
    Uninstall-Task
} elseif ($Status) {
    Get-Status
} else {
    Install-Task
}
