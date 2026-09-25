#!/usr/bin/env python3
"""Tests for scripts/publish_release.py: digest-bound manifest verification (v1 personal,
v2 shared with note-index coverage), audience routing, and the upload/verify/promote
sequence with its ETag, sequence and lost-response handling.
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
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import publish_release as pub  # noqa: E402

COMMIT = "a" * 40
REVISION = "sha256:" + "d" * 64
NOTE_BYTES = "---\nid: alpha\n---\n\n# alpha\n".encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_out(root: Path, files: dict[str, bytes], skills: dict[str, str]) -> Path:
    """A v1 (personal) bundle output directory plus a builder manifest listing every file."""
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
        skill["files"] = [item for item in manifest["files"] if item["path"].startswith(prefix)]
    (out / "inventory.json").write_text(json.dumps(manifest), encoding="utf-8")
    (out / ".portwright-build").write_text("marker\n", encoding="utf-8")
    return out


def make_v2_out(root: Path, *, audience: str = "public", grade: str = "stable", revision: str = REVISION,
                note_id: str = "service/alpha", mangled: bool = False) -> Path:
    """A v2 (shared) bundle output directory with a covered note-index."""
    note_path = f"{grade}/alpha/notes/alpha.md"
    skill_path = f"{grade}/alpha/SKILL.md"
    files = {
        skill_path: f'---\nname: alpha\ndescription: "synthetic test package"\ngrade: {grade}\n---\n\n# alpha\n'.encode(),
        note_path: NOTE_BYTES,
    }
    index = {
        "schema_version": 1, "audience": audience, "commit": COMMIT,
        "notes": [{"note_id": note_id, "kind": "procedure", "service_id": "alpha", "revision": revision,
                   "grade": grade, "path": note_path,
                   "uri": f"skill://gisul/commons/{note_path}", "file_digest": "sha256:" + sha256(NOTE_BYTES),
                   "size": len(NOTE_BYTES)}],
    }
    index_bytes = (json.dumps(index, ensure_ascii=False, indent=2) + "\n").encode()
    manifest = {
        "schema_version": 2, "audience": audience, "commit": COMMIT,
        "skills": {f"{grade}/alpha": {
            "path": skill_path, "sha256": sha256(files[skill_path]), "grade": grade,
            "note_refs": [{"note_id": note_id, "revision": revision if not mangled else "sha256:" + "e" * 64}],
            "files": [{"path": rel, "sha256": sha256(data), "size": len(data)} for rel, data in sorted(files.items())],
        }},
        "files": [{"path": rel, "sha256": sha256(data), "size": len(data)} for rel, data in sorted(files.items())] + [
            {"path": "note-index.json", "sha256": sha256(index_bytes), "size": len(index_bytes)}],
        "note_index": {"path": "note-index.json", "digest": "sha256:" + sha256(index_bytes), "size": len(index_bytes)},
    }
    out = root / "bundles"
    for relative, data in {**files, "note-index.json": index_bytes}.items():
        path = out / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
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
    """In-memory Hub Worker: records calls, stores uploads, answers admin routes."""

    def __init__(self, current: dict | None = None, promote_status: int = 200, verify_status: int = 200, lands_on_error: bool = True):
        self.current = current
        self.promote_status = promote_status
        self.verify_status = verify_status
        self.lands_on_error = lands_on_error
        self.calls: list[tuple[str, str]] = []
        self.objects: dict[str, bytes] = {}
        self.inventory_digest: str | None = None
        self.promote_body: dict | None = None

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
            self.promote_body = body
            if self.promote_status == 200:
                self.current = {k: body[k] for k in ("commit", "release", "inventory_digest")} | {"sequence": body["sequence"]}
                return FakeResponse(200, {"ok": True})
            if self.lands_on_error:
                self.current = {"commit": body["commit"], "release": body["release"], "inventory_digest": self.inventory_digest}
            raise urllib.error.HTTPError(url, self.promote_status, "promote failed", {}, io.BytesIO(b'{"error":"secret-token-xyz"}'))
        raise AssertionError(f"unexpected request {method} /{path}")


def publisher_env() -> dict:
    return {"HUB_ORIGIN": "https://hub.test", "HUB_WORKFLOW_TOKEN": "test-token"}


def run_main(out: Path, transport, audience: str = "personal", env: dict | None = None, argv: list[str] | None = None) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with mock.patch.object(pub, "urlopen", transport), mock.patch.dict(os.environ, env if env is not None else publisher_env(), clear=False), \
            redirect_stdout(stdout), mock.patch.object(sys, "stderr", stderr):
        code = pub.main(argv if argv is not None else ["--out", str(out), "--audience", audience])
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
        personal = next(item for item in manifest["files"] if item["path"].startswith("personal/"))
        manifest["skills"]["github"]["files"].append(personal)
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaises(SystemExit):
            pub.validated_inventory(self.out)

    def test_nested_personal_uri_and_frontmatter(self) -> None:
        manifest = pub.load_manifest(self.out)
        objects = pub.verify_output(self.out, manifest)
        inventory, _ = pub.build_inventory(manifest, objects, COMMIT, audience="personal", source="portwright")
        personal = next(s for s in inventory["skills"] if "personal" in s["uri"])
        self.assertEqual(personal["uri"], "skill://gisul/portwright/personal/my-skill/SKILL.md")
        self.assertEqual(personal["frontmatter"]["name"], "my-skill")
        self.assertIs(personal["frontmatter"]["disable-model-invocation"], True)
        uris = {r["uri"] for r in personal["resources"]}
        self.assertEqual(uris, {"skill://gisul/portwright/personal/my-skill/SKILL.md"})

    def test_watch_sources_signed_without_uri(self) -> None:
        manifest = pub.load_manifest(self.out)
        objects = pub.verify_output(self.out, manifest)
        inventory, objects = pub.build_inventory(manifest, objects, COMMIT, audience="personal", source="portwright")
        item = next(f for f in inventory["files"] if f["path"] == "watch-sources.json")
        self.assertNotIn("uri", item)
        self.assertEqual(item["digest"], "sha256:" + sha256(self.files["watch-sources.json"]))
        self.assertEqual(item["size"], len(self.files["watch-sources.json"]))
        (self.out / "watch-sources.json").write_bytes(b'{"sources":[]}')
        with self.assertRaises(SystemExit):
            pub.verify_output(self.out, pub.load_manifest(self.out))

    def test_v1_release_json_keeps_the_published_shape(self) -> None:
        manifest = pub.load_manifest(self.out)
        objects = pub.verify_output(self.out, manifest)
        inventory, _ = pub.build_inventory(manifest, objects, COMMIT, audience="personal", source="portwright")
        item = next(f for f in inventory["files"] if f["path"] == "release.json")
        self.assertNotIn("uri", item)
        self.assertEqual(inventory["schema_version"], 1)
        self.assertNotIn("audience", inventory)
        self.assertEqual(list(inventory), ["schema_version", "commit", "release", "skills", "files", "aliases"])


class TestSharedInventory(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = make_v2_out(Path(self.tmp.name))

    def test_v2_inventory_preserves_grade_note_refs_and_note_index(self) -> None:
        manifest = pub.validated_inventory(self.out)
        objects = pub.verify_output(self.out, manifest)
        inventory, objects = pub.build_inventory(manifest, objects, COMMIT, audience="public", source="commons")
        self.assertEqual(inventory["schema_version"], 2)
        self.assertEqual(inventory["audience"], "public")
        skill = inventory["skills"][0]
        self.assertEqual(skill["uri"], "skill://gisul/commons/stable/alpha/SKILL.md")
        self.assertEqual(skill["grade"], "stable")
        self.assertNotIn("origin", skill)  # the Hub reader refuses origin on commons packages
        self.assertEqual(skill["note_refs"], [{"note_id": "service/alpha", "revision": REVISION}])
        index_bytes = objects["note-index.json"]
        self.assertEqual(inventory["note_index"], {"path": "note-index.json", "digest": "sha256:" + sha256(index_bytes), "size": len(index_bytes)})
        self.assertIn("note-index.json", objects)

    def test_note_refs_must_cover_the_note_index(self) -> None:
        with self.assertRaises(SystemExit):
            pub.validated_inventory(make_v2_out(Path(self.tmp.name) / "mangled", mangled=True))

    def test_v1_manifest_is_refused_for_a_shared_audience(self) -> None:
        out = make_out(Path(self.tmp.name) / "v1", {"github/SKILL.md": skill_md("github")}, {"github": "github/SKILL.md"})
        with self.assertRaises(SystemExit):
            pub.publish(out, "", "", "public", dry_run=True)

    def test_v2_manifest_is_refused_for_the_personal_audience(self) -> None:
        with self.assertRaises(SystemExit):
            pub.publish(self.out, "", "", "personal", dry_run=True)

    def test_tampered_note_index_is_refused(self) -> None:
        index_path = self.out / "note-index.json"
        payload = json.loads(index_path.read_text())
        payload["notes"][0]["size"] = 1
        index_path.write_text(json.dumps(payload))
        with self.assertRaises(SystemExit):
            pub.validated_inventory(self.out)

    def test_dry_run_reports_identity_without_publishing(self) -> None:
        result = pub.publish(self.out, "", "", "public", dry_run=True)
        self.assertFalse(result["promoted"])
        self.assertIsNone(result["sequence"])
        self.assertEqual(result["audience"], "public")
        released = json.loads((self.out / "inventory.gisul.json").read_text(encoding="utf-8"))
        self.assertEqual(released["schema_version"], 2)


class TestTransportPolicy(unittest.TestCase):
    def test_origin_validation(self) -> None:
        for bad in ("http://hub.test", "https://user:pw@hub.test", "https://hub.test/path",
                    "https://hub.test/?q=1", "https://hub.test/#frag", "hub.test", ""):
            with self.assertRaises(SystemExit, msg=bad):
                pub.clean_origin(bad)
        self.assertEqual(pub.clean_origin("https://hub.test/"), "https://hub.test")
        self.assertEqual(pub.clean_origin("https://hub.test:8443"), "https://hub.test:8443")

    def test_redirect_refused(self) -> None:
        def redirector(req, timeout=0):
            raise urllib.error.HTTPError(req.full_url, 302, "Found", {"Location": "https://evil.test"}, io.BytesIO(b""))
        with mock.patch.object(pub, "urlopen", redirector):
            status, _, _ = pub.request("GET", "https://hub.test/admin/current", "t")
        self.assertEqual(status, 302)

    def test_audience_is_required_and_routes_the_source(self) -> None:
        self.assertEqual(pub.AUDIENCE_SOURCE, {"personal": "portwright", "public": "commons", "company": "commons"})
        out = make_out(Path(tempfile.mkdtemp()), {"github/SKILL.md": skill_md("github")}, {"github": "github/SKILL.md"})
        code, _, stderr = run_main(out, FakeTransport(), env={}, argv=["--out", str(out)])
        self.assertEqual(code, 1)
        self.assertIn("audience", stderr.lower())

    def test_sequence_is_current_plus_one_with_the_pointer_etag(self) -> None:
        transport = FakeTransport(current={"commit": "b" * 40, "release": "old", "inventory_digest": "sha256:" + "0" * 64, "sequence": 41})
        out = make_out(Path(tempfile.mkdtemp()), {"github/SKILL.md": skill_md("github")}, {"github": "github/SKILL.md"})
        code, stdout, _ = run_main(out, transport, audience="personal")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(stdout.strip().splitlines()[-1])["promoted"])
        self.assertEqual(transport.promote_body["sequence"], 42)
        self.assertEqual(transport.promote_body["expected_etag"], "W/1")

    def test_first_release_uses_sequence_one(self) -> None:
        transport = FakeTransport()
        out = make_out(Path(tempfile.mkdtemp()), {"github/SKILL.md": skill_md("github")}, {"github": "github/SKILL.md"})
        code, _, _ = run_main(out, transport, audience="personal")
        self.assertEqual(code, 0)
        self.assertEqual(transport.promote_body["sequence"], 1)
        self.assertIsNone(transport.promote_body["expected_etag"])

    def test_rerun_of_a_landed_release_does_not_mutate(self) -> None:
        """A promote whose response was lost is completed by re-reading, never re-sent."""
        out = make_out(Path(tempfile.mkdtemp()), {"github/SKILL.md": skill_md("github")}, {"github": "github/SKILL.md"})
        identity = pub.publish(out, "", "", "personal", dry_run=True)
        transport = FakeTransport(current={k: identity[k] for k in ("commit", "release", "inventory_digest")} | {"sequence": 41})
        code, stdout, _ = run_main(out, transport, audience="personal")
        self.assertEqual(code, 0)
        result = json.loads(stdout.strip())
        self.assertTrue(result["promoted"])
        self.assertEqual(result["confirmed_via"], "admin/current")
        self.assertEqual(result["sequence"], 41)  # the pointer's own sequence, not a new one
        self.assertEqual(transport.calls, [("GET", "/admin/current")])

    def test_promote_uncertain_with_another_identity_fails(self) -> None:
        """A matching inventory digest is not enough: commit and release must match too."""
        out = make_out(Path(tempfile.mkdtemp()), {"github/SKILL.md": skill_md("github")}, {"github": "github/SKILL.md"})
        identity = pub.publish(out, "", "", "personal", dry_run=True)
        transport = FakeTransport(promote_status=500, lands_on_error=False)
        transport.current = {"commit": "b" * 40, "release": "other", "inventory_digest": identity["inventory_digest"], "sequence": 7}
        code, _, stderr = run_main(out, transport, audience="personal")
        self.assertEqual(code, 1)
        self.assertIn("promote failed: HTTP 500", stderr)
        self.assertNotIn("other", stderr)

    def test_error_body_not_echoed(self) -> None:
        transport = FakeTransport(verify_status=500)
        out = make_out(Path(tempfile.mkdtemp()), {"github/SKILL.md": skill_md("github")}, {"github": "github/SKILL.md"})
        code, _, stderr = run_main(out, transport, audience="personal")
        self.assertEqual(code, 1)
        self.assertIn("HTTP 500", stderr)
        self.assertNotIn("secret-token-xyz", stderr)
        self.assertNotIn("test-token", stderr)

    def test_network_error_fails_safely(self) -> None:
        def broken(req, timeout=0):
            raise urllib.error.URLError("connection refused")
        out = make_out(Path(tempfile.mkdtemp()), {"github/SKILL.md": skill_md("github")}, {"github": "github/SKILL.md"})
        code, _, stderr = run_main(out, broken, audience="personal")
        self.assertEqual(code, 1)
        self.assertIn("HTTP 0", stderr)

    def test_promote_uncertain_rereads_current(self) -> None:
        transport = FakeTransport(promote_status=500)
        out = make_out(Path(tempfile.mkdtemp()), {"github/SKILL.md": skill_md("github")}, {"github": "github/SKILL.md"})
        code, stdout, _ = run_main(out, transport, audience="personal")
        self.assertEqual(code, 0)
        self.assertIn("confirmed_via", stdout)
        promotes = [c for c in transport.calls if c[1] == "/admin/promote"]
        self.assertEqual(len(promotes), 1)  # never retries the mutation blindly

    def test_promote_uncertain_reports_only_fixed_codes(self) -> None:
        transport = FakeTransport(promote_status=500, lands_on_error=False)
        transport.current = {"commit": "b" * 40, "release": "other", "inventory_digest": "sha256:" + "0" * 64}
        out = make_out(Path(tempfile.mkdtemp()), {"github/SKILL.md": skill_md("github")}, {"github": "github/SKILL.md"})
        code, _, stderr = run_main(out, transport, audience="personal")
        self.assertEqual(code, 1)
        self.assertIn("promote failed: HTTP 500", stderr)
        self.assertNotIn("other", stderr)  # the pointer body is never echoed
        self.assertNotIn("secret-token-xyz", stderr)
        self.assertNotIn("test-token", stderr)

    def test_happy_path_order(self) -> None:
        transport = FakeTransport()
        out = make_out(Path(tempfile.mkdtemp()), {"github/SKILL.md": skill_md("github")}, {"github": "github/SKILL.md"})
        code, stdout, _ = run_main(out, transport, audience="personal")
        self.assertEqual(code, 0)
        self.assertIn("promoted", stdout)
        self.assertEqual(transport.calls[0], ("GET", "/admin/current"))
        self.assertEqual(transport.calls[1], ("PUT", f"/admin/releases/{COMMIT}/inventory.json"))
        self.assertEqual(transport.calls[-2:], [("POST", "/admin/verify"), ("POST", "/admin/promote")])

    def test_dry_run_never_touches_the_network(self) -> None:
        transport = FakeTransport()
        out = make_out(Path(tempfile.mkdtemp()), {"github/SKILL.md": skill_md("github")}, {"github": "github/SKILL.md"})
        code, stdout, _ = run_main(out, transport, audience="personal", argv=["--out", str(out), "--audience", "personal", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertEqual(transport.calls, [])
        self.assertFalse(json.loads(stdout.strip())["promoted"])
        self.assertTrue((out / "inventory.gisul.json").is_file())

    def test_missing_token_fails_before_any_request(self) -> None:
        transport = FakeTransport()
        out = make_out(Path(tempfile.mkdtemp()), {"github/SKILL.md": skill_md("github")}, {"github": "github/SKILL.md"})
        code, _, stderr = run_main(out, transport, audience="personal", env={"HUB_ORIGIN": "https://hub.test"})
        self.assertEqual(code, 1)
        self.assertIn("HUB_WORKFLOW_TOKEN", stderr)
        self.assertEqual(transport.calls, [])


class TestFrontmatter(unittest.TestCase):
    def test_flat_scalars_parse(self) -> None:
        meta = pub.frontmatter('---\nname: x\ndescription: "quoted desc"\nflag: true\ncount: 3\n---\nbody\n')
        self.assertEqual(meta, {"name": "x", "description": "quoted desc", "flag": True, "count": 3})

    def test_missing_name_or_description_is_refused(self) -> None:
        for text in ("---\nname: x\n---\n", "---\ndescription: d\n---\n", "no frontmatter\n"):
            with self.subTest(text=text), self.assertRaises(SystemExit):
                pub.frontmatter(text)

    def test_richer_yaml_is_parsed_or_refused_never_guessed(self) -> None:
        text = "---\nname: x\ndescription: >-\n  folded\n  text\n---\nbody\n"
        try:
            meta = pub.frontmatter(text)
        except SystemExit as refused:
            self.assertIn("YAML parser", str(refused))
        else:
            self.assertEqual(meta["description"], "folded text")


if __name__ == "__main__":
    unittest.main()
