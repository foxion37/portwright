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
from tests._v2 import FIXTURES, write_profile, write_service  # noqa: E402


def _serve(home: Path, lines: list[str]) -> list[dict]:
    stdout = io.StringIO()
    serve(home, stdin=io.StringIO("\n".join(lines) + "\n"), stdout=stdout)
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def _roundtrip(home: Path, *requests: dict) -> list[dict]:
    lines = [json.dumps(request) for request in requests]
    return _serve(home, lines)


def _call(tool: str, arguments: dict | None = None, request_id: int = 2) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments or {}},
    }


def _payload(reply: dict) -> dict:
    return json.loads(reply["result"]["content"][0]["text"])


class McpServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = {
            "PORTWRIGHT_JEV_FIXTURES": os.environ.get("PORTWRIGHT_JEV_FIXTURES"),
            "TYPESAFE_API_KEY": os.environ.get("TYPESAFE_API_KEY"),
        }
        os.environ["PORTWRIGHT_JEV_FIXTURES"] = str(FIXTURES)
        os.environ.pop("TYPESAFE_API_KEY", None)

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

    def test_handshake_and_tools_list(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, _ = self._home(directory)
            replies = _roundtrip(
                home,
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
            )
            self.assertEqual(len(replies), 2)
            self.assertEqual(replies[0]["result"]["protocolVersion"], "2024-11-05")
            self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "portwright")
            self.assertEqual(replies[0]["result"]["capabilities"], {"tools": {}})
            names = [tool["name"] for tool in replies[1]["result"]["tools"]]
            self.assertEqual(sorted(names), ["get_note", "preflight", "status"])
            for tool in replies[1]["result"]["tools"]:
                self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_initialize_defaults_unknown_protocol_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, _ = self._home(directory)
            replies = _roundtrip(
                home,
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}},
            )
            self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-06-18")

    def test_preflight_returns_ready_with_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, project = self._home(directory)
            replies = _roundtrip(home, _call("preflight", {"service": "acme", "cwd": str(project)}))
            payload = _payload(replies[0])
            self.assertFalse(replies[0]["result"]["isError"])
            self.assertEqual(payload["state"], "ready")
            self.assertEqual(payload["profile"]["id"], "alpha")

    def test_get_note_reads_and_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, _ = self._home(directory)
            link = home / "services" / "linked.md"
            link.symlink_to("/etc/passwd")
            replies = _roundtrip(
                home,
                _call("get_note", {"path": "services/acme.md"}, request_id=2),
                _call("get_note", {"path": "../x.md"}, request_id=3),
                _call("get_note", {"path": "/etc/passwd"}, request_id=4),
                _call("get_note", {"path": "services/linked.md"}, request_id=5),
                _call("get_note", {"path": "services/acme.txt"}, request_id=6),
            )
            note = _payload(replies[0])
            self.assertEqual(note["path"], "services/acme.md")
            self.assertIn("tool deploy --prod", note["text"])
            for reply in replies[1:]:
                self.assertTrue(reply["result"]["isError"], reply)
                self.assertNotIn(str(home), reply["result"]["content"][0]["text"])

    def test_status_counts_and_fixture_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, _ = self._home(directory)
            write_service(home, "secret", private=True)
            draft = home / "failures" / "_drafts"
            draft.mkdir(parents=True)
            (draft / "2026-09-01-acme-wip.md").write_text("draft", encoding="utf-8")
            replies = _roundtrip(home, _call("status"))
            payload = _payload(replies[0])
            self.assertEqual(payload["version"], "2.0.0")
            self.assertEqual(payload["root_name"], home.name)
            self.assertEqual(payload["notes"]["services"], 1)
            self.assertEqual(payload["notes"]["profiles"], 1)
            self.assertEqual(payload["notes"]["private"], 1)
            self.assertEqual(payload["notes"]["drafts"], 1)
            self.assertEqual(payload["jev_mode"], "fixture")
            self.assertEqual(payload["tier_rules"], 1)

    def test_unknown_method_and_malformed_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, _ = self._home(directory)
            replies = _serve(home, [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "resources/list"}),
                "{not json",
            ])
            self.assertEqual(replies[0]["error"]["code"], -32601)
            self.assertEqual(replies[1]["id"], None)
            self.assertEqual(replies[1]["error"]["code"], -32700)

    def test_invalid_params_and_unknown_tool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, _ = self._home(directory)
            replies = _roundtrip(
                home,
                _call("preflight", {}, request_id=1),
                _call("nope", {}, request_id=2),
            )
            self.assertEqual(replies[0]["error"]["code"], -32602)
            self.assertEqual(replies[1]["error"]["code"], -32602)


if __name__ == "__main__":
    unittest.main()
