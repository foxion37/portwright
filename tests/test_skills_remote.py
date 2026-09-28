"""Remote personal Hub reads for portwright-skills against a fake Worker."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from portwright import skills_remote as sr  # noqa: E402

TOKEN = "tok_" + "Q7x" * 13
COMMIT = "c" * 40
SKILL = b"---\nname: aim\ndescription: Propose intent.\n---\n# aim\n"
REF = b"reference\n"
BINARY = b"\x89PNG\r\n\x1a\n"


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class FakeWorker:
    def __init__(self):
        self.files = {"skill://portwright/personal/aim/SKILL.md": SKILL,
                      "skill://portwright/personal/aim/references/r.md": REF,
                      "skill://portwright/personal/aim/logo.png": BINARY}
        self.served = dict(self.files)
        self.requests: list[dict] = []
        self.status = 200
        self.redirect = False
        worker = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                worker.requests.append({"headers": dict(self.headers), "body": body})
                if worker.redirect:
                    self.send_response(302)
                    self.send_header("Location", "https://evil.example/mcp")
                    self.end_headers()
                    return
                if worker.status != 200:
                    self.send_response(worker.status)
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "bad token " + TOKEN}).encode())
                    return
                params = body.get("params") or {}
                meta = {"release": "r1", "commit": params.get("_meta", {}).get("io.portwright/commit", "a" * 40)}
                if body["method"] == "skills/list":
                    result = {"skills": [{"uri": "skill://portwright/personal/aim/SKILL.md",
                                          "frontmatter": {"name": "aim", "description": "Propose intent."},
                                          "resources": [{"uri": u, "digest": sha(d), "size": len(d)} for u, d in worker.files.items()]}],
                              "_meta": meta}
                else:
                    data = worker.served[params["uri"]]
                    try:
                        content = {"uri": params["uri"], "text": data.decode("utf-8")}
                    except UnicodeDecodeError:
                        content = {"uri": params["uri"], "blob": base64.b64encode(data).decode()}
                    result = {"contents": [content], "_meta": meta}
                raw = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/mcp"

    def close(self):
        self.server.shutdown()


class RemoteSkillsTest(unittest.TestCase):
    def setUp(self):
        self.worker = FakeWorker()
        self.addCleanup(self.worker.close)
        self.token_file = Path(tempfile.mkdtemp()) / "token"
        self.token_file.write_text(TOKEN + "\n")
        os.chmod(self.token_file, 0o600)
        self.remote = sr.RemoteSkills(self.worker.url, self.token_file, allow_http=True)

    def test_catalog_is_one_request_with_bearer_and_agent(self):
        entries = self.remote.entries()
        self.assertEqual([(e.uri, e.name, e.source) for e in entries],
                         [("skill://portwright/personal/aim/SKILL.md", "aim", "remote:personal")])
        self.assertEqual(entries[0].digest, sha(SKILL))
        self.remote.entries()
        self.assertEqual(len(self.worker.requests), 1, "the catalog is kept for the process")
        headers = self.worker.requests[0]["headers"]
        self.assertEqual(headers["Authorization"], "Bearer " + TOKEN)
        self.assertTrue(headers["User-Agent"].startswith("portwright-skills/"))
        self.assertNotIn("cursor", self.worker.requests[0]["body"]["params"])

    def test_load_verifies_and_lists_files(self):
        loaded = self.remote.load("skill://portwright/personal/aim/SKILL.md")
        self.assertEqual(loaded["markdown"], SKILL.decode())
        self.assertEqual(loaded["digest"], sha(SKILL))
        self.assertEqual(loaded["files"], ["SKILL.md", "logo.png", "references/r.md"])
        self.assertEqual(self.remote.read_file("skill://portwright/personal/aim/SKILL.md",
                                               "skill://portwright/personal/aim/references/r.md"), REF.decode())

    def test_digest_mismatch_is_refused_and_cache_cleared(self):
        self.remote.entries()
        self.worker.served["skill://portwright/personal/aim/SKILL.md"] = SKILL + b"tampered"
        with self.assertRaisesRegex(ValueError, "^verification_failed$"):
            self.remote.load("skill://portwright/personal/aim/SKILL.md")
        before = len(self.worker.requests)
        self.remote.entries()
        self.assertEqual(len(self.worker.requests), before + 1, "a failed verification drops the kept catalog")
        self.worker.served["skill://portwright/personal/aim/SKILL.md"] = SKILL
        self.remote.load("skill://portwright/personal/aim/SKILL.md")
        self.worker.served["skill://portwright/personal/aim/references/r.md"] = b"changed"
        with self.assertRaisesRegex(ValueError, "^verification_failed$"):
            self.remote.read_file("skill://portwright/personal/aim/SKILL.md", "skill://portwright/personal/aim/references/r.md")

    def test_read_file_requires_load_and_refuses_binary_and_foreign(self):
        skill = "skill://portwright/personal/aim/SKILL.md"
        with self.assertRaisesRegex(ValueError, "^not_loaded$"):
            self.remote.read_file(skill, "skill://portwright/personal/aim/references/r.md")
        self.remote.load(skill)
        with self.assertRaisesRegex(ValueError, "^binary_refused$"):
            self.remote.read_file(skill, "skill://portwright/personal/aim/logo.png")
        with self.assertRaisesRegex(ValueError, "^not_in_skill$"):
            self.remote.read_file(skill, "skill://portwright/personal/other/SKILL.md")

    def test_commit_pin_uses_portwright_key(self):
        self.remote.load("skill://portwright/personal/aim/SKILL.md", commit=COMMIT)
        metas = [r["body"]["params"].get("_meta", {}) for r in self.worker.requests]
        self.assertTrue(metas and all(m == {"io.portwright/commit": COMMIT} for m in metas), metas)
        with self.assertRaisesRegex(ValueError, "^commit_invalid$"):
            self.remote.load("skill://portwright/personal/aim/SKILL.md", commit="main")

    def test_token_file_missing_or_group_readable_is_refused(self):
        os.chmod(self.token_file, 0o640)
        with self.assertRaisesRegex(ValueError, "^token_file_invalid$"):
            self.remote.entries()
        missing = sr.RemoteSkills(self.worker.url, self.token_file.parent / "absent", allow_http=True)
        with self.assertRaisesRegex(ValueError, "^token_file_invalid$"):
            missing.entries()
        self.assertEqual(self.worker.requests, [])

    def test_token_never_appears_in_errors(self):
        self.worker.status = 401
        with self.assertRaises(ValueError) as caught:
            self.remote.entries()
        self.assertEqual(str(caught.exception), "remote_unavailable")
        self.assertNotIn(TOKEN, repr(caught.exception.__context__) + repr(caught.exception.__cause__))

    def test_redirect_is_refused(self):
        self.worker.redirect = True
        with self.assertRaisesRegex(ValueError, "^remote_unavailable$"):
            self.remote.entries()

    def test_plain_http_is_refused_outside_tests(self):
        with self.assertRaisesRegex(ValueError, "^url_invalid$"):
            sr.RemoteSkills(self.worker.url, self.token_file)


if __name__ == "__main__":
    unittest.main()
