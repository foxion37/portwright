#!/usr/bin/env python3
"""Tests for scripts/jev_bundle_gate.py: the personal gate (approved packages and fixed
code guides, no model path) and the shared gate (exact rebuild, note coverage, policy
receipt). The shared fixture is reused from the builder tests.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "lib"))

import build_skill_bundles as builder  # noqa: E402
import jev_bundle_gate as gate  # noqa: E402
from portwright import hub_contracts  # noqa: E402
from portwright.hub_pipeline import gate_policy, gate_proof  # noqa: E402
from tests.test_skill_bundles import DOC, UUID_B, UUID_C, SharedCase, canon_note, git, sha256, write_note  # noqa: E402


class PersonalGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.approved = {"sample": {"name": "sample", "files": [
            {"path": "SKILL.md", "sha256": "a" * 64, "bytes": 20},
            {"path": "references/guide.md", "sha256": "b" * 64, "bytes": 30},
        ]}}
        self.entry = {"path": "personal/sample/SKILL.md", "personal": True, "files": [
            {"path": "personal/sample/SKILL.md", "sha256": "a" * 64, "size": 20},
            {"path": "personal/sample/references/guide.md", "sha256": "b" * 64, "size": 30},
        ]}

    def test_approval_covers_the_complete_package(self):
        self.assertTrue(gate.personal_approved(self.entry, self.approved))

    def test_changed_supporting_file_requires_new_approval(self):
        self.entry["files"][1]["sha256"] = "c" * 64
        self.assertFalse(gate.personal_approved(self.entry, self.approved))

    def test_added_or_missing_resources_are_not_implicitly_approved(self):
        self.entry["files"].pop()
        self.assertFalse(gate.personal_approved(self.entry, self.approved))

    def test_source_name_alone_does_not_grant_publication(self):
        self.entry["path"] = "sample/SKILL.md"
        self.assertFalse(gate.personal_approved(self.entry, self.approved))

    def write(self, relative: str, data: bytes | str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))

    def run_gate(self, inventory: dict, approvals: dict | None = None) -> tuple[str, str]:
        """Returns (result-or-refusal, printed per-package lines)."""
        approval = self.root / "approval.json"
        approval.write_text(json.dumps({"skills": list((self.approved if approvals is None else approvals).values())}), encoding="utf-8")
        output = io.StringIO()
        with mock.patch.object(gate, "PERSONAL_APPROVAL", approval), \
                mock.patch.object(gate, "validated_inventory", return_value=inventory), \
                redirect_stdout(output):
            try:
                result = gate.check_personal(self.root)
            except SystemExit as error:
                return str(error), output.getvalue()
        return result, output.getvalue()

    def test_approved_package_and_fixed_code_guide_pass(self):
        self.write("personal/sample/SKILL.md", b"s" * 20)
        self.write("personal/sample/references/guide.md", b"g" * 30)
        self.write("portwright-tool-use/SKILL.md", b"guide bytes")
        inventory = {"skills": {
            "sample": self.entry,
            "portwright-tool-use": {"path": "portwright-tool-use/SKILL.md", "origin": "code",
                                    "files": [{"path": "portwright-tool-use/SKILL.md", "sha256": sha256(b"guide bytes"), "size": 11}]},
        }}
        with mock.patch.object(gate, "tracked_files", return_value=["skills/portwright-tool-use/SKILL.md"]), \
                mock.patch.object(gate, "read_tracked", return_value=b"guide bytes"):
            result, output = self.run_gate(inventory)
        self.assertEqual(result, {"ok": True, "audience": "personal", "skills": 2})
        self.assertIn("approved immutable personal snapshot", output)
        self.assertIn("fixed code guide matches the code checkout", output)

    def test_code_guide_must_match_the_checkout(self):
        self.write("portwright-tool-use/SKILL.md", b"changed bytes")
        inventory = {"skills": {"portwright-tool-use": {
            "path": "portwright-tool-use/SKILL.md", "origin": "code",
            "files": [{"path": "portwright-tool-use/SKILL.md", "sha256": sha256(b"changed bytes"), "size": 13}]}}}
        with mock.patch.object(gate, "tracked_files", return_value=["skills/portwright-tool-use/SKILL.md"]), \
                mock.patch.object(gate, "read_tracked", return_value=b"guide bytes"):
            refusal, output = self.run_gate(inventory, approvals={})
        self.assertIn("code guide differs from the code checkout", output)
        self.assertIn("failed", refusal)

    def test_a_missing_or_relabelled_approved_package_is_refused(self):
        for inventory in (
            {"skills": {}},
            {"skills": {"sample": {**self.entry, "path": "sample/SKILL.md", "personal": False}}},
        ):
            with self.subTest(inventory=inventory):
                refusal, _ = self.run_gate(inventory)
                self.assertIn("approved personal package missing, renamed, or changed", refusal)

    def test_unapproved_package_in_the_bundle_is_refused(self):
        self.write("personal/other/SKILL.md", b"x")
        other = {"path": "personal/other/SKILL.md", "personal": True,
                 "files": [{"path": "personal/other/SKILL.md", "sha256": sha256(b"x"), "size": 1}]}
        self.write("personal/sample/SKILL.md", b"s" * 20)
        self.write("personal/sample/references/guide.md", b"g" * 30)
        refusal, output = self.run_gate({"skills": {"sample": self.entry, "other": other}})
        self.assertIn("missing or changed personal approval", output)
        self.assertIn("failed", refusal)

    def test_identifier_in_a_code_guide_is_refused_before_publication(self):
        identifier = "private host " + ".".join(("100", "64", "0", "1"))
        self.write("portwright-tool-use/SKILL.md", identifier)
        inventory = {"skills": {"portwright-tool-use": {
            "path": "portwright-tool-use/SKILL.md", "origin": "code",
            "files": [{"path": "portwright-tool-use/SKILL.md", "sha256": sha256(identifier.encode()), "size": len(identifier)}]}}}
        refusal, output = self.run_gate(inventory, approvals={})
        self.assertIn("blocked by local identifier screen", output)
        self.assertIn("failed", refusal)

    def test_secret_in_any_package_is_refused(self):
        secret = "api_key" + ": " + "abcdef" + "123456"
        self.write("personal/sample/SKILL.md", secret)
        self.write("personal/sample/references/guide.md", b"g" * 30)
        refusal, output = self.run_gate({"skills": {"sample": self.entry}})
        self.assertIn("blocked by local secret screen", output)
        self.assertIn("failed", refusal)


ANSWERS = {"supported": 0.95, "evidence_consistent": 0.95, "malicious": 0.01, "personal": 0.01}


class SharedGateTests(SharedCase):
    """Every shipped revision is proven by a Gate-Proof trailer in its path history."""

    def setUp(self) -> None:
        super().setUp()
        trailers = [self.prove(relative) for relative in builder.tracked_files(
            "services", "failures", "trial/services", "trial/failures", root=self.commons)]
        self.commit_with(trailers)
        self.manifest = self.build()

    def domains(self) -> dict:
        return json.loads((self.commons / "policy" / "official-doc-domains.json").read_text(encoding="utf-8"))

    def prove(self, relative: str, *, tamper=None) -> str:
        """Stamp the note's gate_digest from a real proof; returns its trailer line."""
        path = self.commons / relative
        text = path.read_text(encoding="utf-8")
        meta = hub_contracts.parse_note(text)["metadata"]
        service_id = meta.get("id") or meta.get("service")
        proof = gate_proof(hub_contracts.note_id_from_path(relative), meta["revision"], meta["doc_digest"],
                           dict(ANSWERS), gate_policy(self.domains(), service_id))
        path.write_text(hub_contracts.render_note(text, {"gate_digest": proof["gate_digest"]}), encoding="utf-8")
        if tamper:
            tamper(proof)
        return "Gate-Proof: " + json.dumps(proof, sort_keys=True)

    def commit_with(self, trailers: list[str]) -> None:
        git(self.commons, "add", "-A")
        git(self.commons, "commit", "-qm", "gated", *(["-m", "\n".join(trailers)] if trailers else []))

    def new_sibling(self, body: str) -> str:
        relative = "trial/services/smoke.md"
        write_note(self.commons, relative, canon_note("procedure", "smoke", body, uuid=UUID_B, grade="trial"))
        return relative

    def set_domains(self, services: dict) -> None:
        write_note(self.commons, "policy/official-doc-domains.json", json.dumps({"schema_version": 1, "services": services}))
        self.commit_with([])
        self.manifest = self.build()

    def test_accepts_a_verbatim_rebuild_proven_by_git_history(self) -> None:
        result = gate.check_shared(self.out, self.commons, "public")
        self.assertEqual(result["ok"], True)
        self.assertEqual(result["commit"], self.manifest["commit"])
        self.assertEqual(result["skills"], 2)

    def test_a_revision_without_a_gate_proof_is_refused(self) -> None:
        self.new_sibling("## Steps\n\nUnproven newer state.")
        self.commit_with([])
        self.build()
        with self.assertRaises(SystemExit) as caught:
            gate.check_shared(self.out, self.commons, "public")
        self.assertIn("gate proof", str(caught.exception))

    def test_altered_judgment_cannot_reuse_a_gate_digest(self) -> None:
        for index, value in enumerate((0.1, 0.99)):
            with self.subTest(supported=value):
                relative = self.new_sibling(f"## Steps\n\nForged state {index}.")
                self.commit_with([self.prove(relative, tamper=lambda proof: proof["answers"].update(supported=value))])
                self.build()
                with self.assertRaises(SystemExit) as caught:
                    gate.check_shared(self.out, self.commons, "public")
                self.assertIn("gate proof", str(caught.exception))

    def test_free_text_outside_the_gate_receipt_is_refused(self) -> None:
        """A post-build edit cannot be laundered by rewriting the digests around it."""
        relative = "stable/smoke/notes/smoke.md"
        target = self.out / relative
        target.write_bytes(target.read_bytes() + "\n새 자유 텍스트\n".encode("utf-8"))
        digest, size = "sha256:" + sha256(target.read_bytes()), len(target.read_bytes())
        index_path = self.out / "note-index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        for note in index["notes"]:
            if note["path"] == relative:
                note["file_digest"], note["size"] = digest, size
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        index_bytes = index_path.read_bytes()
        manifest_path = self.out / "inventory.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest["files"] + manifest["skills"]["stable/smoke"]["files"]:
            if item["path"] == relative:
                item["sha256"], item["size"] = digest.split(":", 1)[1], size
            if item["path"] == "note-index.json":
                item["sha256"], item["size"] = sha256(index_bytes), len(index_bytes)
        manifest["note_index"] = {"path": "note-index.json", "digest": "sha256:" + sha256(index_bytes), "size": len(index_bytes)}
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(SystemExit) as caught:
            gate.check_shared(self.out, self.commons, "public")
        self.assertIn("exact rebuild", str(caught.exception))

    def test_a_working_tree_that_differs_from_head_is_refused(self) -> None:
        (self.commons / "services" / "smoke.md").write_text("uncommitted change", encoding="utf-8")
        with self.assertRaises(SystemExit) as caught:
            gate.check_shared(self.out, self.commons, "public")
        self.assertIn("differs from HEAD", str(caught.exception))

    def test_a_changed_policy_for_the_notes_service_is_refused(self) -> None:
        services = self.domains()["services"]
        self.set_domains({**services, "smoke": {**services["smoke"], "hosts": ["docs.example.com", "developer.example.com"]}})
        with self.assertRaises(SystemExit) as caught:
            gate.check_shared(self.out, self.commons, "public")
        self.assertIn("policy_changed", str(caught.exception))

    def test_another_services_policy_change_is_accepted(self) -> None:
        self.set_domains({**self.domains()["services"], "beta": {"hosts": ["docs.beta.example"], "evidence_urls": [DOC], "verified_at": "2026-09-25", "status": "verified"}})
        self.assertEqual(gate.check_shared(self.out, self.commons, "public")["ok"], True)

    def test_withheld_revisions_are_rebuilt_with_the_same_set(self) -> None:
        index = json.loads((self.out / "note-index.json").read_text(encoding="utf-8"))
        trial = next(note for note in index["notes"] if note["grade"] == "trial")
        withheld = frozenset({(trial["note_id"], trial["revision"])})
        builder.build(self.out, content_root=self.commons, audience="public", withheld=withheld)
        self.assertEqual(gate.check_shared(self.out, self.commons, "public", withheld=withheld)["ok"], True)
        shipped = json.loads((self.out / "note-index.json").read_text(encoding="utf-8"))["notes"]
        self.assertNotIn((trial["note_id"], trial["revision"]), {(note["note_id"], note["revision"]) for note in shipped})
        with self.assertRaises(SystemExit) as caught:
            gate.check_shared(self.out, self.commons, "public")
        self.assertIn("exact rebuild", str(caught.exception))

    def test_recalled_note_is_absent_from_the_index(self) -> None:
        index = json.loads((self.out / "note-index.json").read_text(encoding="utf-8"))
        self.assertFalse(any(note["note_id"].endswith("other-%s" % UUID_C) for note in index["notes"]))

    def test_personal_audience_has_no_shared_contract(self) -> None:
        with self.assertRaises(SystemExit):
            gate.check_shared(self.out, self.commons, "personal")

    def test_cli_verifies_against_the_content_root_history(self) -> None:
        with redirect_stdout(io.StringIO()):
            self.assertEqual(gate.main([str(self.out), "--audience", "public", "--content-root", str(self.commons)]), 0)
        with self.assertRaises(SystemExit):
            gate.main([str(self.out), "--audience", "public"])


if __name__ == "__main__":
    unittest.main()
