"""Local skill folders served by portwright-skills: identity, reads, and guarded writes."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from portwright import skills_local as sl  # noqa: E402


def skill_md(name: str, description: str = "Does one thing.") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n"


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class LocalSkillsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.codex = self.tmp / "codex"
        self.agents = self.tmp / "agents"
        for root in (self.codex, self.agents):
            root.mkdir()
        self.write(self.codex / "re0" / "SKILL.md", skill_md("re0"))
        self.write(self.codex / "re0" / "references" / "guide.md", "guide\n")
        self.skills = sl.LocalSkills({"codex": self.codex, "agents": self.agents})

    def write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_entries_use_folder_ids_and_skip_mismatched_names(self):
        self.write(self.agents / "wrong-dir" / "SKILL.md", skill_md("other-name"))
        self.write(self.agents / "no-desc" / "SKILL.md", "---\nname: no-desc\n---\n")
        entries = {entry.uri: entry for entry in self.skills.entries()}
        self.assertEqual(set(entries), {"skill://portwright/local/codex/re0/SKILL.md"})
        entry = entries["skill://portwright/local/codex/re0/SKILL.md"]
        self.assertEqual((entry.name, entry.source, entry.root_id), ("re0", "local:codex", "codex"))
        self.assertEqual(sorted(f["path"] for f in entry.files), ["SKILL.md", "references/guide.md"])
        self.assertEqual(entry.digest, digest((self.codex / "re0" / "SKILL.md").read_bytes()))

    def test_folded_description_is_one_line(self):
        text = "---\nname: folded\ndescription: >-\n  First line\n  second line.\nversion: 1\n---\n"
        self.assertEqual(sl.frontmatter(text), {"name": "folded", "description": "First line second line.", "version": "1"})
        self.assertEqual(sl.frontmatter('---\nname: "q"\ndescription: \'x: y\'\n---\n')["description"], "x: y")
        self.assertEqual(sl.frontmatter("---\nname: lit\ndescription: |\n  a\n  b\n---\n")["description"], "a\nb")

    def test_resolve_refuses_parent_encoded_and_foreign_paths(self):
        self.assertEqual(self.skills.resolve("skill://portwright/local/codex/re0/SKILL.md"),
                         ("codex", self.codex / "re0" / "SKILL.md"))
        for uri in ("skill://portwright/local/codex/../agents/x/SKILL.md",
                    "skill://portwright/local/codex/re0%2f..%2fSKILL.md",
                    "skill://portwright/local/codex/re0/%2E%2E/SKILL.md",
                    "skill://portwright/local/nope/re0/SKILL.md",
                    "skill://portwright/personal/aim/SKILL.md"):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                self.skills.resolve(uri)

    def test_create_refuses_existing_bad_name_and_bad_frontmatter(self):
        created = self.skills.create("new-skill", skill_md("new-skill"), "agents")
        self.assertEqual(created.uri, "skill://portwright/local/agents/new-skill/SKILL.md")
        self.assertEqual((self.agents / "new-skill" / "SKILL.md").read_text(), skill_md("new-skill"))
        cases = [("re0", skill_md("re0"), "codex", "exists"),
                 ("Bad_Name", skill_md("Bad_Name"), None, "name_invalid"),
                 ("x" * 65, skill_md("x" * 65), None, "name_invalid"),
                 ("mismatch", skill_md("other"), None, "frontmatter_invalid"),
                 ("nodesc", "---\nname: nodesc\ndescription:\n---\n", None, "frontmatter_invalid")]
        for name, text, source, code in cases:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, f"^{code}$"):
                self.skills.create(name, text, source)
        self.assertFalse((self.codex / "mismatch").exists())

    def test_update_requires_current_digest_and_keeps_supporting_files(self):
        uri = "skill://portwright/local/codex/re0/SKILL.md"
        old = (self.codex / "re0" / "SKILL.md").read_bytes()
        new_text = skill_md("re0", "Updated.")
        with self.assertRaisesRegex(ValueError, "^digest_conflict$"):
            self.skills.update(uri, new_text, "sha256:" + "0" * 64)
        self.assertEqual((self.codex / "re0" / "SKILL.md").read_bytes(), old)
        entry = self.skills.update(uri, new_text, digest(old))
        self.assertEqual(entry.digest, digest(new_text.encode()))
        self.assertEqual((self.codex / "re0" / "references" / "guide.md").read_text(), "guide\n")
        with self.assertRaisesRegex(ValueError, "^frontmatter_invalid$"):
            self.skills.update(uri, skill_md("renamed"), entry.digest)

    def test_update_refuses_symlinked_skill_dir_but_reads_it(self):
        target = self.tmp / "elsewhere" / "linked"
        self.write(target / "SKILL.md", skill_md("linked"))
        (self.agents / "linked").symlink_to(target, target_is_directory=True)
        uri = "skill://portwright/local/agents/linked/SKILL.md"
        self.assertIn(uri, {entry.uri for entry in self.skills.entries()})
        self.assertEqual(self.skills.read(uri), skill_md("linked").encode())
        with self.assertRaisesRegex(ValueError, "^symlink_refused$"):
            self.skills.update(uri, skill_md("linked", "Changed."), digest(skill_md("linked").encode()))
        self.assertEqual((target / "SKILL.md").read_text(), skill_md("linked"))

    def test_symlinked_root_is_allowed(self):
        linked_root = self.tmp / "root-link"
        linked_root.symlink_to(self.codex, target_is_directory=True)
        skills = sl.LocalSkills({"codex": linked_root})
        uri = "skill://portwright/local/codex/re0/SKILL.md"
        old = (self.codex / "re0" / "SKILL.md").read_bytes()
        skills.update(uri, skill_md("re0", "Via link."), digest(old))
        self.assertIn("Via link.", (self.codex / "re0" / "SKILL.md").read_text())

    def test_failed_replace_leaves_original(self):
        uri = "skill://portwright/local/codex/re0/SKILL.md"
        old = (self.codex / "re0" / "SKILL.md").read_bytes()
        with patch.object(sl.os, "replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.skills.update(uri, skill_md("re0", "Lost."), digest(old))
        self.assertEqual((self.codex / "re0" / "SKILL.md").read_bytes(), old)
        self.assertEqual(sorted(p.name for p in (self.codex / "re0").iterdir()), ["SKILL.md", "references"])
        self.assertFalse((self.codex / sl.LOCK_NAME).exists())

    def test_concurrent_lock_rejects_second_writer(self):
        (self.codex / sl.LOCK_NAME).mkdir()
        with self.assertRaisesRegex(ValueError, "^locked$"):
            self.skills.create("second", skill_md("second"), "codex")
        (self.codex / sl.LOCK_NAME).rmdir()
        results: list[str] = []

        def attempt(name: str) -> None:
            try:
                self.skills.create(name, skill_md(name), "codex")
                results.append("ok")
            except ValueError as error:
                results.append(str(error))

        threads = [threading.Thread(target=attempt, args=(f"par-{i}",)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertTrue(set(results) <= {"ok", "locked"} and "ok" in results)
        self.assertEqual(results.count("ok"), len([p for p in self.codex.iterdir() if p.name.startswith("par-")]))

    def test_unreadable_file_skips_only_that_skill(self):
        self.write(self.agents / "broken" / "SKILL.md", skill_md("broken"))
        (self.agents / "broken" / "dangling.md").symlink_to(self.tmp / "absent.md")
        names = {entry.name for entry in self.skills.entries()}
        self.assertIn("re0", names)
        self.assertNotIn("broken", names)

    def test_size_limits(self):
        with patch.object(sl, "MAX_BYTES", 64), self.assertRaisesRegex(ValueError, "^too_large$"):
            self.skills.create("big", skill_md("big", "x" * 100), "codex")
        for index in range(3):
            self.write(self.agents / "many" / f"f{index}.md", "x")
        self.write(self.agents / "many" / "SKILL.md", skill_md("many"))
        with patch.object(sl, "MAX_FILES", 3):
            self.assertNotIn("many", {entry.name for entry in self.skills.entries()})


class ConfigTest(unittest.TestCase):
    def test_missing_file_is_empty_and_home_is_expanded(self):
        tmp = Path(tempfile.mkdtemp())
        empty = sl.load_config(tmp)
        self.assertEqual((empty.local, empty.remote_url, empty.token_file, empty.work_repo), ({}, None, None, None))
        (tmp / "_local").mkdir()
        (tmp / "_local" / "skills.json").write_text(json.dumps({
            "local": {"codex": "~/.codex/skills"},
            "remote": {"url": "https://personal.example.dev/mcp", "token_file": "~/.config/portwright/t"},
            "work_repo": "~/repo"}))
        config = sl.load_config(tmp)
        home = Path(os.path.expanduser("~"))
        self.assertEqual(config.local, {"codex": home / ".codex/skills"})
        self.assertEqual((config.remote_url, config.token_file, config.work_repo),
                         ("https://personal.example.dev/mcp", home / ".config/portwright/t", home / "repo"))


if __name__ == "__main__":
    unittest.main()
