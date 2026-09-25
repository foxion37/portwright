"""End-to-end local Hub sync, catalog and MCP boundaries."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from portwright import hub_contracts
from portwright.contracts import ContractCatalog
from portwright.mcp import serve
from tests._v2 import write_service


def digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def entry(name="acme", grade="stable", body="Shared procedure", kind="procedure"):
    stem = name if kind == "procedure" else "2026-09-25-acme-lesson"
    note_id = ("service/" if kind == "procedure" else "failure/") + stem
    text = ("---\nid: acme\ndisplay_name: Acme\nversion_tag: v1\nlast_verified: 2026-09-25\nendpoint:\n  type: cli\n  server: acme\nhuman_steps:\n  - 없음\nagent_can:\n  - 실행\nstatus: active\ndistributable: true\n"
            if kind == "procedure" else "---\ndate: 2026-09-25\nservice: acme\nservice_version: v1\nstatus: active\ndistributable: true\n")
    text += f"grade: {grade}\ndoc_url: https://docs.example.org/acme\n---\n## " + ("Procedure" if kind == "procedure" else "Root cause") + f"\n{body}\n"
    revision = hub_contracts.note_revision(kind, "acme", text, "https://docs.example.org/acme")
    text = text.replace(f"grade: {grade}\n", f"grade: {grade}\nrevision: {revision}\n")
    wire = dict(note_id=note_id, kind=kind, service_id="acme", revision=revision, grade=grade,
                path=f"{grade}/acme/notes/{stem}.md", uri=f"skill://gisul/commons/{grade}/acme/notes/{stem}.md",
                file_digest="sha256:" + hashlib.sha256(text.encode()).hexdigest(), size=len(text.encode()), text=text)
    return wire


def response(notes, recalls=None, audience="public"):
    recalls = recalls or dict(schema_version=1, audience=audience, commit="b" * 40, sequence=1, entries=[])
    return dict(schema_version=1, audience=audience,
                release_identity=dict(commit="a" * 40, release="sample", inventory_digest="sha256:" + "a" * 64),
                notes=notes, notes_digest=digest(notes), recalls=recalls, recalls_digest=digest(recalls))


class HubTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.current = response([entry()])
        self.next = None
        self.hits = []
        self.require_hub_agent = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if owner.require_hub_agent and self.headers.get("User-Agent") != "portwright-hub/2.2":
                    self.send_response(403)
                    self.end_headers()
                    return
                owner.hits.append((self.path, self.headers.get("Authorization")))
                data = owner.next if len(owner.hits) % 2 == 0 and owner.next is not None else owner.current
                if "include_trial=false" in self.path and any(note["grade"] == "trial" for note in data["notes"]):
                    data = dict(data, notes=[note for note in data["notes"] if note["grade"] == "stable"])
                    data["notes_digest"] = digest(data["notes"])
                body = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if owner.require_hub_agent and self.headers.get("User-Agent") != "portwright-hub/2.2":
                    self.send_response(403)
                    self.end_headers()
                    return
                owner.hits.append((self.path, self.headers.get("Authorization")))
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                receipt = {"jsonrpc": "2.0", "id": request["id"], "result": {
                    "content": [{"type": "text", "text": json.dumps({"accepted": True})}], "isError": False}}
                body = json.dumps(receipt).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.origin = f"http://127.0.0.1:{server.server_port}"
        config = {"schema_version": 1, "active": "public", "hubs": {
            "public": {"url": "https://public.example.org", "token_env": "PORTWRIGHT_TEST_HUB_TOKEN"},
            "company": {"url": "https://company.example.org", "token_env": "PORTWRIGHT_TEST_HUB_TOKEN"}}}
        path = self.home / "_local/hub/config.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(config))
        self.saved = os.environ.get("PORTWRIGHT_TEST_HUB_TOKEN")
        os.environ["PORTWRIGHT_TEST_HUB_TOKEN"] = "fixture-access"
        self.addCleanup(self.restore_env)
        # Only transport is redirected; the product still enforces HTTPS origins.
        def local_open(request, *args, **kwargs):
            assert isinstance(request, Request)
            assert request.full_url.startswith("https://public.example.org/") or request.full_url.startswith("https://company.example.org/")
            local = Request(self.origin + request.full_url.split(".org", 1)[1], headers=dict(request.header_items()), method=request.get_method(), data=request.data)
            response = urlopen(local, *args, **kwargs)
            response.url = request.full_url
            return response
        self.local_open = local_open
        self.transport = patch("portwright.hub_sync.urlopen", side_effect=local_open)
        self.transport.start()
        self.addCleanup(self.transport.stop)

    def restore_env(self):
        if self.saved is None:
            os.environ.pop("PORTWRIGHT_TEST_HUB_TOKEN", None)
        else:
            os.environ["PORTWRIGHT_TEST_HUB_TOKEN"] = self.saved

    def sync(self, **kwargs):
        from portwright.hub_sync import sync
        return sync(self.home, hub="public", **kwargs)

    def mcp(self, name, arguments):
        out = io.StringIO()
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
        serve(self.home, stdin=io.StringIO(json.dumps(req) + "\n"), stdout=out)
        return json.loads(out.getvalue())

    def test_cli_sync_preflight_mcp_and_private_override(self):
        private = write_service(self.home, "acme", private=True)
        before = private.read_bytes()
        self.sync()
        self.assertEqual(private.read_bytes(), before)
        self.assertEqual(ContractCatalog(self.home).lookup("service", "acme").path, private.resolve())
        read = self.mcp("get_note", {"path": "services/_private/acme.md"})
        self.assertEqual(json.loads(read["result"]["content"][0]["text"])["text"], before.decode())
        self.assertEqual(self.mcp("preflight", {"service": "acme"})["result"]["isError"], False)
        self.assertEqual(len(self.hits), 2)

    def test_configured_hub_does_not_expose_unverified_flat_local_notes(self):
        legacy = write_service(self.home, "acme")
        flat = self.home / "services/_hub/acme.md"
        flat.parent.mkdir()
        legacy.rename(flat)
        self.assertIsNone(ContractCatalog(self.home).lookup("service", "acme"))
        self.assertTrue(self.mcp("get_note", {"path": "services/_hub/acme.md"})["result"]["isError"])
        self.sync()
        self.assertEqual(ContractCatalog(self.home).lookup("service", "acme").data["grade"], "stable")

    def test_trial_defaults_and_one_time_and_persistent_opt_in(self):
        trial = entry("acme", "trial", "New trial")
        self.current = response([trial])
        self.sync()
        self.assertIsNone(ContractCatalog(self.home).lookup("service", "acme"))
        self.assertFalse(self.mcp("get_note", {"path": "services/_hub/public/missing/acme.md"})["result"]["isError"] is False)
        self.sync(include_trial=True)
        self.assertEqual(ContractCatalog(self.home, include_trial=True).lookup("service", "acme").data["grade"], "trial")
        self.assertIsNone(ContractCatalog(self.home).lookup("service", "acme"))
        from contextlib import redirect_stdout
        from portwright.cli import main
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["preflight", "acme", "--json", "--home", str(self.home)]), 0)
        self.assertIsNone(json.loads(out.getvalue())["procedure"])
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["preflight", "acme", "--include-trial", "--json", "--home", str(self.home)]), 0)
        self.assertIsNotNone(json.loads(out.getvalue())["procedure"])
        self.assertIsNotNone(json.loads(self.mcp("preflight", {"service": "acme", "include_trial": True})["result"]["content"][0]["text"])["procedure"])
        config = self.home / "_local/hub/config.json"
        data = json.loads(config.read_text())
        data["hubs"]["public"]["include_trial"] = True
        config.write_text(json.dumps(data))
        self.sync()
        self.assertEqual(ContractCatalog(self.home).lookup("service", "acme").data["grade"], "trial")

    def test_stable_and_trial_siblings_are_distinct_revisions(self):
        stable, trial = entry(), entry(grade="trial", body="Experimental replacement")
        self.current = response([stable, trial])
        self.sync(include_trial=True)
        normal = ContractCatalog(self.home).lookup("service", "acme")
        opted = ContractCatalog(self.home, include_trial=True).lookup("service", "acme")
        self.assertEqual(normal.data["revision"], stable["revision"])
        self.assertEqual(opted.data["revision"], trial["revision"])
        self.assertTrue(self.mcp("get_note", {"path": opted.relative_path})["result"]["isError"])
        self.assertFalse(self.mcp("get_note", {"path": opted.relative_path, "include_trial": True})["result"]["isError"])
        self.assertTrue(ContractCatalog(self.home).validate_workspace().ok)

    def test_sync_rejects_note_body_identity_even_with_matching_hashes(self):
        cases = (
            ("procedure", "id: acme", "id: other"),
            ("lesson", "service: acme", "service: other"),
            ("lesson", "date: 2026-09-25", "date: 2026-09-24"),
        )
        for kind, original, replacement in cases:
            with self.subTest(kind=kind, replacement=replacement):
                note = entry(kind=kind)
                text = note["text"].replace(original, replacement)
                revision = hub_contracts.note_revision(kind, "acme", text, "https://docs.example.org/acme")
                text = text.replace(note["revision"], revision)
                note.update(text=text, revision=revision, size=len(text.encode()),
                            file_digest="sha256:" + hashlib.sha256(text.encode()).hexdigest())
                self.current = response([note])
                with self.assertRaises(ValueError):
                    self.sync()
                self.assertFalse((self.home / "_local/hub/state.json").exists())

    def test_note_and_recall_digests_are_checked_independently(self):
        note = entry()
        self.current = response([note])
        self.current["notes_digest"] = "sha256:" + "0" * 64
        with self.assertRaises(ValueError):
            self.sync()
        broken = dict(note, revision="sha256:" + "0" * 64)
        self.current = response([broken])
        with self.assertRaises(ValueError):
            self.sync()
        self.current = response([note])
        self.current["recalls_digest"] = "sha256:" + "0" * 64
        with self.assertRaises(ValueError):
            self.sync()
        self.assertFalse((self.home / "_local/hub/state.json").exists())

    def test_tampered_cached_recall_cannot_be_overwritten_by_fresh_sync(self):
        self.sync()
        state_file = self.home / "_local/hub/state.json"
        state = json.loads(state_file.read_text())
        state["snapshots"]["public"]["recalls_digest"] = "sha256:" + "0" * 64
        state_file.write_text(json.dumps(state))
        with self.assertRaises(ValueError):
            self.sync()
        self.assertEqual(json.loads(state_file.read_text()), state)
    def test_damaged_active_note_can_be_replaced_online_but_not_read_offline(self):
        self.sync()
        old = ContractCatalog(self.home).lookup("service", "acme").path
        old.write_bytes(b"damaged")
        with self.assertRaises(ValueError):
            self.sync(offline=True)
        with self.assertRaises(ValueError):
            ContractCatalog(self.home).lookup("service", "acme")
        self.current = response([entry(body="Recovered")])
        self.sync()
        self.assertEqual(ContractCatalog(self.home).lookup("service", "acme").body[-1], "Recovered")

    def test_online_recovery_still_rejects_lost_recalls(self):
        old = entry()
        recalled = dict(schema_version=1, audience="public", commit="c" * 40, sequence=2,
                        entries=[dict(note_id=old["note_id"], revision=old["revision"])])
        self.current = response([entry(body="Replacement")], recalled)
        self.sync()
        ContractCatalog(self.home).lookup("service", "acme").path.write_bytes(b"damaged")
        self.current = response([entry(body="Another replacement")])
        with self.assertRaises(ValueError):
            self.sync()
        with self.assertRaises(ValueError):
            self.sync(offline=True)

    def test_mid_write_failure_and_lock_keep_pointer(self):
        self.sync()
        state = (self.home / "_local/hub/state.json").read_bytes()
        self.current = response([entry(body="Replacement"), entry(kind="lesson")])
        from portwright import hub_sync
        write_new = hub_sync._write_new
        def fail_second(root, path, data):
            if path.startswith("failures/_hub/"):
                raise OSError("simulated write failure")
            return write_new(root, path, data)
        with patch("portwright.hub_sync._write_new", side_effect=fail_second):
            with self.assertRaises(OSError):
                self.sync()
        self.assertEqual((self.home / "_local/hub/state.json").read_bytes(), state)
        self.assertEqual(len(list((self.home / "services/_hub/public").glob("*/acme.md"))), 2)
        lock = self.home / "_local/hub/sync.lock"
        lock.write_text("occupied")
        with self.assertRaises(FileExistsError):
            self.sync()
        self.assertEqual(lock.read_text(), "occupied")

    def test_next_successful_owner_cleans_only_its_inactive_generations(self):
        self.sync()
        state_path = self.home / "_local/hub/state.json"
        first = json.loads(state_path.read_text())["snapshots"]["public"]["generation"]
        foreign = self.home / "services/_hub/company/owned"
        foreign.mkdir(parents=True)
        (foreign / "keep.md").write_text("user data")
        local = self.home / "services/_hub/public/user-owned"
        local.mkdir()
        (local / "keep.md").write_text("local data")
        self.current = response([entry(body="Partial"), entry(kind="lesson")])
        from portwright import hub_sync
        write_new = hub_sync._write_new
        def fail_second(root, path, data):
            if path.startswith("failures/_hub/"):
                raise OSError("write failed")
            return write_new(root, path, data)
        with patch("portwright.hub_sync._write_new", side_effect=fail_second):
            with self.assertRaises(OSError):
                self.sync()
        partial = next(p.name for p in (self.home / "services/_hub/public").iterdir()
                       if p.name != first and re.fullmatch(r"[a-f0-9]{32}", p.name))
        self.current = response([entry(body="Second")])
        self.sync()
        self.assertFalse((self.home / "services/_hub/public" / partial).exists())
        self.assertTrue((self.home / "services/_hub/public" / first / "acme.md").exists())
        self.current = response([entry(body="Third")])
        self.sync()
        self.assertFalse((self.home / "services/_hub/public" / first).exists())
        self.assertEqual((foreign / "keep.md").read_text(), "user data")
        self.assertEqual((local / "keep.md").read_text(), "local data")
        self.assertEqual(ContractCatalog(self.home).lookup("service", "acme").body[-1], "Third")

    def test_known_recall_offline_and_old_generation_cannot_be_read(self):
        old = entry()
        self.sync()
        old_path = ContractCatalog(self.home).lookup("service", "acme").relative_path
        newer = entry(body="Replacement")
        self.current = response([newer], dict(schema_version=1, audience="public", commit="c" * 40, sequence=2,
                                            entries=[dict(note_id=old["note_id"], revision=old["revision"])]))
        self.sync()
        self.assertTrue(self.mcp("get_note", {"path": old_path})["result"]["isError"])
        self.assertEqual(self.sync(offline=True)["recall_freshness"], "last-sync")
        self.assertEqual(ContractCatalog(self.home).lookup("service", "acme").data["revision"], newer["revision"])
        self.current = response([old])
        with self.assertRaises(ValueError):
            self.sync()

    def test_two_hubs_never_fallback_and_reject_unsafe_config(self):
        self.sync()
        from portwright.hub_sync import sync
        self.current = response([], audience="company")
        sync(self.home, hub="company")
        self.assertEqual(ContractCatalog(self.home).lookup("service", "acme").data["grade"], "stable")
        self.assertIsNone(ContractCatalog(self.home, hub="company").lookup("service", "acme"))
        self.assertFalse(self.mcp("preflight", {"service": "acme"})["result"]["isError"])
        config = self.home / "_local/hub/config.json"
        data = json.loads(config.read_text())
        data["hubs"]["public"]["include_trial"] = True
        data["hubs"]["company"]["include_trial"] = False
        config.write_text(json.dumps(data))
        self.current = response([entry(grade="trial")])
        self.sync(include_trial=True)
        self.current = response([], audience="company")
        sync(self.home, hub="company")
        self.assertEqual(ContractCatalog(self.home).lookup("service", "acme").data["grade"], "trial")
        data["hubs"]["public"]["include_trial"] = False
        data["hubs"]["company"]["include_trial"] = True
        config.write_text(json.dumps(data))
        self.current = response([entry(grade="trial", body="Company trial")], audience="company")
        sync(self.home, hub="company", include_trial=True)
        self.assertEqual(ContractCatalog(self.home, hub="company").lookup("service", "acme").body[-1], "Company trial")
        payload = {"note_id": "service/acme", "revision": entry(grade="trial")["revision"], "reason": "Documented command failed"}
        with patch("portwright.mcp.urlopen", side_effect=self.local_open) as transport:
            self.assertFalse(self.mcp("report_failure", payload)["result"]["isError"])
            self.assertTrue(transport.call_args.args[0].full_url.startswith("https://public.example.org/"))
        data["hubs"]["company"]["url"] = "https://company.example.org/extra"
        config.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            sync(self.home, hub="company")

    def test_explicit_hub_sync_without_default_selection(self):
        from contextlib import redirect_stdout
        from portwright.cli import main
        from portwright.hub_sync import sync
        tracked = write_service(self.home, "local", extra="distributable: true")
        private = write_service(self.home, "private", private=True, extra="distributable: false")
        config = self.home / "_local/hub/config.json"
        data = json.loads(config.read_text())
        del data["active"]
        config.write_text(json.dumps(data))
        self.sync()
        self.assertEqual(sync(self.home, hub="public", offline=True)["hub"], "public")
        self.assertEqual(ContractCatalog(self.home).lookup("service", "local").path, tracked.resolve())
        self.assertEqual(ContractCatalog(self.home).lookup("service", "private").path, private.resolve())
        self.assertIsNone(ContractCatalog(self.home).lookup("service", "acme"))
        out = io.StringIO()
        with redirect_stdout(out):
            check_rc = main(["check", "--home", str(self.home)])
            self.assertEqual(check_rc, 0, out.getvalue())
            self.assertEqual(main(["preflight", "local", "--json", "--home", str(self.home)]), 0)
        self.assertIsNotNone(json.loads(out.getvalue()[out.getvalue().index("{"):])["procedure"])
        self.assertFalse(self.mcp("status", {})["result"]["isError"])
        self.assertIsNone(json.loads(self.mcp("preflight", {"service": "acme"})["result"]["content"][0]["text"])["procedure"])
        self.assertFalse(self.mcp("preflight", {"service": "local"})["result"]["isError"])
        data["active"] = "invalid"
        config.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            ContractCatalog(self.home).lookup("service", "local")
    def test_symlink_and_root_escape_are_refused(self):
        outside = self.home.parent / "not-owned"
        link = self.home / "services/_hub"
        link.parent.mkdir(exist_ok=True)
        link.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.sync()
        self.assertFalse(outside.exists())
        link.unlink()
        evil = entry()
        evil["path"] = "../../outside.md"
        self.current = response([evil])
        with self.assertRaises(ValueError):
            self.sync()
    def test_replaced_hub_parent_cannot_read_replace_or_unlink_outside_home(self):
        from portwright import hub_sync
        self.sync()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        outside = Path(temp.name)
        external = {name: outside / name for name in ("config.json", "state.json", "state.pending.json")}
        for name, path in external.items():
            path.write_text(f"outside {name}")
        parent = self.home / "_local/hub"
        parent.rename(self.home / "_local/displaced")
        parent.symlink_to(outside, target_is_directory=True)
        before = {name: path.read_bytes() for name, path in external.items()}
        calls = (
            lambda: hub_sync._read_bytes(self.home, "_local/hub/config.json"),
            lambda: hub_sync._replace(self.home, "_local/hub/state.pending.json", "_local/hub/state.json"),
            lambda: hub_sync._unlink(self.home, "_local/hub/state.json"),
            lambda: hub_sync.snapshot(self.home),
            lambda: self.sync(offline=True),
            lambda: self.sync(),
        )
        received = len(self.hits)
        for operation in calls:
            with self.subTest(operation=operation):
                with self.assertRaises(ValueError):
                    operation()
        self.assertEqual(len(self.hits), received)
        self.assertEqual({name: path.read_bytes() for name, path in external.items()}, before)

    def test_parent_replacement_during_write_cannot_escape_home(self):
        from portwright import hub_sync
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        outside = Path(temp.name)
        parent = self.home / "services/_hub"
        parent.mkdir(parents=True)
        original = hub_sync._mkdir
        ready = threading.Event()
        switched = threading.Event()

        def attacker():
            if not ready.wait(2):
                return
            generation = next((self.home / "services/_hub/public").iterdir()).name
            (outside / "public" / generation).mkdir(parents=True)
            parent.rename(self.home / "services/displaced")
            parent.symlink_to(outside, target_is_directory=True)
            switched.set()

        worker = threading.Thread(target=attacker, daemon=True)
        worker.start()

        def replace_parent(root, relative):
            result = original(root, relative)
            if relative.startswith("services/_hub/public/") and not ready.is_set():
                ready.set()
                self.assertTrue(switched.wait(2))
            return result

        with patch("portwright.hub_sync._mkdir", side_effect=replace_parent):
            try:
                self.sync()
            except (ValueError, OSError):
                pass
        worker.join(2)
        self.assertTrue(switched.is_set())
        self.assertEqual(list(outside.rglob("*.md")), [])

    def test_mcp_get_note_does_not_read_swapped_parent(self):
        self.sync()
        note = ContractCatalog(self.home).lookup("service", "acme")
        original = self.home / "services/_hub"
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        outside = Path(temp.name)
        (outside / Path(note.relative_path).relative_to("services/_hub")).parent.mkdir(parents=True)
        (outside / Path(note.relative_path).relative_to("services/_hub")).write_text("SENTINEL_SECRET")
        from portwright import mcp
        resolve = mcp._note_path

        def swap(*args):
            result = resolve(*args)
            original.rename(self.home / "services/displaced")
            original.symlink_to(outside, target_is_directory=True)
            return result

        with patch("portwright.mcp._note_path", side_effect=swap):
            answer = self.mcp("get_note", {"path": note.relative_path})
        self.assertTrue(answer["result"]["isError"])
        self.assertNotIn("SENTINEL_SECRET", json.dumps(answer))

    def test_catalog_does_not_read_parent_replaced_after_validation(self):
        self.sync()
        note = ContractCatalog(self.home).lookup("service", "acme")
        parent = self.home / "services/_hub"
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        outside = Path(temp.name)
        target = outside / Path(note.relative_path).relative_to("services/_hub")
        target.parent.mkdir(parents=True)
        target.write_text("SENTINEL_SECRET")
        iterate = ContractCatalog.iter_notes
        swapped = threading.Event()

        def swap(catalog, *args, **kwargs):
            paths = iterate(catalog, *args, **kwargs)
            if not swapped.is_set():
                parent.rename(self.home / "services/displaced")
                parent.symlink_to(outside, target_is_directory=True)
                swapped.set()
            return paths

        with patch.object(ContractCatalog, "iter_notes", swap):
            found = ContractCatalog(self.home).lookup("service", "acme")
        self.assertTrue(swapped.is_set())
        self.assertEqual(found.data["id"], "acme")
        self.assertNotIn("SENTINEL_SECRET", repr(found))

    def test_malformed_config_list_returns_safe_error_and_serves_next_request(self):
        path = self.home / "_local/hub/config.json"
        path.write_text('{"token":"SENTINEL_SECRET", invalid')
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        ]
        out = io.StringIO()
        serve(self.home, stdin=io.StringIO("\n".join(json.dumps(req) for req in requests) + "\n"), stdout=out)
        first, second = map(json.loads, out.getvalue().splitlines())
        self.assertEqual(first["error"]["code"], -32603)
        self.assertNotIn("SENTINEL_SECRET", json.dumps(first))
        self.assertNotIn("invalid", json.dumps(first))
        self.assertEqual(second["result"], {})

    def test_invalid_config_value_list_does_not_echo_value_or_stop(self):
        path = self.home / "_local/hub/config.json"
        data = json.loads(path.read_text())
        data["hubs"]["public"]["token_env"] = "SENTINEL_SECRET-invalid"
        path.write_text(json.dumps(data))
        requests = ({"jsonrpc": "2.0", "id": i, "method": method} for i, method in ((1, "tools/list"), (2, "ping")))
        out = io.StringIO()
        serve(self.home, stdin=io.StringIO("\n".join(map(json.dumps, requests)) + "\n"), stdout=out)
        first, second = map(json.loads, out.getvalue().splitlines())
        self.assertEqual(first["error"]["code"], -32603)
        self.assertNotIn("SENTINEL_SECRET", json.dumps(first))
        self.assertEqual(second["result"], {})

    def test_invalid_url_port_never_echoes_config_in_mcp_errors(self):
        config = self.home / "_local/hub/config.json"
        data = json.loads(config.read_text())
        data["hubs"]["public"]["url"] = "https://public.example.org:SENTINEL_VALUE"
        config.write_text(json.dumps(data))
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "preflight", "arguments": {"service": "acme"}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "report_failure", "arguments": {
                 "note_id": "service/acme", "revision": entry()["revision"], "reason": "Command failed"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "ping"},
        ]
        out = io.StringIO()
        serve(self.home, stdin=io.StringIO("\n".join(map(json.dumps, requests)) + "\n"), stdout=out)
        listed, preflight, report, ping = map(json.loads, out.getvalue().splitlines())
        self.assertEqual(listed["error"], {"code": -32603, "message": "internal error"})
        for reply in (preflight, report):
            self.assertTrue(reply["result"]["isError"])
            self.assertNotIn("SENTINEL_VALUE", json.dumps(reply))
        self.assertEqual(ping["result"], {})
        self.assertEqual(len(self.hits), 0)

    def test_unrecognized_internal_exception_is_not_reflected(self):
        with patch("portwright.mcp.run_preflight", side_effect=ValueError("SENTINEL_VALUE")):
            answer = self.mcp("preflight", {"service": "acme"})
        self.assertEqual(answer["result"]["content"][0]["text"], "internal error")

    def test_cli_sync_to_preflight_to_mcp_uses_one_active_generation(self):
        from contextlib import redirect_stdout
        from portwright.cli import main
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["hub", "sync", "--home", str(self.home)]), 0)
            self.assertEqual(main(["preflight", "acme", "--home", str(self.home), "--json"]), 0)
        decision = json.loads(output.getvalue().partition("\n")[2])
        self.assertEqual(decision["procedure"], ContractCatalog(self.home).lookup("service", "acme").relative_path)
        self.assertTrue(decision["hub_synced_at"])
        self.assertEqual(self.mcp("get_note", {"path": decision["procedure"]})["result"]["isError"], False)

    def test_local_submission_rejects_sensitive_text_without_remote_call(self):
        from portwright.hub_sync import screen_submission
        payload = {"note_id": "service/acme", "revision": "sha256:" + "a" * 64,
                   "reason": "Access failed " + "AKIA" + "A" * 16}
        before = len(self.hits)
        self.assertEqual(screen_submission("report_failure", payload)["ok"], False)
        result = self.mcp("report_failure", payload)
        self.assertTrue(result["result"]["isError"])
        self.assertEqual(len(self.hits), before)
        self.assertNotIn(payload["reason"], json.dumps(result))

    def test_local_submission_rejects_nul_and_single_encoded_secret(self):
        from portwright.hub_sync import screen_submission
        base = {"note_id": "service/acme", "revision": "sha256:" + "a" * 64}
        self.assertFalse(screen_submission("report_failure", dict(base, reason="failed\x00now"))["ok"])
        encoded = "%41KIA" + "A" * 16
        self.assertEqual(screen_submission("report_failure", dict(base, reason=encoded))["reason_code"], "secret")

    def test_relay_rejects_other_hub_and_recalled_revisions_before_post(self):
        from portwright.hub_sync import sync
        public = entry(body="Public procedure")
        company = entry(body="Company procedure")
        self.current = response([public])
        self.sync()
        self.current = response([company], audience="company")
        sync(self.home, hub="company")
        read = self.mcp("preflight", {"service": "acme", "hub": "company"})
        self.assertIn("services/_hub/company/", json.loads(read["result"]["content"][0]["text"])["procedure"])
        report = {"note_id": company["note_id"], "revision": company["revision"],
                  "reason": "Documented command failed"}
        confirm = {"note_id": company["note_id"], "revision": company["revision"],
                   "success_evidence": {"action": "Followed procedure", "outcome": "Succeeded"}}
        before = len(self.hits)
        for name, payload in (("report_failure", report), ("confirm_lesson", confirm)):
            result = self.mcp(name, payload)["result"]
            self.assertEqual(json.loads(result["content"][0]["text"]), {"error": "note_not_in_selected_hub"})
            self.assertTrue(result["isError"])
        self.assertEqual(len(self.hits), before)

        config = self.home / "_local/hub/config.json"
        data = json.loads(config.read_text())
        data["active"] = "company"
        config.write_text(json.dumps(data))
        with patch("portwright.mcp.urlopen", side_effect=self.local_open) as transport:
            for name, payload in (("report_failure", report), ("confirm_lesson", confirm)):
                self.assertFalse(self.mcp(name, payload)["result"]["isError"])
            self.assertEqual(transport.call_count, 2)
            self.assertTrue(all(call.args[0].full_url.startswith("https://company.example.org/")
                                for call in transport.call_args_list))
        recalled = dict(schema_version=1, audience="company", commit="c" * 40, sequence=2,
                        entries=[{"note_id": company["note_id"], "revision": company["revision"]}])
        self.current = response([], recalled, audience="company")
        sync(self.home, hub="company")
        before = len(self.hits)
        self.assertEqual(json.loads(self.mcp("report_failure", report)["result"]["content"][0]["text"]),
                         {"error": "note_not_in_selected_hub"})
        self.assertEqual(len(self.hits), before)

    def test_trial_submission_requires_selected_snapshot_opt_in(self):
        from portwright.hub_sync import sync
        trial = entry(grade="trial")
        self.current = response([trial], audience="company")
        sync(self.home, hub="company")
        payload = {"note_id": trial["note_id"], "revision": trial["revision"],
                   "reason": "Trial command did not work"}
        data = json.loads((self.home / "_local/hub/config.json").read_text())
        data["active"] = "company"
        (self.home / "_local/hub/config.json").write_text(json.dumps(data))
        self.assertEqual(json.loads(self.mcp("report_failure", payload)["result"]["content"][0]["text"]),
                         {"error": "note_not_in_selected_hub"})
        sync(self.home, hub="company", include_trial=True)
        with patch("portwright.mcp.urlopen", side_effect=self.local_open):
            self.assertFalse(self.mcp("report_failure", payload)["result"]["isError"])

    def test_sync_and_relay_use_hub_agent_accepted_by_edge(self):
        self.require_hub_agent = True
        note = entry()
        self.sync()
        payload = {"note_id": note["note_id"], "revision": note["revision"],
                   "reason": "The documented command did not work"}
        with patch("portwright.mcp.urlopen", side_effect=self.local_open):
            self.assertFalse(self.mcp("report_failure", payload)["result"]["isError"])

    def test_valid_local_submission_reaches_only_selected_hub(self):
        note = entry()
        self.sync()
        payload = {"note_id": note["note_id"], "revision": note["revision"],
                   "reason": "The published command fails on the documented version"}
        with patch("portwright.mcp.urlopen", side_effect=self.local_open):
            result = self.mcp("report_failure", payload)
        self.assertFalse(result["result"]["isError"])
        self.assertEqual(json.loads(result["result"]["content"][0]["text"])["accepted"], True)
        self.assertEqual([path for path, _ in self.hits][-1:], ["/mcp"])
        os.environ.pop("PORTWRIGHT_TEST_HUB_TOKEN")
        listed = self.mcp("status", {})
        self.assertFalse(listed["result"]["isError"])
        out = io.StringIO()
        serve(self.home, stdin=io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"), stdout=out)
        names = [tool["name"] for tool in json.loads(out.getvalue())["result"]["tools"]]
        self.assertNotIn("report_failure", names)

    def test_mid_fetch_new_recall_removes_staged_revision(self):
        note = entry()
        recalls = dict(schema_version=1, audience="public", commit="c" * 40, sequence=2, entries=[dict(note_id=note["note_id"], revision=note["revision"])])
        self.next = response([], recalls)
        self.sync()
        self.assertIsNone(ContractCatalog(self.home).lookup("service", "acme"))
        received = len(self.hits)
        os.environ.pop("PORTWRIGHT_TEST_HUB_TOKEN")
        self.assertEqual(self.sync(offline=True)["status"], "offline")
        self.assertEqual(len(self.hits), received)


if __name__ == "__main__":
    unittest.main()
