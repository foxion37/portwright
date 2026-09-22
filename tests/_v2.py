"""Shared helpers for the 2.0.0 test modules (profiles, freshness, tier, browser)."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "portwright"
FIXTURES = ROOT / "tests" / "fixtures" / "jev"


def run_cli(*args: str, fixtures: Path | None = FIXTURES) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in {"TYPESAFE_API_KEY", "PORTWRIGHT_JEV_FIXTURES"}}
    if fixtures is not None:
        env["PORTWRIGHT_JEV_FIXTURES"] = str(fixtures)
    return subprocess.run([str(CLI), *args], capture_output=True, text=True, env=env)


def preflight_json(*args: str, fixtures: Path | None = FIXTURES) -> dict:
    run = run_cli("preflight", *args, "--json", fixtures=fixtures)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def write_service(home: Path, service_id: str, *, version_tag: str = "v1", last_verified: str = "2026-07-13", extra: str = "", private: bool = False) -> Path:
    directory = home / "services" / ("_private" if private else "")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{service_id}.md"
    path.write_text("\n".join([
        "---",
        f"id: {service_id}",
        f"display_name: {service_id.title()}",
        f'version_tag: "{version_tag}"',
        f'last_verified: "{last_verified}"',
        "endpoint:",
        "  type: cli",
        f'  server: "{service_id}"',
        "human_steps:",
        "  - 없음",
        "agent_can:",
        "  - 직접 실행",
        "status: active",
        extra.rstrip("\n"),
        "---",
        "## 한 줄 요약",
        "검증용 Procedure",
        "## 정답 절차",
        "1. `tool deploy --prod` 를 실행한다.",
        "",
    ]).replace("\n\n---", "\n---"), encoding="utf-8")
    return path


def write_profile(home: Path, profile_id: str, project_path: Path, *, github: str, db: str | None, services: tuple[str, ...] = (), tier: str = "confirm", env_kind: str = "dotenv", env_project: str = "") -> Path:
    directory = home / "profiles"
    directory.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"id: {profile_id}",
        f"project_path: {project_path}",
        f"github_account: {github}",
        "env_source:",
        f"  kind: {env_kind}",
        f"  project: {env_project or profile_id}",
        "  environment: dev",
        "  injector: opsvc",
    ]
    if db:
        lines += ["databases:", f"  - {db}"]
    if services:
        lines += ["services:", *[f"  - {s}" for s in services]]
    lines += [f"default_tier: {tier}", "host: any", "status: active", "---", "## 한 줄 요약", "테스트 프로파일", ""]
    path = directory / f"{profile_id}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
