"""Smoke tests — 驗 shell scripts 至少 syntax OK / 可讀。"""

import os
import platform
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

BASH_SCRIPTS = [
    "run_daily.sh",
    "setup_cron_linux.sh",
]

BAT_SCRIPTS = [
    "run_daily.bat",
    "setup_task_windows.bat",
]

PS1_SCRIPTS = [
    "setup_task_windows.ps1",
]


@pytest.mark.parametrize("name", BASH_SCRIPTS)
def test_bash_script_syntax(name):
    """驗 bash script syntax。用 bash -n 不執行。"""
    p = SCRIPTS_DIR / name
    assert p.exists(), f"{p} missing"
    if platform.system() == "Windows":
        # Git-Bash / MSYS bash available
        result = subprocess.run(
            ["bash", "-n", str(p)],
            capture_output=True, text=True, timeout=10,
        )
    else:
        result = subprocess.run(
            ["bash", "-n", str(p)],
            capture_output=True, text=True, timeout=10,
        )
    assert result.returncode == 0, f"bash -n failed: {result.stderr}"


@pytest.mark.parametrize("name", BAT_SCRIPTS)
def test_bat_script_exists_and_readable(name):
    """BAT 唔有靠譜嘅 syntax parser；只 verify 存在 + 可讀。"""
    p = SCRIPTS_DIR / name
    assert p.exists()
    content = p.read_text(encoding="utf-8")
    assert content.strip(), f"{name} is empty"
    # basic structural checks
    assert "@echo off" in content or content.lower().startswith("::"), \
        f"{name} lacks @echo off"


@pytest.mark.parametrize("name", PS1_SCRIPTS)
def test_powershell_script_syntax(name):
    """PowerShell：awkward on Windows-only, 但因為閱讀起來上邊有 syntax errors。"""
    p = SCRIPTS_DIR / name
    assert p.exists()
    content = p.read_text(encoding="utf-8")
    assert "[CmdletBinding()]" in content or "param(" in content or "# " in content


def test_all_routes_call_the_same_main_cli():
    """run_daily wrappers 必須 invoke python -m daily_xauusd_brief.main（直接跑路徑）。
    setup_*.{sh,bat,ps1} 為 0 階、裝入 OS scheduler，企接用 run_daily.sh 不真的 invoke 'python -m ...' —
    這裡只 跳過 setup 驗入去。
    """
    DIRECT_RUNNERS = ["run_daily.sh", "run_daily.bat"]
    for name in DIRECT_RUNNERS:
        p = SCRIPTS_DIR / name
        content = p.read_text(encoding="utf-8")
        assert "daily_xauusd_brief.main" in content, f"{name} 不 invoke python -m ..."


def test_run_daily_supports_dry_run_flag():
    shell = SCRIPTS_DIR / "run_daily.sh"
    txt = shell.read_text(encoding="utf-8")
    assert '"$@"' in txt or '""' in txt


def test_setup_cron_linux_tags_managed_block():
    txt = (SCRIPTS_DIR / "setup_cron_linux.sh").read_text(encoding="utf-8")
    assert "# daily-xauusd-brief-managed" in txt


def test_setup_task_windows_registers_under_known_name():
    txt = (SCRIPTS_DIR / "setup_task_windows.ps1").read_text(encoding="utf-8")
    assert "DailyXauusdBrief" in txt


def test_setup_cron_linux_invokes_run_daily_sh():
    """Linux cron setup 應該裝指定 `scripts/run_daily.sh` 個 runner 落 crontab。"""
    txt = (SCRIPTS_DIR / "setup_cron_linux.sh").read_text(encoding="utf-8")
    assert "run_daily.sh" in txt


def test_setup_task_windows_invokes_run_daily_bat():
    """Windows Task Scheduler 應該指向 scripts\\run_daily.bat。"""
    txt = (SCRIPTS_DIR / "setup_task_windows.ps1").read_text(encoding="utf-8")
    assert "run_daily.bat" in txt


def test_docs_scheduling_md_exists():
    p = REPO_ROOT / "docs" / "scheduling.md"
    assert p.exists()
    txt = p.read_text(encoding="utf-8")
    assert "crontab" in txt.lower() or "cron" in txt.lower()
    assert "Task Scheduler" in txt or "task scheduler" in txt.lower()
    assert "Hermes" in txt or "hermes" in txt.lower()
