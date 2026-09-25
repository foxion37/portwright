from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch
from tests._v2 import preflight_json


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from portwright.update import run_update


class UpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix=".update-test-", dir=ROOT)
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        environment = patch.dict(os.environ, {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.origin = self.base / "origin.git"
        self.seed = self.base / "seed"
        self.local = self.base / "local"
        self.git(self.base, "init", "--bare", "--initial-branch=main", str(self.origin))
        self.git(self.base, "clone", str(self.origin), str(self.seed))
        self.put(self.seed, "VERSION", "1.0.0\n")
        self.put(self.seed, ".gitignore", "services/_private/\nfailures/_private/\n")
        self.put(self.seed, "services/example.md", self.service("example"))
        self.put(self.seed, "services/recent.md", self.service("recent", age=89))
        self.put(self.seed, "services/boundary.md", self.service("boundary", age=90))
        self.put(self.seed, "services/broken.md", self.service("broken").replace("id: broken\n", ""))
        self.put(self.seed, "services/nonpublic.md", self.service("nonpublic").replace("distributable: true", "distributable: false"))
        self.put(self.seed, "services/unclassified.md", self.service("unclassified", age=0).replace("distributable: true\n", ""))
        self.put(self.seed, "failures/2026-01-01-example-stale.md", "---\ndate: 2026-01-01\nservice: example\nservice_version: v1\nstatus: stale\ndistributable: true\n---\n## Root cause\nAn obsolete setting was selected.\n")
        self.put(self.seed, "unrelated.txt", "original\n")
        self.commit(self.seed)
        self.git(self.seed, "push", "origin", "main")
        self.git(self.base, "clone", str(self.origin), str(self.local))
        self.put(self.local, "services/_private/personal.md", "local-only procedure\n")
        self.put(self.local, "failures/_private/personal.md", "local-only lesson\n")
        self.put(self.seed, "VERSION", "2.0.0\n")
        self.put(self.seed, "services/example.md", self.service("example") + "\nUpdated procedure.\n")
        self.commit(self.seed)
        self.git(self.seed, "push", "origin", "main")

    def git(self, root: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=portwright", "-c", "user.email=portwright@local", "-C", str(root), *args],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def put(self, root: Path, path: str, text: str) -> None:
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")

    def commit(self, root: Path) -> None:
        self.git(root, "add", ".")
        self.git(root, "commit", "-m", "Update fixture")

    def service(self, service_id: str, *, age: int = 100) -> str:
        verified = date.today() - timedelta(days=age)
        return (
            f"---\nid: {service_id}\ndisplay_name: Example\nversion_tag: v1\n"
            f"last_verified: {verified.isoformat()}\nendpoint:\n  type: cli\n  server: example\n"
            "human_steps:\n  - None\nagent_can:\n  - Read documentation\n"
            "status: active\ndistributable: true\n---\nExample procedure.\n"
        )

    def snapshot(self) -> dict[str, bytes]:
        return {
            path.relative_to(self.local).as_posix(): path.read_bytes()
            for path in self.local.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(self.local).parts
        }

    def test_actual_update(self) -> None:
        from portwright import update

        expected_head = self.git(self.seed, "rev-parse", "HEAD")
        real_git = update._git

        def advance_remote(root: Path, *args: str) -> str:
            if args[0] == "pull":
                self.put(self.seed, "VERSION", "3.0.0\n")
                self.put(self.seed, "services/_private/personal.md", "upstream collision\n")
                self.git(self.seed, "add", "-f", "services/_private/personal.md")
                self.commit(self.seed)
                self.git(self.seed, "push", "origin", "main")
            return real_git(root, *args)

        with patch.object(update, "_git", side_effect=advance_remote):
            report = run_update(self.local, dry_run=False)
        self.assertEqual((self.local / "VERSION").read_text(), "2.0.0\n")
        self.assertEqual(self.git(self.local, "rev-parse", "HEAD"), expected_head)
        self.assertIn("Updated procedure.", (self.local / "services/example.md").read_text())
        self.assertEqual(report.current_version, "2.0.0")
        self.assertEqual(report.latest_version, "2.0.0")
        self.assertEqual(report.notes_pulled, 1)
        self.assertEqual((report.stale_candidates, report.revalidated), (5, 4))
        self.assertEqual(report.unclassified, ["services/unclassified.md"])
        self.assertEqual(report.private_excluded, ["failures/_private/personal.md", "services/_private/personal.md"])
        self.assertEqual((self.local / "services/_private/personal.md").read_text(), "local-only procedure\n")
        before_retry = self.snapshot()
        refused = run_update(self.local, dry_run=False)
        self.assertEqual(refused.refused_conflicts, ["services/_private/personal.md"])
        self.assertEqual(refused.notes_pulled, 0)
        self.assertEqual(self.snapshot(), before_retry)

    def test_dry_run_unchanged(self) -> None:
        before, head = self.snapshot(), self.git(self.local, "rev-parse", "HEAD")
        index = self.git(self.local, "ls-files", "--stage")
        report = run_update(self.local, dry_run=True)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.git(self.local, "rev-parse", "HEAD"), head)
        self.assertEqual(self.git(self.local, "ls-files", "--stage"), index)
        self.assertEqual((report.current_version, report.latest_version, report.notes_pulled), ("1.0.0", "2.0.0", 1))
        self.assertEqual((report.stale_candidates, report.revalidated), (5, 4))
        self.git(self.local, "remote", "set-url", "origin", str(self.base / "missing.git"))
        offline = run_update(self.local, dry_run=True)
        self.assertEqual((offline.latest_version, offline.notes_pulled), ("1.0.0", 0))
        self.assertEqual(self.snapshot(), before)
        self.git(self.local, "remote", "set-url", "origin", str(self.origin))
        self.git(self.seed, "checkout", "--orphan", "rewritten")
        self.put(self.seed, "VERSION", "9.0.0\n")
        self.commit(self.seed)
        self.git(self.seed, "push", "--force", "origin", "HEAD:main")
        with self.assertRaises(RuntimeError):
            run_update(self.local, dry_run=True)
        self.assertEqual(self.snapshot(), before)

    def test_dirty_preserved(self) -> None:
        self.git(self.local, "config", "pull.rebase", "true")
        self.put(self.local, "unrelated.txt", "staged local work\n")
        self.git(self.local, "add", "unrelated.txt")
        self.put(self.local, "unrelated.txt", "unstaged local work\n")
        self.put(self.local, "untracked.txt", "untracked local work\n")
        report = run_update(self.local, dry_run=False)
        self.assertEqual(report.refused_conflicts, [])
        self.assertEqual((self.local / "VERSION").read_text(), "2.0.0\n")
        self.assertEqual((self.local / "unrelated.txt").read_text(), "unstaged local work\n")
        self.assertEqual(self.git(self.local, "show", ":unrelated.txt"), "staged local work")
        self.assertEqual((self.local / "untracked.txt").read_text(), "untracked local work\n")

    def test_conflict_refused(self) -> None:
        self.put(self.local, "services/example.md", self.service("example") + "\nStaged local edit.\n")
        self.git(self.local, "add", "services/example.md")
        self.put(self.local, "services/example.md", self.service("example") + "\nUnstaged local edit.\n")
        before, head = self.snapshot(), self.git(self.local, "rev-parse", "HEAD")
        index = self.git(self.local, "ls-files", "--stage")
        for dry_run in (True, False):
            report = run_update(self.local, dry_run=dry_run)
            self.assertEqual(report.refused_conflicts, ["services/example.md"])
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(self.git(self.local, "rev-parse", "HEAD"), head)
            self.assertEqual(self.git(self.local, "ls-files", "--stage"), index)
            self.assertEqual(report.notes_pulled, 1 if dry_run else 0)

    def test_official_document_refresh_feeds_preflight_without_promoting_freshness(self) -> None:
        from portwright.doc_cache import load_document

        url = "https://docs.example.com/cli"
        procedure = self.service("example").replace(
            "distributable: true\n",
            f"distributable: true\nfreshness_evidence:\n  url: {url}\n",
        )
        self.put(self.seed, "services/example.md", procedure)
        self.commit(self.seed)
        self.git(self.seed, "push", "origin", "main")
        with patch("portwright.doc_cache._fetch", return_value="Current official CLI documentation."):
            report = run_update(self.local, dry_run=False)
        self.assertEqual(report.docs_fetched, ["example"])
        self.assertEqual(report.docs_failed, [])
        cached = load_document(self.local, "example", url)
        self.assertEqual(cached["text"], "Current official CLI documentation.")
        self.assertEqual((self.local / "services/example.md").read_text(), procedure)
        decision = preflight_json("example", "--home", str(self.local), fixtures=None)
        self.assertEqual(decision["freshness"]["state"], "unknown")
        self.assertEqual(decision["freshness"]["action"], "verify-required")
        self.assertEqual(decision["freshness"]["compared"]["source_sha256"], cached["sha256"])
        before = self.snapshot()
        with patch("portwright.doc_cache._fetch", side_effect=AssertionError("dry-run made a request")):
            preview = run_update(self.local, dry_run=True)
        self.assertEqual(preview.docs_planned, ["example"])
        self.assertEqual(preview.docs_fetched, [])
        self.assertEqual(self.snapshot(), before)
        with patch("portwright.doc_cache._fetch", return_value="Changed official CLI documentation."):
            changed = run_update(self.local, dry_run=False)
        self.assertEqual(changed.docs_changed, ["example"])
        self.assertEqual((self.local / "services/example.md").read_text(), procedure)

    def test_symlink_source_root_is_not_revalidated(self) -> None:
        notes = self.local / "services"
        notes.rename(self.local / "detached-services")
        notes.symlink_to(self.local / "detached-services", target_is_directory=True)
        report = run_update(self.local, dry_run=True)
        self.assertEqual(report.stale_candidates, 1)  # only the independent stale Lesson
        self.assertEqual(report.revalidated, 1)
        self.assertEqual(report.docs_planned, [])
        self.assertEqual(report.unclassified, [])


if __name__ == "__main__":
    unittest.main()
