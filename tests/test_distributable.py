"""Distributable boundary: one shared selector for export and skill bundles, and the
flag<->location check that keeps private content out of publication."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "lib"))

from portwright.contracts import ContractCatalog, distributable_notes  # noqa: E402
from tests._v2 import run_cli, write_service  # noqa: E402
from tests.test_deep_modules import valid_lesson  # noqa: E402

try:  # the public export repo does not ship the exporter itself
    import export_public
except ImportError:
    export_public = None

try:
    import build_skill_bundles as builder
except ImportError:
    builder = None

PACKAGED = ("portwright-tool-use", "portwright-tool-memory")
EXPORT_CONFIG = {
    "include_prefixes": ["services/", "failures/"],
    "include_files": [],
    "exclude": [],
    "binary_files": [],
    "renames": {},
    "personal_markers": [],
    "substitutions": [],
}


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)



def write_note(repo: Path, relative: str, text: str) -> Path:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def service(repo: Path, source: str, name: str, *, distributable: bool) -> Path:
    path = write_service(repo, name, private=source == "private",
                         extra=f"distributable: {'true' if distributable else 'false'}")
    if source in {"hub", "draft"}:
        destination = repo / "services" / ("_hub" if source == "hub" else "_drafts") / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        path.rename(destination)
        return destination
    return path


def lesson(repo: Path, source: str, service_id: str, slug: str, *, distributable: bool) -> Path:
    name, text = valid_lesson(service_id, slug)
    text = text.replace("status: active", f"status: active\ndistributable: {'true' if distributable else 'false'}")
    directory = {"tracked": "", "private": "_private", "hub": "_hub", "draft": "_drafts"}[source]
    return write_note(repo, f"failures/{directory + '/' if directory else ''}{name}", text)


def skill_md(name: str) -> str:
    return f"---\nname: {name}\ndescription: synthetic test skill\n---\n\n# {name}\n"


class RepoCase(unittest.TestCase):
    """Synthetic git repo: the selector reads Git's path list, so notes must be committed."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = (Path(self.tmp.name) / "repo").resolve()
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "config", "user.name", "Test")
        for skill in PACKAGED:
            write_note(self.repo, f"skills/{skill}/SKILL.md", skill_md(skill))
        self.out = (Path(self.tmp.name) / "out").resolve()
        for module in (export_public, builder):
            if module is not None:
                old = module.ROOT
                module.ROOT = self.repo
                self.addCleanup(setattr, module, "ROOT", old)

    def commit(self) -> None:
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "test")

    def tracked(self, *prefixes: str) -> list[str]:
        out = subprocess.run(
            ["git", "-C", str(self.repo), "ls-files", "-z", "--", *prefixes],
            capture_output=True, text=True, check=True,
        ).stdout
        return sorted(p for p in out.split("\0") if p)


class SelectionParityTests(RepoCase):
    def populate(self) -> None:
        service(self.repo, "tracked", "acme", distributable=True)
        service(self.repo, "tracked", "internal", distributable=False)
        service(self.repo, "private", "hidden", distributable=True)
        service(self.repo, "hub", "upstream", distributable=True)
        service(self.repo, "draft", "wip", distributable=True)
        lesson(self.repo, "tracked", "acme", "known-fix", distributable=True)
        lesson(self.repo, "tracked", "acme", "internal-note", distributable=False)
        lesson(self.repo, "hub", "acme", "upstream-fix", distributable=True)
        nested = service(self.repo, "tracked", "archived", distributable=False)
        target = self.repo / "services" / "archive" / nested.name
        target.parent.mkdir(parents=True, exist_ok=True)
        nested.rename(target)
        write_note(self.repo, "failures/notes/scratch.md", "scratch notes without frontmatter\n")
        write_note(self.repo, "services/not_TEMPLATE.md", "not the catalog template\n")
        self.commit()

    def expected(self) -> set[str]:
        return {note.relative_path for note in distributable_notes(self.repo, self.tracked())}

    def test_shared_selector_picks_only_valid_flagged_tracked_notes(self) -> None:
        self.populate()
        self.assertEqual(self.expected(), {"services/acme.md", "failures/2026-07-13-acme-known-fix.md"})

    @unittest.skipIf(export_public is None, "export_public is not part of the public export repo")
    def test_export_and_bundle_select_the_same_notes(self) -> None:
        self.populate()
        report = export_public.export(self.out / "public", EXPORT_CONFIG, dry_run=True)
        inventory = builder.build(self.out / "bundles")

        expected = self.expected()
        self.assertEqual({p for p in report["files"] if p.startswith(("services/", "failures/"))}, expected)
        self.assertEqual(set(report["skipped_not_distributable"]),
                         {"services/internal.md", "failures/2026-07-13-acme-internal-note.md",
                          "services/archive/archived.md", "failures/notes/scratch.md",
                          "services/not_TEMPLATE.md"})
        self.assertEqual(set(report["skipped_private_or_draft"]),
                         {"services/_private/hidden.md", "services/_hub/upstream.md", "services/_drafts/wip.md",
                          "failures/_hub/2026-07-13-acme-upstream-fix.md"})

        self.assertEqual(set(inventory["skipped_not_distributable"]), set(report["skipped_not_distributable"]))
        self.assertEqual(set(inventory["skipped_private_or_draft"]), set(report["skipped_private_or_draft"]))
        self.assertEqual(set(inventory["skills"]) - set(PACKAGED), {"acme"})
        bundled = (self.out / "bundles" / "acme" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("2026-07-13-acme-known-fix.md", bundled)
        self.assertNotIn("internal-note", bundled)
        self.assertFalse((self.out / "bundles" / "internal").exists())
        self.assertFalse((self.out / "bundles" / "hidden").exists())
        self.assertFalse((self.out / "bundles" / "upstream").exists())

    @unittest.skipIf(builder is None, "build_skill_bundles is not importable")
    def test_invalid_flagged_note_aborts_the_build(self) -> None:
        service(self.repo, "tracked", "broken", distributable=True)
        path = self.repo / "services" / "broken.md"
        path.write_text(path.read_text().replace("status: active", "status: bogus"), encoding="utf-8")
        self.commit()
        with self.assertRaises(SystemExit):
            builder.build(self.out)

    def test_missing_git_tracked_note_reports_its_path(self) -> None:
        path = service(self.repo, "tracked", "acme", distributable=True)
        self.commit()
        path.unlink()
        with self.assertRaises(ValueError) as caught:
            distributable_notes(self.repo, self.tracked())
        self.assertIn("services/acme.md", str(caught.exception))


class SelectorTests(unittest.TestCase):
    def test_selector_rejects_invalid_flagged_note(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = write_service(root, "broken", extra="distributable: true")
            path.write_text(path.read_text().replace("status: active", "status: bogus"), encoding="utf-8")
            with self.assertRaises(ValueError):
                distributable_notes(root, ["services/broken.md"])

    def test_selector_ignores_nontracked_sources_without_reading_them(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            write_service(root, "acme", extra="distributable: true")
            # Malformed bodies in non-tracked sources must not even be read.
            for relative in ("services/_private/broken.md", "services/_hub/broken.md",
                             "services/_drafts/broken.md", "profiles/broken.md"):
                write_note(root, relative, "not a note at all")
            paths = ["services/acme.md", "services/_private/broken.md", "services/_hub/broken.md",
                     "services/_drafts/broken.md", "profiles/broken.md", "README.md"]
            self.assertEqual([note.relative_path for note in distributable_notes(root, paths)],
                             ["services/acme.md"])

    def test_selector_accepts_absolute_paths_inside_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = write_service(root, "acme", extra="distributable: true")
            self.assertEqual([note.relative_path for note in distributable_notes(root, [path])],
                             ["services/acme.md"])


class FlagLocationCheckTests(unittest.TestCase):
    def check(self, home: Path) -> subprocess.CompletedProcess[str]:
        return run_cli("check", "--home", str(home))

    def test_private_flagged_true_fails_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            write_service(home, "hidden", private=True, extra="distributable: true")
            run = self.check(home)
            failures = ContractCatalog(home).validate_workspace().failures
            self.assertEqual([note.relative_path for note in failures], ["services/_private/hidden.md"])
            self.assertEqual([issue.field for issue in failures[0].issues], ["distributable"])
        self.assertEqual(run.returncode, 1)

    def test_tracked_false_or_missing_flag_fails_check(self) -> None:
        for extra in ("distributable: false", ""):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve()
                write_service(home, "acme", extra=extra)
                run = self.check(home)
                failures = ContractCatalog(home).validate_workspace().failures
                self.assertEqual([note.relative_path for note in failures], ["services/acme.md"])
                self.assertEqual([issue.field for issue in failures[0].issues], ["distributable"])
            self.assertEqual(run.returncode, 1)

    def test_hub_and_drafts_are_exempt_from_flag_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            write_service(home, "acme", extra="distributable: true")
            service(home, "hub", "upstream", distributable=False)
            service(home, "draft", "wip", distributable=True)
            lesson(home, "hub", "acme", "upstream-fix", distributable=False)
            lesson(home, "draft", "acme", "wip-fix", distributable=True)
            run = self.check(home)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("0 failed", run.stdout)


if __name__ == "__main__":
    unittest.main()
