"""portwright-skills over stdio: the five tools across local folders and the personal Hub."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from portwright import skills_mcp  # noqa: E402
from portwright.skills_local import SkillsConfig  # noqa: E402
from portwright.skills_remote import RemoteSkills  # noqa: E402
from tests.test_skills_local import digest, skill_md  # noqa: E402
from tests.test_skills_remote import TOKEN, FakeWorker  # noqa: E402

REMOTE_SKILL = "skill://portwright/personal/aim/SKILL.md"


class SkillsServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.codex = self.tmp / "codex"
        (self.codex / "re0" / "references").mkdir(parents=True)
        (self.codex / "re0" / "SKILL.md").write_text(skill_md("re0", "Rebuild aim from zero."))
        (self.codex / "re0" / "references" / "g.md").write_text("guide\n")
        self.worker = FakeWorker()
        self.addCleanup(self.worker.close)
        token = self.tmp / "token"
        token.write_text(TOKEN)
        os.chmod(token, 0o600)
        self.remote = RemoteSkills(self.worker.url, token, allow_http=True)
        self.config = SkillsConfig(local={"codex": self.codex})

    def session(self, calls: list[tuple[str, dict]], remote=None) -> list[dict]:
        lines = [json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})]
        lines += [json.dumps({"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": n, "arguments": a}})
                  for i, (n, a) in enumerate(calls, 1)]
        out = io.StringIO()
        skills_mcp.serve_config(self.config, remote=self.remote if remote is None else remote,
                                stdin=io.StringIO("\n".join(lines) + "\n"), stdout=out)
        replies = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "portwright-skills")
        return replies[1:]

    @staticmethod
    def payload(reply: dict) -> tuple[bool, dict]:
        result = reply["result"]
        return result["isError"], json.loads(result["content"][0]["text"])

    def test_stdio_round_trip_all_five_tools(self):
        local = "skill://portwright/local/codex/re0/SKILL.md"
        new_text = skill_md("re0", "Updated aim.")
        replies = self.session([
            ("search_skills", {"query": "aim"}),
            ("load_skill", {"uri": local}),
            ("read_skill_file", {"skill_uri": local, "uri": "skill://portwright/local/codex/re0/references/g.md"}),
            ("load_skill", {"uri": REMOTE_SKILL}),
            ("read_skill_file", {"skill_uri": REMOTE_SKILL, "uri": "skill://portwright/personal/aim/references/r.md"}),
            ("update_skill", {"uri": local, "markdown": new_text, "expected_digest": digest((self.codex / "re0" / "SKILL.md").read_bytes())}),
            ("create_skill", {"name": "fresh", "markdown": skill_md("fresh")}),
            ("search_skills", {"query": "fresh"}),
        ])
        results = [self.payload(r) for r in replies]
        self.assertEqual([err for err, _ in results], [False] * 8, results)
        found = results[0][1]
        self.assertEqual({(s["uri"], s["source"]) for s in found["skills"]},
                         {(local, "local:codex"), (REMOTE_SKILL, "remote:personal")})
        self.assertFalse(found["remote_unavailable"])
        self.assertEqual(results[1][1]["files"], ["SKILL.md", "references/g.md"])
        self.assertEqual(results[2][1]["text"], "guide\n")
        self.assertEqual(results[3][1]["digest"], results[0][1]["skills"][[s["uri"] for s in found["skills"]].index(REMOTE_SKILL)]["digest"])
        self.assertEqual(results[4][1]["text"], "reference\n")
        self.assertEqual(results[5][1]["digest"], digest(new_text.encode()))
        self.assertEqual(results[6][1]["uri"], "skill://portwright/local/codex/fresh/SKILL.md")
        self.assertIn("skill://portwright/local/codex/fresh/SKILL.md", {s["uri"] for s in results[7][1]["skills"]},
                      "a write is visible to the next search despite the kept local catalog")

    def test_search_survives_remote_failure(self):
        self.worker.status = 503
        error, found = self.payload(self.session([("search_skills", {"query": "aim"})])[0])
        self.assertFalse(error)
        self.assertTrue(found["remote_unavailable"])
        self.assertEqual(found["remote_error"], "remote_unavailable")
        self.assertEqual([s["source"] for s in found["skills"]], ["local:codex"])

    def test_remote_configuration_errors_are_reported(self):
        os.chmod(self.tmp / "token", 0o644)
        found = self.payload(self.session([("search_skills", {"query": "aim"})])[0])[1]
        self.assertEqual(found["remote_error"], "token_file_invalid")
        out = io.StringIO()
        config = SkillsConfig(local={"codex": self.codex}, remote_url="http://insecure.example/mcp", token_file=self.tmp / "token")
        skills_mcp.serve_config(config, stdin=io.StringIO(json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_skills", "arguments": {}}}) + "\n"), stdout=out)
        self.assertEqual(json.loads(json.loads(out.getvalue())["result"]["content"][0]["text"])["remote_error"], "url_invalid")

    def test_local_load_digest_matches_returned_markdown(self):
        server = skills_mcp.SkillsServer(self.config, None)
        uri = "skill://portwright/local/codex/re0/SKILL.md"
        stale = server.local.entry_for(uri)
        (self.codex / "re0" / "SKILL.md").write_text(skill_md("re0", "Edited elsewhere."))
        server.local.entry_for = lambda _uri: stale
        loaded = server.load(uri, None)
        self.assertEqual(loaded["digest"], digest(loaded["markdown"].encode()))

    def test_write_to_remote_uri_is_refused_and_errors_are_codes(self):
        replies = self.session([
            ("update_skill", {"uri": REMOTE_SKILL, "markdown": "x", "expected_digest": "sha256:" + "0" * 64}),
            ("read_skill_file", {"skill_uri": "skill://portwright/local/codex/re0/SKILL.md", "uri": "skill://portwright/local/codex/other/SKILL.md"}),
            ("create_skill", {"name": "re0", "markdown": skill_md("re0")}),
        ])
        self.assertEqual([self.payload(r) for r in replies],
                         [(True, {"error": "not_local"}), (True, {"error": "not_in_skill"}), (True, {"error": "exists"})])

    def test_bad_arguments_are_invalid_params(self):
        reply = self.session([("search_skills", {"query": 3}), ("load_skill", {})])
        self.assertEqual([r["error"]["code"] for r in reply], [-32602, -32602])

    def test_no_remote_configured(self):
        found = self.payload(self.session([("search_skills", {"query": ""})], remote=False)[0])[1]
        self.assertEqual([s["source"] for s in found["skills"]], ["local:codex"])
        self.assertTrue(found["remote_unavailable"])
        self.assertEqual(found["remote_error"], "not_configured")


if __name__ == "__main__":
    unittest.main()
