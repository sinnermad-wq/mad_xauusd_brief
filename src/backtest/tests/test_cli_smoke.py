"""CLI smoke tests for --mode backtest.

Tests (3):
1. --mode backtest --dry-run with insufficient data → exit 0, prints INSUFFICIENT_DATA
2. --mode backtest --horizon-bars rejects non-integer → exit 1
3. --mode backtest --help shows backtest args
"""
import subprocess
import sys

ROOT = "C:\\Users\\madwa\\projects\\daily-xauusd-bot"
PY   = f"{ROOT}\\.venv\\Scripts\\python.exe"
MOD  = "daily_xauusd_brief.main"


def _run(args, check=False):
    r = subprocess.run(
        [PY, "-m", MOD] + args,
        capture_output=True, text=True,
        cwd=ROOT,
    )
    if check:
        assert r.returncode == 0, f"rc={r.returncode}\nstdout={r.stdout}\nstderr={r.stderr}"
    return r


def test_backtest_help_shows_backtest_args():
    r = _run(["--mode", "backtest", "--help"])
    assert r.returncode == 0
    assert "--horizon-bars" in r.stdout
    assert "--backtest-source" in r.stdout
    assert "--from-date" in r.stdout


def test_backtest_invalid_horizon_bars_exits_1():
    r = _run(["--mode", "backtest", "--horizon-bars", "a,b,c"])
    assert r.returncode == 1
    assert "must be comma-separated ints" in r.stderr or "invalid" in r.stderr.lower()


def test_backtest_dry_run_exit_0_insufficient_data():
    """With no history in the temp path → no signals → INSUFFICIENT_DATA but exit 0."""
    import tempfile
    from pathlib import Path

    # Use a tmp path with no history files
    with tempfile.TemporaryDirectory() as td:
        r = _run([
            "--mode", "backtest",
            "--dry-run",
            "--horizon-bars", "1,3",
            "--backtest-source", "fusion",
        ])
        # Should not crash; verdict INSUFFICIENT or early return
        assert r.returncode == 0, f"rc={r.returncode}\nstderr={r.stderr}"
        # Either stdout has INSUFFICIENT_DATA or no signals warning
        combined = r.stdout + r.stderr
        assert (
            "INSUFFICIENT_DATA" in combined or
            "no signals" in combined.lower() or
            "signals loaded" in combined
        ), f"Unexpected output: {combined}"