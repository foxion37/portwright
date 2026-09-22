#!/usr/bin/env python3
"""Tests for scripts/build_skill_bundles.py: complete-package union, personal skills,
digest-bound inventory, watch-sources export, and output-destination safety.

Each test builds a synthetic git repository in a temp directory; no user skills or
configuration are read.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "lib"))

import build_skill_bundles as builder  # noqa: E402
from tests._v2 import write_service  # noqa: E402

PACKAGED = ("portwright-tool-use", "portwright-tool-memory")


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


class RepoCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = (Path(self.tmp.name) / "repo").resolve()
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "config", "user.name", "Test")
        for skill in PACKAGED:
            write_skill(self.repo, f"skills/{skill}/SKILL.md", skill_md(skill))
        self.commit()
        self._old_root = builder.ROOT
        builder.ROOT = self.repo
        self.addCleanup(setattr, builder, "ROOT", self._old_root)
        self.out = (Path(self.tmp.name) / "out").resolve()

    def commit(self) -> None:
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "test")

    def build(self) -> dict:
        return builder.build(self.out)

class TestBundleContents(RepoCase):
    def test_support_files_preserved(self) -> None:
        write_skill(self.repo, "skills/portwright-tool-use/references/checklist.md", "# checklist\n")
        write_skill(self.repo, "skills/portwright-tool-use/agents/openai.yaml", "interface: test\n")
        write_skill(self.repo, "skills/portwright-tool-use/LICENSE", "MIT\n")
        self.commit()
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
        self.commit()
        inventory = self.build()
        emitted = self.out / "personal" / "my-skill"
        self.assertEqual((emitted / "SKILL.md").read_bytes(), (self.repo / "skills/personal/my-skill/SKILL.md").read_bytes())
        self.assertEqual((emitted / "references/notes.md").read_text(), "personal notes\n")
        entry = inventory["skills"]["my-skill"]
        self.assertEqual(entry["path"], "personal/my-skill/SKILL.md")
        self.assertTrue(entry["personal"])

    def test_personal_name_mismatch_refused(self) -> None:
        write_skill(self.repo, "skills/personal/my-skill/SKILL.md", skill_md("other-name"))
        self.commit()
        with self.assertRaises(SystemExit):
            self.build()

    def test_unclassified_skill_content_refused(self) -> None:
        write_skill(self.repo, "skills/random-thing/SKILL.md", skill_md("random-thing"))
        self.commit()
        with self.assertRaises(SystemExit):
            self.build()

    def test_watch_sources_exported(self) -> None:
        write_service(self.repo, "github", extra="distributable: true\nfreshness_evidence:\n  url: https://cli.github.com/manual/gh_auth_switch")
        write_service(self.repo, "other", extra="distributable: true")
        self.commit()
        self.build()
        watch = json.loads((self.out / "watch-sources.json").read_text())
        self.assertEqual(watch, {"sources": [{"skill": "github", "url": "https://cli.github.com/manual/gh_auth_switch"}]})

    def test_watch_sources_omitted_when_none(self) -> None:
        write_service(self.repo, "other", extra="distributable: true")
        self.commit()
        self.build()
        self.assertFalse((self.out / "watch-sources.json").exists())

    def test_inventory_binds_every_file_to_commit(self) -> None:
        write_service(self.repo, "github", extra="distributable: true")
        self.commit()
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
        path.write_text(path.read_text() + "\napi_key: abcdef123456\n")
        self.commit()
        with self.assertRaises(SystemExit):
            self.build()

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
        self.commit()
        tracked.unlink()
        tracked.symlink_to("/etc/hostname")
        with self.assertRaises(SystemExit):
            self.build()

    def test_binary_support_file_refused(self) -> None:
        path = self.repo / "skills/portwright-tool-use/blob.bin"
        path.write_bytes(b"\xff\xfe\x00\x01")
        self.commit()
        with self.assertRaises(SystemExit):
            self.build()

    def test_invalid_distributable_note_refused(self) -> None:
        write_service(self.repo, "broken", extra="distributable: true\nstatus: bogus")
        self.commit()
        with self.assertRaises(SystemExit):
            self.build()

    def test_non_distributable_note_skipped(self) -> None:
        write_service(self.repo, "personal-only", extra="distributable: false")
        self.commit()
        inventory = self.build()
        self.assertIn("services/personal-only.md", inventory["skipped_not_distributable"])
        self.assertNotIn("personal-only", inventory["skills"])


if __name__ == "__main__":
    unittest.main()
