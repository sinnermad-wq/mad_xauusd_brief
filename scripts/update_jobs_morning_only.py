#!/usr/bin/env python3
"""Update xauusd_morning_briefing job to script-only mode."""
import json, pathlib

jobs_path = pathlib.Path("C:/Users/madwa/AppData/Local/hermes/cron/jobs.json")
data = json.loads(jobs_path.read_text(encoding="utf-8"))

PYTHON = r"C:\Users\madwa\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
SCRIPT = r"C:\Users\madwa\projects\daily-xauusd-bot\scripts\generate_xauusd_refresh.py"

for job in data["jobs"]:
    if job["id"] == "03937dbbfa64":
        print(f"Before: no_agent={job['no_agent']}")
        print(f"  script={job.get('script')}")

        job["no_agent"] = True
        job["script"] = f'"{PYTHON}" "{SCRIPT}" --mode morning'
        job["prompt"] = (
            "[DEPRECATED — script-only mode, prompt ignored] "
            "Run generate_xauusd_refresh.py --mode morning. "
            "Output (Chinese briefing) goes to stdout for scheduler delivery. "
            "Files written to data/xauusd_refresh/morning/. "
            "Weekend skip handled internally by script."
        )

        print(f"After:  no_agent={job['no_agent']}")
        print(f"  script={job['script']}")
        print(f"  prompt={job['prompt'][:60]}...")

jobs_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
print("jobs.json updated OK")