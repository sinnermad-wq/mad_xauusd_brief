"""Tests for Weekly Digest system — generate_weekly_digest.py + digest_loader.py."""
from __future__ import annotations

import json
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

HKT = timezone(timedelta(hours=8))


# ── Fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Build a minimal fake repo with history + fusion + engine_reviews."""
    repo = tmp_path
    hist = repo / "data" / "history"
    cand = hist / "candlestick"
    fusion_dir = hist / "fusion"
    rev_dir = repo / "data"
    weekly_out = repo / "data" / "weekly_digest"

    for d in [hist, cand, fusion_dir, rev_dir, weekly_out]:
        d.mkdir(parents=True, exist_ok=True)

    today = datetime.now(HKT).date()
    d7 = today - timedelta(days=7)
    d14 = today - timedelta(days=14)

    # 3 daily briefing files (7d window)
    for i, d in enumerate([d7 + timedelta(days=j) for j in range(7)]):
        bf = hist / f"{d.isoformat()}T08-31-00_daily.json"
        bf.write_text(json.dumps({
            "timestamp": f"{d.isoformat()}T08:31:00",
            "mode": "daily",
            "xauusd_price": 4100.0 + i * 5,
            "daily_change_abs": 5.0,
            "daily_change_pct": 0.12,
            "trend": ["bullish", "bearish", "bullish"][i % 3],
            "technical_summary": "Test",
            "top_news": [{"title": f"News {i}", "tag": "central_bank", "source": "Test", "url": "http://x"}],
            "risk_notes": "",
            "final_summary": "",
            "notes": "",
        }), encoding="utf-8")

    # 1 fusion file
    fusion_file = fusion_dir / f"{today.isoformat()}.json"
    fusion_file.write_text(json.dumps({
        "engine_name": "fusion",
        "run_id": "test-run-001",
        "fusion_bias": "bullish",
        "fusion_confidence": 0.66,
        "consensus_label": "insufficient_context",
        "conflict_label": "none",
        "trade_candidate": False,
        "decision_ready": True,
        "trade_eligible": False,
        "signal_strength": "moderate",
        "summary_zh": "Test fusion output",
        "explanation_zh": "Test explanation",
    }), encoding="utf-8")

    # engine_reviews.csv
    reviews_file = rev_dir / "engine_reviews.csv"
    reviews_file.write_text(
        "review_id,date,hkt_time,session,symbol,engine_version,input_source,"
        "higher_tf_bias,direction_classification,bias,confidence,support_levels,"
        "resistance_levels,invalidation_level,candle_semantics,evidence_count,"
        "conflict_count,insufficient_context,ma200_available,pre_event_flag,"
        "initial_price,validation_window,validation_price,max_favorable_move,"
        "max_adverse_move,invalidation_hit,outcome_label,confidence_bucket,"
        "review_score,failure_reason,notes,lesson,next_adjustment,created_at,updated_at\n"
        f"TEST-R_2026-07-07_103257_aaaaaa,2026-07-07,10:32:57,NY_open,GC=F,v1,manual,"
        "bullish,bullish_continuation,bullish,67.0,4156.4,4169.46,4187.3,"
        "seller_pressure,4,1,false,true,false,4163.2,4h,4168.3,22.1,8.4,false,"
        "correct,55-69,4,none,,test,,,2026-07-07T16:05:18+08:00,2026-07-07T16:15:03+08:00\n"
        f"TEST-R_2026-07-08_103257_bbbbbb,2026-07-08,10:32:57,NY_open,GC=F,v1,manual,"
        "bearish,bearish_reversal,bearish,55.0,4156.4,4169.46,4187.3,"
        "seller_pressure,3,1,false,true,false,4163.2,4h,4168.3,22.1,8.4,false,"
        ",55-69,4,none,,test,,,2026-07-08T16:05:18+08:00,2026-07-08T16:15:03+08:00\n",
        encoding="utf-8",
    )
    return repo


# ── generate_weekly_digest.py ───────────────────────────────────────────────
class TestGenerateWeeklyDigest:
    def test_7d_generates_json(self, temp_repo: Path, tmp_path: Path):
        from scripts.generate_weekly_digest import generate_weekly_digest

        digest = generate_weekly_digest(
            base_dir=temp_repo,
            window_days=7,
            output_dir=tmp_path / "out",
            output_md=False,
            dry_run=False,
        )

        assert digest["schema_version"] == "1.0"
        assert digest["window_days"] == 7
        assert digest["dominant_bias"] == "bullish"
        assert isinstance(digest["avg_fusion_confidence"], float)
        assert "summary" in digest

        # JSON file written (dated, not latest symlink)
        json_files = [f for f in (tmp_path / "out").glob("*_digest.json")
                      if f.name != "latest_weekly_digest.json"]
        assert len(json_files) == 1

        # latest_weekly_digest.json written
        latest = (tmp_path / "out") / "latest_weekly_digest.json"
        assert latest.exists()

    def test_md_output(self, temp_repo: Path, tmp_path: Path):
        from scripts.generate_weekly_digest import generate_weekly_digest

        generate_weekly_digest(
            base_dir=temp_repo,
            window_days=7,
            output_dir=tmp_path / "out",
            output_md=True,
            dry_run=False,
        )

        md_files = list((tmp_path / "out").glob("*_weekly.md"))
        assert len(md_files) == 1
        content = md_files[0].read_text(encoding="utf-8")
        assert "# XAUUSD Weekly Digest" in content
        assert "Fusion Engine" in content

    def test_dry_run_no_files_written(self, temp_repo: Path):
        from scripts.generate_weekly_digest import generate_weekly_digest

        digest = generate_weekly_digest(
            base_dir=temp_repo,
            window_days=7,
            output_dir=Path("/nonexistent"),
            output_md=False,
            dry_run=True,
        )

        assert digest["window_days"] == 7
        assert not (temp_repo / "data" / "weekly_digest" / "latest_weekly_digest.json").exists()

    def test_empty_history_graceful(self, tmp_path: Path):
        from scripts.generate_weekly_digest import generate_weekly_digest

        # Empty repo — no briefings, no fusion
        empty_repo = tmp_path / "empty"
        empty_repo.mkdir()
        (empty_repo / "data").mkdir()
        (empty_repo / "data" / "history").mkdir()

        digest = generate_weekly_digest(
            base_dir=empty_repo,
            window_days=7,
            output_dir=tmp_path / "out2",
            dry_run=False,
        )

        # Should not raise — graceful degradation
        assert digest["schema_version"] == "1.0"
        assert digest["summary"]["fusion"]["total_runs"] == 0
        assert digest["summary"]["price"]["latest"] is None

    def test_main_cli_dry_run(self, temp_repo: Path, capsys):
        import sys
        from pathlib import Path as P

        orig_argv = sys.argv
        orig_path = sys.path.copy()
        sys.path.insert(0, str((temp_repo.parent).parent / "src"))
        try:
            import scripts.generate_weekly_digest as gwd

            # Isolate: no args = dry_run default
            sys.argv = ["generate_weekly_digest.py"]
            rc = gwd.main()
            assert rc == 0
        finally:
            sys.argv[:] = orig_argv
            sys.path[:] = orig_path


# ── digest_loader.py ───────────────────────────────────────────────────────
class TestDigestLoader:
    def test_load_latest_returns_real_digest(self, temp_repo: Path, tmp_path: Path):
        from scripts.generate_weekly_digest import generate_weekly_digest
        from strategy_health.digest_loader import load_latest_weekly_digest

        out_dir = tmp_path / "weekly_digest"
        generate_weekly_digest(
            base_dir=temp_repo,
            window_days=7,
            output_dir=out_dir,
            dry_run=False,
        )

        # Load with explicit output_dir
        digest = load_latest_weekly_digest(output_dir=out_dir)
        assert digest.get("_fallback") is not True
        assert digest["window_days"] == 7
        assert digest["dominant_bias"] == "bullish"

    def test_load_from_exact_path(self, temp_repo: Path, tmp_path: Path):
        from scripts.generate_weekly_digest import generate_weekly_digest
        from strategy_health.digest_loader import load_weekly_digest_from_path

        out_dir = tmp_path / "weekly_digest"
        generate_weekly_digest(base_dir=temp_repo, window_days=7, output_dir=out_dir)
        json_file = list(out_dir.glob("*_digest.json"))[0]

        digest = load_weekly_digest_from_path(json_file)
        assert digest.get("_fallback") is not True
        assert digest["window_days"] == 7

    def test_fallback_no_files(self, tmp_path: Path):
        from strategy_health.digest_loader import (
            load_latest_weekly_digest,
            weekly_digest_fallback,
        )

        empty_dir = tmp_path / "nonexistent"
        empty_dir.mkdir(parents=True)

        digest = load_latest_weekly_digest(output_dir=empty_dir)
        assert digest["_fallback"] is True

        fb = weekly_digest_fallback("test reason")
        assert fb["_fallback"] is True
        assert fb["metadata"]["fallback_reason"] == "test reason"

    def test_fallback_corrupt_file(self, tmp_path: Path):
        from strategy_health.digest_loader import load_weekly_digest_from_path

        bad = tmp_path / "corrupt.json"
        bad.write_text("not valid json {{{", encoding="utf-8")

        digest = load_weekly_digest_from_path(bad)
        assert digest["_fallback"] is True

    def test_fallback_missing_file(self, tmp_path: Path):
        from strategy_health.digest_loader import load_weekly_digest_from_path

        digest = load_weekly_digest_from_path(tmp_path / "does_not_exist.json")
        assert digest["_fallback"] is True

    def test_digest_age_hours(self, temp_repo: Path, tmp_path: Path):
        from scripts.generate_weekly_digest import generate_weekly_digest
        from strategy_health.digest_loader import (
            digest_age_hours,
            load_latest_weekly_digest,
        )

        out_dir = tmp_path / "wd"
        generate_weekly_digest(base_dir=temp_repo, window_days=7, output_dir=out_dir)
        digest = load_latest_weekly_digest(output_dir=out_dir)

        age = digest_age_hours(digest)
        assert age is not None
        assert 0 <= age < 24  # generated just now

    def test_get_summary_helpers(self, temp_repo: Path, tmp_path: Path):
        from scripts.generate_weekly_digest import generate_weekly_digest
        from strategy_health.digest_loader import (
            get_fusion_summary,
            get_price_summary,
            load_latest_weekly_digest,
        )

        out_dir = tmp_path / "wd2"
        generate_weekly_digest(base_dir=temp_repo, window_days=7, output_dir=out_dir)
        digest = load_latest_weekly_digest(output_dir=out_dir)

        price = get_price_summary(digest)
        assert price["latest"] is not None
        fusion = get_fusion_summary(digest)
        assert fusion["total_runs"] >= 0

    def test_window_days_filter(self, temp_repo: Path, tmp_path: Path):
        from scripts.generate_weekly_digest import generate_weekly_digest
        from strategy_health.digest_loader import load_latest_weekly_digest

        out_dir = tmp_path / "wd3"
        # Generate both 7d and 14d digests
        for w in [7, 14]:
            generate_weekly_digest(base_dir=temp_repo, window_days=w, output_dir=out_dir)

        digest_7 = load_latest_weekly_digest(window_days=7, output_dir=out_dir)
        digest_14 = load_latest_weekly_digest(window_days=14, output_dir=out_dir)

        # Both should load (preferring matching window)
        assert digest_7.get("window_days") == 7
        assert digest_14.get("window_days") == 14


# ── Integration: generate → load round-trip ────────────────────────────────
class TestRoundTrip:
    def test_full_round_trip(self, temp_repo: Path, tmp_path: Path):
        from scripts.generate_weekly_digest import generate_weekly_digest
        from strategy_health.digest_loader import (
            digest_age_hours,
            get_fusion_summary,
            get_news_summary,
            load_latest_weekly_digest,
        )

        out_dir = tmp_path / "roundtrip"
        generate_weekly_digest(
            base_dir=temp_repo,
            window_days=7,
            output_dir=out_dir,
            output_md=True,
        )

        digest = load_latest_weekly_digest(output_dir=out_dir)

        assert digest.get("_fallback") is not True
        assert digest["window_days"] == 7
        assert digest["dominant_bias"] == "bullish"

        fs = get_fusion_summary(digest)
        assert fs["total_runs"] == 1
        assert fs["bullish_count"] == 1

        ns = get_news_summary(digest)
        assert ns["total_articles"] > 0
        assert any(t["tag"] == "central_bank" for t in ns["top_tags"])

        age = digest_age_hours(digest)
        assert age is not None