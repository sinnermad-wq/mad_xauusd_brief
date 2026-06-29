"""Standalone CLI：驗證 newsapi.ai (Event Registry) / newsapi.org key。

Usage:
    python -m daily_xauusd_brief.verify_newsapi_key [KEY]
    # 或讀環境變數 NEWSAPI_KEY
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

NEWSAPI_AI = "https://eventregistry.org/api/v1/article/getArticles"
NEWSAPI_ORG = "https://newsapi.org/v2/everything"


def _looks_like_uuid(s: str) -> bool:
    return "-" in s and len(s.replace("-", "")) >= 24


def verify_newsapi_ai(key: str) -> tuple[bool, dict]:
    """驗 newsapi.ai。Return (ok, payload)."""
    body = {
        "apiKey": key,
        "query": {"lang": "eng", "$query": {"keyword": "test"}, "dateStart": "now-1d", "dateEnd": "now"},
        "resultType": "articles",
        "articlesCount": 1,
    }
    try:
        r = httpx.post(NEWSAPI_AI, json=body, timeout=15)
    except httpx.HTTPError as exc:
        return False, {"error": "network", "detail": str(exc)}
    payload = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    return r.status_code == 200 and "error" not in payload, {
        "http": r.status_code,
        "body": payload,
    }


def verify_newsapi_org(key: str) -> tuple[bool, dict]:
    """驗 newsapi.org。"""
    params = {"q": "test", "pageSize": 1, "apiKey": key}
    try:
        r = httpx.get(NEWSAPI_ORG, params=params, timeout=15)
    except httpx.HTTPError as exc:
        return False, {"error": "network", "detail": str(exc)}
    payload = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    return r.status_code == 200 and payload.get("status") == "ok", {
        "http": r.status_code,
        "body": payload,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="驗證 NewsAPI key 並回報 endpoint 結果")
    parser.add_argument("key", nargs="?", help="API key（或者設 NEWSAPI_KEY env）")
    parser.add_argument(
        "--force", choices=["ai", "org"], help="強制測某 endpoint（不打 auto-detect）"
    )
    args = parser.parse_args()

    key = args.key or os.environ.get("NEWSAPI_KEY", "").strip()
    if not key:
        print("[ERROR] no key provided (arg or $NEWSAPI_KEY)", file=sys.stderr)
        return 2

    looks_uuid = _looks_like_uuid(key)
    use_ai = args.force != "org" and (args.force == "ai" or looks_uuid)
    use_org = args.force != "ai" and (args.force == "org" or not looks_uuid)

    print(f"Key 長度: {len(key)}, 格式: {'UUID' if looks_uuid else 'hex'}")
    print()

    if use_ai:
        ok, payload = verify_newsapi_ai(key)
        print(f"[newsapi.ai]   {'✅ OK' if ok else '❌ FAIL'}")
        print(f"  {json.dumps(payload, ensure_ascii=False)[:240]}")
    if use_org:
        ok, payload = verify_newsapi_org(key)
        print(f"[newsapi.org]  {'✅ OK' if ok else '❌ FAIL'}")
        print(f"  {json.dumps(payload, ensure_ascii=False)[:240]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
