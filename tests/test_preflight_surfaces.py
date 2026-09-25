"""CLI and MCP must run one preflight operation: same decision, same ledger row.

Both surfaces go through `portwright.preflight.run_preflight`, so a MCP-only caller
leaves the same usage record the CLI does — and neither surface can drift from the other.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from portwright.mcp import serve  # noqa: E402
from tests._v2 import run_cli, write_profile, write_service  # noqa: E402


def _mcp_session(home: Path, arguments: dict) -> list[dict]:
    request = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "preflight", "arguments": arguments},
    }
    stdout = io.StringIO()
    serve(home, stdin=io.StringIO(json.dumps(request) + "\n"), stdout=stdout)
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    return [json.loads(reply["result"]["content"][0]["text"]) for reply in replies]


def _ledger_rows(home: Path) -> list[dict]:
    path = home / "_local" / "usage.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class PreflightSurfaceParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = {key: os.environ.get(key) for key in ("PORTWRIGHT_JEV_FIXTURES", "TYPESAFE_API_KEY")}
        for key in self._env:
            os.environ.pop(key, None)  # JEV off: the decision must come from the cache alone

    def tearDown(self) -> None:
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _home(self, directory: str) -> tuple[Path, Path]:
        home = Path(directory)
        project = home / "project"
        project.mkdir()
        write_service(home, "acme")
        write_profile(home, "alpha", project, github="acct-alpha", db="postgres:alpha", services=("acme",))
        (home / "install").mkdir()
        (home / "install" / "tiers.json").write_text('{"rules": [{"tier": "confirm"}]}', encoding="utf-8")
        return home, project

    def test_cli_and_mcp_take_the_same_decision_and_write_the_same_ledger_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, project = self._home(directory)
            self.assertEqual(_ledger_rows(home), [])

            run = run_cli(
                "preflight", "acme", "--intent", "call", "--cwd", str(project), "--json", "--home", str(home),
                fixtures=None,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            cli_decision = json.loads(run.stdout)

            mcp_decision = _mcp_session(home, {"service": "acme", "intent": "call", "cwd": str(project)})[0]

            self.assertEqual(cli_decision["state"], "ready")
            self.assertEqual(cli_decision, mcp_decision)

            rows = _ledger_rows(home)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0], rows[1])
            self.assertEqual(
                set(rows[0]),
                {"date", "service", "profile", "freshness", "tier", "intent", "procedure"},
            )
            self.assertEqual(rows[0]["service"], "acme")
            self.assertEqual(rows[0]["intent"], "call")
            self.assertEqual(rows[0]["profile"], "alpha")
            self.assertEqual(rows[0]["procedure"], "services/acme.md")

    def test_reused_session_engine_records_every_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, project = self._home(directory)
            arguments = {"service": "acme", "intent": "call", "cwd": str(project)}

            first = _mcp_session(home, arguments)
            second = _mcp_session(home, arguments)

            self.assertEqual(first, second)
            self.assertEqual(len(_ledger_rows(home)), 2)


if __name__ == "__main__":
    unittest.main()
