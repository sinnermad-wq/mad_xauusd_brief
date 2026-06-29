"""One-off helper: 重寫 .env 中的 NEWSAPI_KEY。

Usage:
    python scripts/update_env.py            # 使用 $NEW_NEWSAPI_KEY env
    NEW_NEWSAPI_KEY="*** scripts/update_env.py    # 直接傳 env

只更新 NEWSAPI_KEY，其他 env 保持不變。
"""

import os
import sys
from pathlib import Path


PFX = "NEWSAPI_KEY="


def main() -> int:
    new_key = os.environ.get("NEW_NEWSAPI_KEY", "").strip()
    if not new_key:
        print("[ERROR] set $NEW_NEWSAPI_KEY before invoking", file=sys.stderr)
        return 2

    repo_root = Path(__file__).resolve().parent.parent
    env_path = repo_root / ".env"
    if not env_path.exists():
        print(f"[ERROR] {env_path} not found", file=sys.stderr)
        return 2

    text = env_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    out_lines: list[str] = []
    replaced = False
    for ln in lines:
        if ln.startswith(PFX):
            out_lines.append(PFX + new_key)
            replaced = True
        else:
            out_lines.append(ln)
    if not replaced:
        out_lines.append(PFX + new_key)
    env_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print(f"[OK] updated NEWSAPI_KEY in {env_path} (len={len(new_key)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
