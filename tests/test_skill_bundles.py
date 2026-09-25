#!/usr/bin/env python3
"""Tests for scripts/build_skill_bundles.py: the historical M1 API, the personal v1 mode
(approved packages plus fixed code guides) and the shared v2 mode (stable/trial packages,
verbatim note bytes, note-index coverage, recall exclusion).

Each test builds a synthetic git repository in a temp directory; no user skills or
configuration are read.
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "lib"))

import build_skill_bundles as builder  # noqa: E402
import publish_release  # noqa: E402
from portwright import hub_contracts  # noqa: E402
from tests._v2 import write_service  # noqa: E402

H1_FIXTURE = ROOT / "hub" / "test" / "fixtures" / "release-v2"
H1_READER = ROOT / "hub" / "src" / "release-reader.ts"

PACKAGED = ("portwright-tool-use", "portwright-tool-memory")
DOC = "https://docs.example.com/reference"
UUID_A = "11111111-1111-4111-8111-111111111111"
UUID_B = "22222222-2222-4222-8222-222222222222"
UUID_C = "33333333-3333-4333-8333-333333333333"


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_skill(repo: Path, relative: str, text: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def skill_md(name: str, extra: str = "") -> str:
    lines = ["---", f"name: {name}", "description: synthetic test skill", *([extra] if extra else []), "---", "", f"# {name}", ""]
    return "\n".join(lines)


def canon_note(kind: str, service_id: str, body: str, *, uuid: str, grade: str = "stable") -> str:
    """A commons canon note shaped exactly like the Hub writer's committed bytes."""
    head = ["---"]
    if kind == "procedure":
        head += [f"id: {service_id}", 'display_name: "Synthetic Service"', 'version_tag: "1.0"',
                 'last_verified: "2026-09-25"', "status: active"]
    else:
        head += ["date: 2026-09-25", f"service: {service_id}", 'service_version: "1.0"', "status: active"]
    head += ["distributable: true", f"doc_url: {DOC}"]
    text = "\n".join(head + ["---", "", body, ""])
    revision = hub_contracts.note_revision(kind, service_id, text, DOC)
    updates = {"grade": grade, "revision": revision, "intake_id": uuid, "doc_digest": "sha256:" + "a" * 64,
               "gate_version": "hub-gates-v1", "gate_digest": "sha256:" + "b" * 64}
    return hub_contracts.render_note(text, updates=updates)


def note_revision_of(text: str, kind: str, service_id: str) -> str:
    return hub_contracts.note_revision(kind, service_id, text, DOC)


def write_note(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def tree(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


class TempRepo(unittest.TestCase):
    def make_repo(self, name: str = "repo") -> Path:
        repo = (Path(self.tmp.name) / name).resolve()
        repo.mkdir()
        git(repo, "init", "-q")
        git(repo, "config", "user.email", "test@example.invalid")
        git(repo, "config", "user.name", "Test")
        return repo

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def commit(self, repo: Path) -> None:
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "test")


class RepoCase(TempRepo):
    """The historical M1 API (`build(out)` with no audience), still used by the export."""

    def setUp(self) -> None:
        super().setUp()
        self.repo = self.make_repo()
        for skill in PACKAGED:
            write_skill(self.repo, f"skills/{skill}/SKILL.md", skill_md(skill))
        self.commit(self.repo)
        self._old_root = builder.ROOT
        builder.ROOT = self.repo
        self.addCleanup(setattr, builder, "ROOT", self._old_root)
        self.out = (Path(self.tmp.name) / "out").resolve()

    def build(self) -> dict:
        return builder.build(self.out)


class TestBundleContents(RepoCase):
    def test_support_files_preserved(self) -> None:
        write_skill(self.repo, "skills/portwright-tool-use/references/checklist.md", "# checklist\n")
        write_skill(self.repo, "skills/portwright-tool-use/agents/openai.yaml", "interface: test\n")
        write_skill(self.repo, "skills/portwright-tool-use/LICENSE", "MIT\n")
        self.commit(self.repo)
        inventory = self.build()
        for relative in ("SKILL.md", "references/checklist.md", "agents/openai.yaml", "LICENSE"):
            emitted = self.out / "portwright-tool-use" / relative
            self.assertTrue(emitted.is_file(), relative)
            self.assertEqual(emitted.read_bytes(), (self.repo / "skills/portwright-tool-use" / relative).read_bytes())
        listed = {entry["path"] for entry in inventory["files"]}
        self.assertIn("portwright-tool-use/references/checklist.md", listed)

    def test_personal_nested_identity_and_files(self) -> None:
        write_skill(self.repo, "skills/personal/my-skill/SKILL.md", skill_md("my-skill", "disable-model-invocation: true"))
        write_skill(self.repo, "skills/personal/my-skill/references/notes.md", "personal notes\n")
        self.commit(self.repo)
        inventory = self.build()
        emitted = self.out / "personal" / "my-skill"
        self.assertEqual((emitted / "SKILL.md").read_bytes(), (self.repo / "skills/personal/my-skill/SKILL.md").read_bytes())
        self.assertEqual((emitted / "references/notes.md").read_text(), "personal notes\n")
        entry = inventory["skills"]["my-skill"]
        self.assertEqual(entry["path"], "personal/my-skill/SKILL.md")
        self.assertTrue(entry["personal"])

    def test_personal_name_mismatch_refused(self) -> None:
        write_skill(self.repo, "skills/personal/my-skill/SKILL.md", skill_md("other-name"))
        self.commit(self.repo)
        with self.assertRaises(SystemExit):
            self.build()

    def test_unclassified_skill_content_refused(self) -> None:
        write_skill(self.repo, "skills/random-thing/SKILL.md", skill_md("random-thing"))
        self.commit(self.repo)
        with self.assertRaises(SystemExit):
            self.build()

    def test_watch_sources_exported(self) -> None:
        write_service(self.repo, "github", extra="distributable: true\nfreshness_evidence:\n  url: https://cli.github.com/manual/gh_auth_switch")
        write_service(self.repo, "other", extra="distributable: true")
        self.commit(self.repo)
        self.build()
        watch = json.loads((self.out / "watch-sources.json").read_text())
        self.assertEqual(watch, {"sources": [{"skill": "github", "url": "https://cli.github.com/manual/gh_auth_switch"}]})

    def test_watch_sources_omitted_when_none(self) -> None:
        write_service(self.repo, "other", extra="distributable: true")
        self.commit(self.repo)
        self.build()
        self.assertFalse((self.out / "watch-sources.json").exists())

    def test_inventory_binds_every_file_to_commit(self) -> None:
        write_service(self.repo, "github", extra="distributable: true")
        self.commit(self.repo)
        inventory = self.build()
        commit = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(inventory["commit"], commit)
        emitted = {p.relative_to(self.out).as_posix() for p in self.out.rglob("*") if p.is_file()}
        listed = {entry["path"] for entry in inventory["files"]}
        self.assertEqual(emitted - {"inventory.json", builder.MARKER}, listed)
        for entry in inventory["files"]:
            data = (self.out / entry["path"]).read_bytes()
            self.assertEqual(entry["sha256"], sha256(data))
            self.assertEqual(entry["size"], len(data))


class TestRefusals(RepoCase):
    def test_dangerous_output_destination_refused(self) -> None:
        victim = Path(self.tmp.name) / "precious"
        victim.mkdir()
        (victim / "keep.txt").write_text("do not delete")
        with self.assertRaises(SystemExit):
            builder.build(victim)
        self.assertEqual((victim / "keep.txt").read_text(), "do not delete")

    def test_output_symlink_refused(self) -> None:
        target = Path(self.tmp.name) / "target"
        target.mkdir()
        link = Path(self.tmp.name) / "link"
        link.symlink_to(target)
        with self.assertRaises(SystemExit):
            builder.build(link)

    def test_rebuild_replaces_own_output(self) -> None:
        self.build()
        marker = self.out / builder.MARKER
        self.assertTrue(marker.is_file())
        self.build()  # second build must not refuse its own previous output
        self.assertTrue((self.out / "inventory.json").is_file())

    def test_secret_in_distributable_note_refused(self) -> None:
        path = write_service(self.repo, "leaky", extra="distributable: true")
        path.write_text(path.read_text() + "\n" + "api_key" + ": " + "abcdef" + "123456" + "\n")
        self.commit(self.repo)
        with self.assertRaises(SystemExit):
            self.build()

    def test_identifier_in_public_bundle_refused_but_personal_remains_local(self) -> None:
        write_skill(self.repo, "skills/portwright-tool-use/references/local.md",
                    "private host " + ".".join(("100", "64", "0", "1")) + "\n")
        self.commit(self.repo)
        with self.assertRaises(SystemExit) as caught:
            self.build()
        self.assertIn("skills/portwright-tool-use/references/local.md", str(caught.exception))
        self.assertIn("private-network", str(caught.exception))
        self.assertNotIn(".".join(("100", "64", "0", "1")), str(caught.exception))

    def test_public_note_home_is_anonymized_without_changing_source(self) -> None:
        path = write_service(self.repo, "local", extra="distributable: true")
        path.write_text(path.read_text() + "\nUsed /users/" + "ab" + "/cache to reproduce.\n")
        self.commit(self.repo)
        inventory = self.build()
        output = (self.out / "local" / "SKILL.md").read_text()
        self.assertIn("~/cache", output)
        self.assertNotIn("/users/" + "ab" + "/", output)
        self.assertIn("/users/" + "ab" + "/", path.read_text())
        self.assertIn("local", inventory["skills"])

    def test_private_tracked_note_skipped_never_published(self) -> None:
        path = write_service(self.repo, "hidden", extra="distributable: true", private=True)
        git(self.repo, "add", "-f", str(path.relative_to(self.repo)))
        git(self.repo, "commit", "-qm", "private")
        inventory = self.build()
        self.assertIn("services/_private/hidden.md", inventory["skipped_private_or_draft"])
        self.assertNotIn("hidden", inventory["skills"])
        self.assertFalse((self.out / "hidden").exists())

    def test_symlinked_support_file_refused(self) -> None:
        tracked = self.repo / "skills/portwright-tool-use/references.md"
        tracked.write_text("x")
        self.commit(self.repo)
        tracked.unlink()
        tracked.symlink_to("/etc/hostname")
        with self.assertRaises(SystemExit):
            self.build()

    def test_binary_support_file_refused(self) -> None:
        path = self.repo / "skills/portwright-tool-use/blob.bin"
        path.write_bytes(b"\xff\xfe\x00\x01")
        self.commit(self.repo)
        with self.assertRaises(SystemExit):
            self.build()

    def test_invalid_distributable_note_refused(self) -> None:
        write_service(self.repo, "broken", extra="distributable: true\nstatus: bogus")
        self.commit(self.repo)
        with self.assertRaises(SystemExit):
            self.build()

    def test_non_distributable_note_skipped(self) -> None:
        write_service(self.repo, "personal-only", extra="distributable: false")
        self.commit(self.repo)
        inventory = self.build()
        self.assertIn("services/personal-only.md", inventory["skipped_not_distributable"])
        self.assertNotIn("personal-only", inventory["skills"])


class SharedCase(TempRepo):
    """A commons checkout: stable notes, a trial update sibling, a recalled lesson."""

    def setUp(self) -> None:
        super().setUp()
        self.commons = self.make_repo("commons")
        self.other_revision = None
        self.populate()
        self.commit(self.commons)
        self.out = (Path(self.tmp.name) / "shared").resolve()

    def populate(self) -> None:
        write_note(self.commons, "services/smoke.md", canon_note("procedure", "smoke", "## Steps\n\nCheck the state.", uuid=UUID_A))
        write_note(self.commons, "trial/services/smoke.md",
                   canon_note("procedure", "smoke", "## Steps\n\nCheck the new state.", uuid=UUID_B, grade="trial"))
        write_note(self.commons, "failures/2026-09-25-smoke-%s.md" % UUID_A,
                   canon_note("lesson", "smoke", "## Root cause\n\nShort fix.", uuid=UUID_A))
        recalled = write_note(self.commons, "failures/2026-09-25-other-%s.md" % UUID_C,
                              canon_note("lesson", "other", "## Root cause\n\nWithdrawn.", uuid=UUID_C))
        self.other_revision = note_revision_of(recalled.read_text(), "lesson", "other")
        hex_digest = self.other_revision.split(":", 1)[1]
        write_note(self.commons, f"recalls/failures/other/{hex_digest}.json", json.dumps({
            "schema_version": 1, "note_id": "failure/2026-09-25-other-%s" % UUID_C,
            "revision": self.other_revision, "reason_code": "distinct_reports", "created": 1790294400,
            "event_ids": [UUID_A, UUID_B], "operator_review_event_id": None,
        }))
        write_note(self.commons, "policy/official-doc-domains.json", json.dumps({
            "schema_version": 1,
            "services": {"smoke": {"hosts": ["docs.example.com"], "evidence_urls": [DOC], "verified_at": "2026-09-25", "status": "verified"}},
        }))
        # Shared mode must never look at skills/, private or profile content.
        write_note(self.commons, "skills/personal/leak/SKILL.md", skill_md("leak"))
        write_note(self.commons, "profiles/mine/profile.md", "# profile\n")

    def build(self, audience: str = "public", out: Path | None = None) -> dict:
        return builder.build(out or self.out, content_root=self.commons, audience=audience)

    def note_paths(self) -> set[str]:
        return {p.relative_to(self.out).as_posix() for p in self.out.rglob("*") if p.is_file()}

    def index(self) -> dict:
        return json.loads((self.out / "note-index.json").read_text(encoding="utf-8"))


class TestSharedBuild(SharedCase):
    def test_grade_split_keeps_verbatim_note_bytes(self) -> None:
        inventory = self.build()
        self.assertEqual(inventory["schema_version"], 2)
        self.assertEqual(inventory["audience"], "public")
        self.assertEqual(inventory["commit"], subprocess.run(
            ["git", "-C", str(self.commons), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip())
        self.assertEqual({p for p in self.note_paths() if p.startswith("stable/")}, {
            "stable/smoke/SKILL.md",
            "stable/smoke/notes/smoke.md",
            "stable/smoke/notes/2026-09-25-smoke-%s.md" % UUID_A,
        })
        self.assertEqual({p for p in self.note_paths() if p.startswith("trial/")},
                         {"trial/smoke/SKILL.md", "trial/smoke/notes/smoke.md"})
        for shipped, source in (
            ("stable/smoke/notes/smoke.md", "services/smoke.md"),
            ("trial/smoke/notes/smoke.md", "trial/services/smoke.md"),
        ):
            self.assertEqual((self.out / shipped).read_bytes(), (self.commons / source).read_bytes())
        skill = (self.out / "stable" / "smoke" / "SKILL.md").read_text()
        self.assertIn("name: smoke", skill)
        self.assertIn("grade: stable", skill)
        self.assertIn("service/smoke", skill)
        self.assertEqual(inventory["skills"]["stable/smoke"]["grade"], "stable")
        self.assertNotEqual(inventory["skills"]["stable/smoke"]["note_refs"], inventory["skills"]["trial/smoke"]["note_refs"])

    def test_note_index_covers_files_and_skill_references(self) -> None:
        self.build()
        index = self.index()
        files = {entry["path"]: entry for entry in json.loads((self.out / "inventory.json").read_text())["files"]}
        self.assertEqual(index["audience"], "public")
        self.assertEqual({note["path"] for note in index["notes"]}, {p for p in files if "/notes/" in p})
        for note in index["notes"]:
            self.assertEqual(files[note["path"]]["sha256"], note["file_digest"].split(":", 1)[1])
            self.assertEqual(files[note["path"]]["size"], note["size"])
            self.assertTrue(note["uri"].startswith("skill://gisul/commons/"))
        self.assertEqual(hub_contracts.validate_note_index(index)["ok"], True)
        for name, skill in json.loads((self.out / "inventory.json").read_text())["skills"].items():
            grade, service = name.split("/")
            expected = {(note["note_id"], note["revision"]) for note in index["notes"] if note["grade"] == grade and note["service_id"] == service}
            self.assertEqual({(ref["note_id"], ref["revision"]) for ref in skill["note_refs"]}, expected)

    def test_initial_trial_note_at_the_canonical_path(self) -> None:
        """A first submission is trial but still lives at services/<id>.md (§4.2)."""
        write_note(self.commons, "services/fresh.md", canon_note("procedure", "fresh", "## Steps\n\nBody.", uuid=UUID_B, grade="trial"))
        self.commit(self.commons)
        inventory = self.build()
        self.assertEqual(set(inventory["skills"]), {"stable/smoke", "trial/fresh", "trial/smoke"})
        self.assertEqual(inventory["skills"]["trial/fresh"]["grade"], "trial")
        self.assertEqual(inventory["skills"]["trial/fresh"]["path"], "trial/fresh/SKILL.md")
        self.assertEqual((self.out / "trial" / "fresh" / "notes" / "fresh.md").read_bytes(),
                         (self.commons / "services/fresh.md").read_bytes())
        self.assertFalse((self.out / "stable" / "fresh").exists())

    def test_stable_update_stages_the_sibling_with_the_same_note_id(self) -> None:
        """An update of a stable note ships both revisions, one per grade (§A-1)."""
        self.build()
        index = self.index()
        service = [note for note in index["notes"] if note["note_id"] == "service/smoke"]
        self.assertEqual({note["grade"] for note in service}, {"stable", "trial"})
        self.assertEqual(len({note["revision"] for note in service}), 2)

    def test_recalled_revision_is_not_shipped(self) -> None:
        inventory = self.build()
        self.assertNotIn("other", inventory["skills"])
        self.assertFalse(any("other" in path for path in self.note_paths()))
        self.assertFalse(any(note["note_id"].endswith("other-%s" % UUID_C) for note in self.index()["notes"]))

    def test_withheld_revision_is_not_shipped_but_its_sibling_is(self) -> None:
        trial = canon_note("procedure", "smoke", "## Steps\n\nCheck the new state.", uuid=UUID_B, grade="trial")
        held = ("service/smoke", note_revision_of(trial, "procedure", "smoke"))
        inventory = builder.build(self.out, content_root=self.commons, audience="public", withheld=frozenset({held}))
        self.assertEqual(set(inventory["skills"]), {"stable/smoke"})
        self.assertNotIn(held, {(note["note_id"], note["revision"]) for note in self.index()["notes"]})

    def test_withheld_policy_invalid_bytes_do_not_block_clean_siblings(self) -> None:
        text = canon_note("procedure", "smoke", "Contact person" + "@example.com", uuid=UUID_B, grade="trial")
        write_note(self.commons, "trial/services/smoke.md", text)
        self.commit(self.commons)
        held = ("service/smoke", note_revision_of(text, "procedure", "smoke"))
        builder.build(self.out, content_root=self.commons, audience="public", withheld=frozenset({held}))
        self.assertEqual({note["grade"] for note in self.index()["notes"]}, {"stable"})
        self.assertNotIn(held, {(note["note_id"], note["revision"]) for note in self.index()["notes"]})

    def test_shared_mode_never_reads_skills_or_profiles(self) -> None:
        inventory = self.build()
        self.assertFalse(any(path.startswith(("skills/", "profiles/", "personal/")) for path in self.note_paths()))
        self.assertNotIn("leak", json.dumps(inventory))

    def test_bundle_is_a_byte_stable_rebuild(self) -> None:
        self.build()
        second = Path(self.tmp.name) / "shared-again"
        self.build(out=second)
        self.assertEqual(tree(second), tree(self.out))

    def test_company_audience_uses_the_same_notes(self) -> None:
        inventory = self.build(audience="company")
        self.assertEqual(inventory["audience"], "company")
        self.assertEqual({p for p in self.note_paths() if p.startswith("stable/")},
                         {"stable/smoke/SKILL.md", "stable/smoke/notes/smoke.md", "stable/smoke/notes/2026-09-25-smoke-%s.md" % UUID_A})

    def test_cli_requires_an_explicit_audience_and_content_root(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            builder.main(["--out", str(self.out)])
        with self.assertRaises(SystemExit):
            builder.main(["--out", str(self.out), "--audience", "public"])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(builder.main(["--out", str(self.out), "--audience", "public", "--content-root", str(self.commons)]), 0)
            self.assertEqual(builder.main(["--out", str(self.out), "--audience", "company", "--content-root", str(self.commons)]), 0)


class TestSharedRefusals(SharedCase):
    def rebuild(self, relative: str, text: str, message: str) -> None:
        write_note(self.commons, relative, text)
        self.commit(self.commons)
        with self.assertRaises(SystemExit) as caught:
            self.build()
        self.assertIn(message, str(caught.exception))

    def test_missing_distributable_refused(self) -> None:
        text = canon_note("procedure", "noflag", "## Steps\n\nBody.", uuid=UUID_A).replace("distributable: true", "distributable: false")
        self.rebuild("services/noflag.md", text, "distributable")

    def test_revision_must_reproduce_from_the_bytes(self) -> None:
        text = canon_note("procedure", "tampered", "## Steps\n\nBody.", uuid=UUID_A).replace(
            note_revision_of(canon_note("procedure", "tampered", "## Steps\n\nBody.", uuid=UUID_A), "procedure", "tampered"),
            "sha256:" + "c" * 64)
        self.rebuild("services/tampered.md", text, "revision does not match its bytes")

    def test_secret_in_a_commons_note_refused(self) -> None:
        text = canon_note("lesson", "smoke", "## Root cause\n\n" + "api_key" + ": " + "abcdef" + "123456", uuid=UUID_A)
        self.rebuild("failures/2026-09-25-leak-%s.md" % UUID_B, text, "suspected secret material")

    def test_unrecognized_note_path_refused(self) -> None:
        write_note(self.commons, "services/_private/hidden.md", canon_note("procedure", "hidden", "## Steps\n\nBody.", uuid=UUID_A))
        self.commit(self.commons)
        with self.assertRaises(SystemExit):
            self.build()

    def test_stable_grade_at_a_trial_sibling_path_refused(self) -> None:
        text = canon_note("procedure", "sibling", "## Steps\n\nBody.", uuid=UUID_A, grade="stable")
        self.rebuild("trial/services/sibling.md", text, "does not match its trial path")

    def test_tracked_content_that_differs_from_head_refused(self) -> None:
        write_note(self.commons, "services/uncommitted.md", canon_note("procedure", "uncommitted", "## Steps\n\nBody.", uuid=UUID_B, grade="trial"))
        with self.assertRaises(SystemExit) as caught:
            self.build()
        self.assertIn("differs from HEAD", str(caught.exception))

    def test_recall_marker_with_an_unknown_shape_refused(self) -> None:
        write_note(self.commons, "recalls/failures/smoke.json", json.dumps({"note_id": "failure/x", "revision": "sha256:" + "0" * 64}))
        self.commit(self.commons)
        with self.assertRaises(SystemExit):
            self.build()


class TestH1WireContract(SharedCase):
    """The published v2 inventory must be accepted by the Hub reader (H1)."""

    def wire(self) -> tuple[str, dict, dict[str, bytes]]:
        self.build()
        result = publish_release.publish(self.out, "", "", "public", dry_run=True)
        text = (self.out / "inventory.gisul.json").read_text(encoding="utf-8")
        manifest = publish_release.validated_inventory(self.out)
        _, objects = publish_release.build_inventory(
            manifest, publish_release.verify_output(self.out, manifest), result["commit"], audience="public", source="commons")
        return text, json.loads(text), objects

    def test_wire_inventory_matches_h1_fixture_shape(self) -> None:
        fixture = json.loads((H1_FIXTURE / "inventory.json").read_text(encoding="utf-8"))
        fixture_index = json.loads((H1_FIXTURE / "objects" / "note-index.json").read_text(encoding="utf-8"))
        _, inventory, objects = self.wire()
        self.assertEqual(set(inventory), set(fixture))
        self.assertEqual(set(inventory["note_index"]), set(fixture["note_index"]))
        skill_keys = {frozenset(skill) for skill in fixture["skills"]}
        self.assertEqual({frozenset(skill) for skill in inventory["skills"]}, skill_keys)
        self.assertEqual({frozenset(entry) for entry in inventory["files"]}, {frozenset(entry) for entry in fixture["files"]})
        for skill in inventory["skills"]:
            self.assertNotIn("origin", skill)
            self.assertTrue(skill["uri"].startswith(f"skill://gisul/commons/{skill['grade']}/"))
            self.assertTrue(skill["note_refs"])
        for entry in inventory["files"]:
            data = objects[entry["path"]]
            self.assertEqual((entry["digest"], entry["size"]), ("sha256:" + sha256(data), len(data)))
            if "uri" in entry:
                self.assertRegex(entry["path"], r"^(stable|trial)/")
                self.assertEqual(entry["uri"], "skill://gisul/commons/" + entry["path"])
            else:
                self.assertIn(entry["path"], {"note-index.json", "release.json"})
        listed = next(entry for entry in inventory["files"] if entry["path"] == "note-index.json")
        self.assertEqual((listed["digest"], listed["size"]), (inventory["note_index"]["digest"], inventory["note_index"]["size"]))
        index = json.loads(objects["note-index.json"])
        self.assertEqual(set(index), set(fixture_index))
        self.assertEqual({frozenset(note) for note in index["notes"]}, {frozenset(note) for note in fixture_index["notes"]})

    def test_h1_reader_accepts_builder_output(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available")
        text, inventory, objects = self.wire()
        (Path(self.tmp.name) / "wire.json").write_text(text, encoding="utf-8")
        (Path(self.tmp.name) / "index.json").write_bytes(objects["note-index.json"])
        script = (
            "const [inv, idx, reader] = process.argv.slice(-3);"
            "const fs = await import('node:fs'); const crypto = await import('node:crypto');"
            "const {parseInventory, validateNoteIndex} = await import(reader);"
            "const text = fs.readFileSync(inv, 'utf8'); const data = JSON.parse(text);"
            "const identity = {commit: data.commit, release: data.release,"
            " inventory_digest: 'sha256:' + crypto.createHash('sha256').update(text).digest('hex')};"
            "validateNoteIndex(parseInventory(text, identity, data.audience), JSON.parse(fs.readFileSync(idx, 'utf8')));"
            "process.stdout.write('H1 OK');"
        )
        run = subprocess.run(
            [node, "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script,
             str(Path(self.tmp.name) / "wire.json"), str(Path(self.tmp.name) / "index.json"), H1_READER.as_uri()],
            capture_output=True, text=True, timeout=60)
        self.assertEqual((run.returncode, run.stdout), (0, "H1 OK"), run.stderr[-400:])
        self.assertEqual(inventory["audience"], "public")


class TestPersonalBuild(TempRepo):
    def setUp(self) -> None:
        super().setUp()
        code = self.make_repo("code")
        for skill in PACKAGED:
            write_skill(code, f"skills/{skill}/SKILL.md", skill_md(skill))
        self.commit(code)
        old_root = builder.ROOT
        builder.ROOT = code
        self.addCleanup(setattr, builder, "ROOT", old_root)
        self.repo = self.make_repo("personal")
        self.write_package("demo")
        write_note(self.repo, "services/not-in-personal.md", canon_note("procedure", "smoke", "## Steps\n\nBody.", uuid=UUID_A))
        self.approval = Path(self.tmp.name) / "approval.json"
        self.approval.write_text(json.dumps({"skills": [self.approval_entry("demo")]}), encoding="utf-8")
        self._old_approval = builder.APPROVAL_FILE
        builder.APPROVAL_FILE = self.approval
        self.addCleanup(setattr, builder, "APPROVAL_FILE", self._old_approval)
        self.commit(self.repo)
        self.out = (Path(self.tmp.name) / "personal-out").resolve()

    def write_package(self, name: str) -> None:
        write_skill(self.repo, f"skills/personal/{name}/SKILL.md", skill_md(name))
        write_skill(self.repo, f"skills/personal/{name}/references/guide.md", "# guide\n")

    def approval_entry(self, name: str) -> dict:
        files = []
        for relative in sorted((self.repo / f"skills/personal/{name}").rglob("*")):
            if relative.is_file():
                data = relative.read_bytes()
                files.append({"path": relative.relative_to(self.repo / f"skills/personal/{name}").as_posix(),
                              "sha256": sha256(data), "bytes": len(data)})
        return {"name": name, "files": files}

    def build(self) -> dict:
        return builder.build(self.out, content_root=self.repo, audience="personal")

    def test_only_approved_packages_and_code_guides(self) -> None:
        inventory = self.build()
        self.assertNotIn("schema_version", inventory)
        self.assertNotIn("note_index", inventory)
        self.assertEqual(set(inventory["skills"]), {"demo", *PACKAGED})
        self.assertTrue(inventory["skills"]["demo"]["personal"])
        self.assertEqual(inventory["skills"]["demo"]["path"], "personal/demo/SKILL.md")
        self.assertEqual(inventory["skills"]["portwright-tool-use"]["origin"], "code")
        for relative in ("personal/demo/SKILL.md", "personal/demo/references/guide.md"):
            source = self.repo / "skills" / relative
            self.assertEqual((self.out / relative).read_bytes(), source.read_bytes())
        self.assertNotIn("smoke", inventory["skills"])
        self.assertFalse((self.out / "smoke").exists())

    def test_unapproved_package_refused(self) -> None:
        self.write_package("extra")
        self.commit(self.repo)
        with self.assertRaises(SystemExit) as caught:
            self.build()
        self.assertIn("not in the approved inventory", str(caught.exception))

    def test_package_bytes_must_match_the_approval(self) -> None:
        write_skill(self.repo, "skills/personal/demo/references/guide.md", "# changed\n")
        self.commit(self.repo)
        with self.assertRaises(SystemExit) as caught:
            self.build()
        self.assertIn("differ from the approved inventory", str(caught.exception))

    def test_missing_approval_inventory_refuses_every_package(self) -> None:
        builder.APPROVAL_FILE = Path(self.tmp.name) / "absent.json"
        with self.assertRaises(SystemExit) as caught:
            self.build()
        self.assertIn("not in the approved inventory", str(caught.exception))

    def test_personal_rebuild_replaces_own_output(self) -> None:
        self.build()
        self.build()
        self.assertTrue((self.out / builder.MARKER).is_file())


if __name__ == "__main__":
    unittest.main()
