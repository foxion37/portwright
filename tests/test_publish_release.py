#!/usr/bin/env python3
"""Tests for scripts/publish_release.py: digest-bound manifest verification, nested
personal skill URIs, watch-sources as a signed (digest-bound) release file, origin and
redirect refusal, and uncertain-promote resolution. All HTTP is a fake local transport;
no credentials or network are used.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import publish_release as pub  # noqa: E402

COMMIT = "a" * 40


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_out(root: Path, files: dict[str, bytes], skills: dict[str, str]) -> Path:
    """Write a bundle output directory plus a builder manifest listing every file."""
    out = root / "bundles"
    for relative, data in files.items():
        path = out / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = {
        "commit": COMMIT,
        "skills": {name: {"path": path, "sha256": sha256(files[path]), "lessons": 0} for name, path in skills.items()},
        "files": [{"path": rel, "sha256": sha256(data), "size": len(data)} for rel, data in sorted(files.items())],
        "skipped_not_distributable": [],
    }
    for skill in manifest["skills"].values():
        prefix = skill["path"].removesuffix("SKILL.md")
        skill["files"] = [entry for entry in manifest["files"] if entry["path"].startswith(prefix)]
    (out / "inventory.json").write_text(json.dumps(manifest), encoding="utf-8")
    (out / ".portwright-build").write_text("marker\n", encoding="utf-8")
    return out


def skill_md(name: str, extra: str = "") -> bytes:
    lines = ["---", f"name: {name}", "description: synthetic test skill", *([extra] if extra else []), "---", "", f"# {name}", ""]
    return "\n".join(lines).encode()


class FakeResponse:
    def __init__(self, status: int, payload: dict | None = None, headers: dict | None = None):
        self.status = status
        self.headers = headers or {}
        self._payload = json.dumps(payload).encode() if payload is not None else b""

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeTransport:
    """In-memory gisul Worker: records calls, stores uploads, answers admin routes."""

    def __init__(self, current: dict | None = None, promote_status: int = 200, verify_status: int = 200, lands_on_error: bool = True):
        self.current = current
        self.promote_status = promote_status
        self.verify_status = verify_status
        self.lands_on_error = lands_on_error
        self.calls: list[tuple[str, str]] = []
        self.objects: dict[str, bytes] = {}
        self.inventory_digest: str | None = None

    def __call__(self, req: urllib.request.Request, timeout: int = 0):
        url = req.full_url
        method = req.get_method()
        path = url.split("://", 1)[1].split("/", 1)[1]
        self.calls.append((method, "/" + path))
        if method == "GET" and path == "admin/current":
            if self.current is None:
                return FakeResponse(404, {"error": "none"})
            return FakeResponse(200, {"current": self.current, "etag": "W/1"})
        if method == "PUT" and path.startswith("admin/releases/"):
            relative = path.split("/", 3)[3]
            data = req.data or b""
            self.objects[relative] = data
            if relative == "inventory.json":
                self.inventory_digest = "sha256:" + sha256(data)
            return FakeResponse(201, {"created": True})
        if method == "POST" and path == "admin/verify":
            if self.verify_status != 200:
                raise urllib.error.HTTPError(url, self.verify_status, "verify failed", {}, io.BytesIO(b'{"error":"secret-token-xyz"}'))
            return FakeResponse(200, {"ok": True})
        if method == "POST" and path == "admin/promote":
            body = json.loads(req.data)
            if self.promote_status == 200:
                self.current = {k: body[k] for k in ("commit", "release", "inventory_digest")}
                return FakeResponse(200, {"ok": True})
            if self.lands_on_error:
                self.current = {"commit": body["commit"], "release": body["release"], "inventory_digest": self.inventory_digest}
            raise urllib.error.HTTPError(url, self.promote_status, "promote failed", {}, io.BytesIO(b'{"error":"secret-token-xyz"}'))
        raise AssertionError(f"unexpected request {method} /{path}")


def publisher_env(transport: FakeTransport) -> dict:
    return {
        "GISUL_PUBLISH_ORIGIN": "https://gisul.test",
        "GISUL_PUBLISH_TOKEN": "test-token",
        "GISUL_RELEASE_SEQUENCE": "7",
    }


def run_main(out: Path, transport: FakeTransport, env: dict | None = None) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with mock.patch.object(pub, "urlopen", transport), mock.patch.dict(os.environ, env or publisher_env(transport)), \
            mock.patch.object(sys, "stdout", stdout), mock.patch.object(sys, "stderr", stderr):
        code = pub.main([str(out)])
    return code, stdout.getvalue(), stderr.getvalue()


class TestManifestVerification(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.files = {
            "github/SKILL.md": skill_md("github"),
            "github/references/cli.md": b"# gh\n",
            "personal/my-skill/SKILL.md": skill_md("my-skill", "disable-model-invocation: true"),
            "watch-sources.json": json.dumps({"sources": [{"skill": "github", "url": "https://cli.github.com/manual/gh_auth_switch"}]}).encode(),
        }
        self.skills = {"github": "github/SKILL.md", "my-skill": "personal/my-skill/SKILL.md"}
        self.out = make_out(Path(self.tmp.name), dict(self.files), self.skills)

    def test_unlisted_file_rejected(self) -> None:
        (self.out / "github" / "extra.md").write_text("smuggled")
        with self.assertRaises(SystemExit):
            pub.verify_output(self.out, pub.load_manifest(self.out))

    def test_tampered_file_rejected(self) -> None:
        (self.out / "github" / "SKILL.md").write_bytes(b"tampered")
        with self.assertRaises(SystemExit):
            pub.verify_output(self.out, pub.load_manifest(self.out))

    def test_missing_file_rejected(self) -> None:
        (self.out / "github" / "references" / "cli.md").unlink()
        with self.assertRaises(SystemExit):
            pub.verify_output(self.out, pub.load_manifest(self.out))

    def test_global_only_personal_resource_cannot_bypass_approval(self) -> None:
        path = "personal/my-skill/extra.md"
        content = b"not included in the approved package"
        (self.out / path).write_bytes(content)
        manifest_path = self.out / "inventory.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"].append({"path": path, "sha256": sha256(content), "size": len(content)})
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaises(SystemExit):
            pub.validated_inventory(self.out)

    def test_public_skill_cannot_claim_a_personal_resource(self) -> None:
        manifest_path = self.out / "inventory.json"
        manifest = json.loads(manifest_path.read_text())
        personal = next(entry for entry in manifest["files"] if entry["path"].startswith("personal/"))
        manifest["skills"]["github"]["files"].append(personal)
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaises(SystemExit):
            pub.validated_inventory(self.out)

    def test_nested_personal_uri_and_frontmatter(self) -> None:
        manifest = pub.load_manifest(self.out)
        objects = pub.verify_output(self.out, manifest)
        inventory, _ = pub.build_inventory(manifest, objects, COMMIT)
        personal = next(s for s in inventory["skills"] if "personal" in s["uri"])
        self.assertEqual(personal["uri"], "skill://gisul/portwright/personal/my-skill/SKILL.md")
        self.assertEqual(personal["frontmatter"]["name"], "my-skill")
        self.assertIs(personal["frontmatter"]["disable-model-invocation"], True)
        uris = {r["uri"] for r in personal["resources"]}
        self.assertEqual(uris, {"skill://gisul/portwright/personal/my-skill/SKILL.md"})

    def test_watch_sources_signed_without_uri(self) -> None:
        manifest = pub.load_manifest(self.out)
        objects = pub.verify_output(self.out, manifest)
        inventory, objects = pub.build_inventory(manifest, objects, COMMIT)
        entry = next(f for f in inventory["files"] if f["path"] == "watch-sources.json")
        self.assertNotIn("uri", entry)
        self.assertEqual(entry["digest"], "sha256:" + sha256(self.files["watch-sources.json"]))
        self.assertEqual(entry["size"], len(self.files["watch-sources.json"]))
        # tampering with the signed metadata must refuse the release
        (self.out / "watch-sources.json").write_bytes(b'{"sources":[]}')
        with self.assertRaises(SystemExit):
            pub.verify_output(self.out, pub.load_manifest(self.out))

    def test_release_json_has_no_uri(self) -> None:
        manifest = pub.load_manifest(self.out)
        objects = pub.verify_output(self.out, manifest)
        inventory, _ = pub.build_inventory(manifest, objects, COMMIT)
        entry = next(f for f in inventory["files"] if f["path"] == "release.json")
        self.assertNotIn("uri", entry)
        self.assertEqual(inventory["schema_version"], 1)


class TestTransportPolicy(unittest.TestCase):
    def test_origin_validation(self) -> None:
        for bad in ("http://gisul.test", "https://user:pw@gisul.test", "https://gisul.test/path",
                    "https://gisul.test/?q=1", "https://gisul.test/#frag", "gisul.test", ""):
            with self.assertRaises(SystemExit, msg=bad):
                pub.clean_origin(bad)
        self.assertEqual(pub.clean_origin("https://gisul.test/"), "https://gisul.test")
        self.assertEqual(pub.clean_origin("https://gisul.test:8443"), "https://gisul.test:8443")

    def test_redirect_refused(self) -> None:
        def redirector(req, timeout=0):
            raise urllib.error.HTTPError(req.full_url, 302, "Found", {"Location": "https://evil.test"}, io.BytesIO(b""))
        with mock.patch.object(pub, "urlopen", redirector):
            status, _, _ = pub.request("GET", "https://gisul.test/admin/current", "t")
        self.assertEqual(status, 302)

    def test_error_body_not_echoed(self) -> None:
        transport = FakeTransport(verify_status=500)
        out = make_out(Path(tempfile.mkdtemp()), {"github/SKILL.md": skill_md("github")}, {"github": "github/SKILL.md"})
        code, _, stderr = run_main(out, transport)
        self.assertEqual(code, 1)
        self.assertIn("HTTP 500", stderr)
        self.assertNotIn("secret-token-xyz", stderr)
        self.assertNotIn("test-token", stderr)

    def test_network_error_fails_safely(self) -> None:
        def broken(req, timeout=0):
            raise urllib.error.URLError("connection refused")
        out = make_out(Path(tempfile.mkdtemp()), {"github/SKILL.md": skill_md("github")}, {"github": "github/SKILL.md"})
        code, _, stderr = run_main(out, broken)
        self.assertEqual(code, 1)
        self.assertIn("HTTP 0", stderr)

    def test_promote_uncertain_rereads_current(self) -> None:
        transport = FakeTransport(promote_status=500)
        out = make_out(Path(tempfile.mkdtemp()), {"github/SKILL.md": skill_md("github")}, {"github": "github/SKILL.md"})
        code, stdout, _ = run_main(out, transport)
        self.assertEqual(code, 0)
        self.assertIn("confirmed_via", stdout)
        promotes = [c for c in transport.calls if c[1] == "/admin/promote"]
        self.assertEqual(len(promotes), 1)  # never retries the mutation blindly

    def test_promote_uncertain_reports_true_state(self) -> None:
        transport = FakeTransport(promote_status=500, lands_on_error=False)
        transport.current = {"commit": "b" * 40, "release": "other", "inventory_digest": "sha256:" + "0" * 64}
        out = make_out(Path(tempfile.mkdtemp()), {"github/SKILL.md": skill_md("github")}, {"github": "github/SKILL.md"})
        code, _, stderr = run_main(out, transport)
        self.assertEqual(code, 1)
        self.assertIn("current pointer", stderr)

    def test_happy_path_order(self) -> None:
        transport = FakeTransport()
        out = make_out(Path(tempfile.mkdtemp()), {"github/SKILL.md": skill_md("github")}, {"github": "github/SKILL.md"})
        code, stdout, _ = run_main(out, transport)
        self.assertEqual(code, 0)
        self.assertIn("promoted", stdout)
        self.assertEqual(transport.calls[0], ("GET", "/admin/current"))
        self.assertEqual(transport.calls[1], ("PUT", f"/admin/releases/{COMMIT}/inventory.json"))
        self.assertEqual(transport.calls[-2:], [("POST", "/admin/verify"), ("POST", "/admin/promote")])


class TestFrontmatter(unittest.TestCase):
    def test_flat_subset(self) -> None:
        meta = pub._subset_yaml("name: x\ndescription: \"quoted desc\"\nflag: true\ncount: 3")
        self.assertEqual(meta, {"name": "x", "description": "quoted desc", "flag": True, "count": 3})

    def test_subset_refuses_nested(self) -> None:
        with self.assertRaises(SystemExit):
            pub._subset_yaml("name: x\nnested:\n  a: 1")

    def test_subset_refuses_block_scalar(self) -> None:
        with self.assertRaises(SystemExit):
            pub._subset_yaml("name: x\ndescription: |\n  folded")

    def test_node_yaml_when_available(self) -> None:
        if pub._node_yaml("name: probe") is None:
            self.skipTest("node + gisul yaml package not installed")
        meta = pub.frontmatter("---\nname: x\ndescription: >-\n  folded\n  text\nflag: true\n---\nbody\n")
        self.assertEqual(meta["description"], "folded text")
        self.assertIs(meta["flag"], True)


if __name__ == "__main__":
    unittest.main()
