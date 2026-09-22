#!/usr/bin/env python3
"""Run the 2.0.0 gates and record the evidence the seed's GATES_OK / UPDATE_OK observers read.

Writes docs/research/2026-09-21-v2-validation.json bound to HEAD and to a sha256 manifest of
the ignored private note roots. Observers re-derive both and refuse stale evidence.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "research" / "2026-09-21-v2-validation.json"
UPDATE_SCENARIOS = ("actual_update", "dry_run_unchanged", "dirty_preserved", "conflict_refused")


def private_manifest() -> str:
    digest = hashlib.sha256()
    for directory in ("services/_private", "failures/_private"):
        for path in sorted((ROOT / directory).rglob("*")):
            if path.is_file() and not path.is_symlink():
                digest.update(path.relative_to(ROOT).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), capture_output=True, text=True, cwd=ROOT, env=env)


def unittest_counts() -> dict[str, int]:
    env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
    result = run(sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v", env=env)
    output = result.stdout + result.stderr
    ran = int(re.search(r"^Ran (\d+) tests", output, re.M).group(1))
    failed = len(re.findall(r"^(?:FAIL|ERROR): ", output, re.M))
    return {"passed": ran - failed, "failed": failed, "ran": ran}


def update_scenarios() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
    result = run(sys.executable, "-m", "unittest", "tests.test_update", "-v", env=env)
    output = result.stdout + result.stderr
    scenarios: dict[str, str] = {}
    for name in UPDATE_SCENARIOS:
        match = re.search(rf"^test_{name} .*?\.\.\. (\w+)", output, re.M)
        scenarios[name] = "pass" if match and match.group(1) == "ok" else "fail"
    return scenarios


def check_counts() -> dict[str, int]:
    result = run(str(ROOT / "bin" / "portwright"), "check")
    match = re.search(r"(\d+) passed, (\d+) failed", result.stdout)
    return {"passed": int(match.group(1)), "failed": int(match.group(2))}


def client_tmp_home() -> str:
    env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
    result = run(sys.executable, "-m", "unittest", "tests.test_deep_modules.ClientManagerInterfaceTests", env=env)
    return "pass" if result.returncode == 0 else "fail"


def main() -> int:
    record = {
        "commit": run("git", "rev-parse", "HEAD").stdout.strip(),
        "working_tree_clean": not run(
            "git", "status", "--porcelain=v1", "--untracked-files=all", "--no-renames",
            "--", ".", ":(top,exclude)docs/research/",
        ).stdout.strip(),
        "unittest": unittest_counts(),
        "check": check_counts(),
        "client_tmp_home": client_tmp_home(),
        "update_scenarios": update_scenarios(),
        "private_manifest": private_manifest(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))
    ok = (
        record["unittest"]["failed"] == 0
        and record["check"]["failed"] == 0
        and record["client_tmp_home"] == "pass"
        and all(value == "pass" for value in record["update_scenarios"].values())
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
