from __future__ import annotations

import sys
from datetime import date
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from portwright.contracts import ContractCatalog
from portwright.memory import MemoryLifecycle
from portwright.review import collect
from tests._v2 import preflight_json, run_cli, write_service
from tests.test_deep_modules import valid_lesson
from tests.test_mcp import _call, _roundtrip


def service(root: Path, source: str, *, name: str = "acme") -> Path:
    path = write_service(root, name, private=source == "private",
                         extra=f"distributable: {'true' if source == 'tracked' else 'false'}")
    if source in {"hub", "draft"}:
        destination = root / "services" / ("_hub" if source == "hub" else "_drafts") / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        path.rename(destination)
        return destination
    return path


def lesson(root: Path, source: str, *, subdir: str = "") -> Path:
    name, text = valid_lesson("acme")
    directory = root / "failures" / {"tracked": "", "private": "_private", "hub": "_hub"}[source] / subdir
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    text = text.replace("status: active", f"status: active\ndistributable: {'true' if source == 'tracked' else 'false'}")
    path.write_text(text, encoding="utf-8")
    return path


class CatalogTests(unittest.TestCase):
    def test_duplicate_procedure_id_fails_check_even_when_each_note_is_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            first = service(root, "private")
            second = first.parent / "nested" / first.name
            second.parent.mkdir()
            second.write_bytes(first.read_bytes())
            catalog = ContractCatalog(root)
            self.assertTrue(catalog.validate_note(first).ok)
            self.assertTrue(catalog.validate_note(second).ok)
            run = run_cli("check", "--home", directory)
            self.assertEqual(run.returncode, 1, run.stdout)
            self.assertIn("[FAIL]", run.stdout)
            self.assertTrue(any(issue.field == "duplicate_id" for note in catalog.validate_workspace().failures for issue in note.issues))

    def test_private_overrides_tracked_and_hub_with_nonfatal_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            service(root, "hub")
            service(root, "tracked")
            private = service(root, "private")
            run = run_cli("check", "--home", directory)
            self.assertEqual(run.returncode, 0, run.stdout)
            self.assertIn("[WARN]", run.stdout)
            self.assertTrue(run.stdout.rstrip().endswith("0 failed"))
            decision = preflight_json("acme", "--home", directory)
            self.assertEqual(decision["procedure"], private.relative_to(root).as_posix())

    def test_hub_only_procedure_and_lesson_are_available(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            procedure = service(root, "hub")
            failure = lesson(root, "hub")
            decision = preflight_json("acme", "--home", directory)
            self.assertEqual(decision["procedure"], procedure.relative_to(root).as_posix())
            self.assertEqual(decision["active_lessons"], [failure.relative_to(root).as_posix()])

    def test_duplicate_lesson_id_fails_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            lesson(root, "hub", subdir="one")
            lesson(root, "hub", subdir="two")
            run = run_cli("check", "--home", directory)
            self.assertEqual(run.returncode, 1, run.stdout)
            report = ContractCatalog(root).validate_workspace()
            self.assertTrue(any(issue.field == "duplicate_id" for note in report.failures for issue in note.issues))

    def test_lesson_override_resolves_before_status_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            service(root, "tracked")
            lesson(root, "hub")
            lesson(root, "tracked")
            private = lesson(root, "private")
            private.write_text(private.read_text().replace("status: active", "status: stale"))
            decision = preflight_json("acme", "--home", directory)
            self.assertEqual(decision["active_lessons"], [])
            self.assertEqual(decision["stale_lessons"], [private.relative_to(root).as_posix()])

    def test_unsafe_source_roots_fail_check_and_never_supply_notes(self):
        for source in ("services", "services/_private", "services/_hub", "failures/_hub"):
            for inside in (False, True):
                with self.subTest(source=source, inside=inside), tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
                    root = Path(directory).resolve()
                    target = root / "elsewhere" if inside else Path(outside)
                    target.mkdir(exist_ok=True)
                    (target / "acme.md").write_text("must not be read")
                    link = root / source
                    link.parent.mkdir(parents=True, exist_ok=True)
                    link.symlink_to(target, target_is_directory=True)
                    run = run_cli("check", "--home", directory)
                    self.assertEqual(run.returncode, 1, run.stdout)
                    decision = preflight_json("acme", "--home", directory)
                    self.assertIsNone(decision["procedure"])
                    self.assertEqual(decision["state"], "cache-invalid")

    def test_symlink_note_is_rejected_without_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            tracked = service(root, "tracked")
            link = root / "services" / "_private" / tracked.name
            link.parent.mkdir()
            link.symlink_to(tracked)
            decision = preflight_json("acme", "--home", directory)
            self.assertEqual(decision["state"], "cache-invalid")
            self.assertEqual(decision["invalid_entries"], [link.relative_to(root).as_posix()])

    def test_drafts_are_checked_but_not_lookup_or_mcp_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            draft = service(root, "draft")
            self.assertEqual(preflight_json("acme", "--home", directory)["state"], "derive-required")
            replies = _roundtrip(root, _call("get_note", {"path": draft.relative_to(root).as_posix()}))
            self.assertTrue(replies[0]["result"]["isError"])
            self.assertIn(draft.resolve(), ContractCatalog(root).iter_notes())

    def test_nested_notes_fail_check_but_are_not_lookup_sources(self):
        for directory_name in ("services", "failures", "profiles"):
            with self.subTest(directory=directory_name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                nested = root / directory_name / "archive" / "nested" / "acme.md"
                nested.parent.mkdir(parents=True)
                nested.write_text("misplaced note", encoding="utf-8")
                (nested.parent / "_TEMPLATE.md").write_text("template", encoding="utf-8")
                catalog = ContractCatalog(root)
                self.assertEqual(catalog.iter_notes(), [])
                self.assertEqual(preflight_json("acme", "--home", directory)["state"], "derive-required")
                report = catalog.validate_workspace()
                self.assertEqual([note.path for note in report.failures], [nested])
                self.assertEqual([issue.field for issue in report.failures[0].issues], ["location"])
                self.assertEqual(run_cli("check", "--home", directory).returncode, 1)

    def test_tracked_overrides_hub_by_frontmatter_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            service(root, "hub")
            tracked = service(root, "tracked")
            catalog = ContractCatalog(root)
            self.assertEqual(catalog.lookup("service", "acme").path, tracked)
            duplicate = tracked.with_name("different-filename.md")
            duplicate.write_bytes(tracked.read_bytes())
            report = catalog.validate_workspace()
            self.assertTrue(any(issue.field == "duplicate_id" for note in report.failures for issue in note.issues))
            self.assertFalse(catalog.lookup("service", "acme").ok)

    def test_procedure_and_lesson_ids_have_separate_namespaces(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            failure = lesson(root, "tracked")
            procedure = service(root, "tracked", name=failure.stem)
            catalog = ContractCatalog(root)
            self.assertTrue(catalog.validate_workspace().ok)
            self.assertEqual(catalog.lookup("service", failure.stem).path, procedure)
            self.assertEqual(catalog.lookup("failure", failure.stem).path, failure)

    def test_review_counts_only_the_resolved_lesson(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            service(root, "tracked")
            service(root, "private")
            for source in ("hub", "tracked", "private"):
                lesson(root, source)
            findings = collect(root, today=date(2026, 9, 25))
            self.assertEqual([finding.kind for finding in findings], ["unlinked-lesson"])
            self.assertEqual(findings[0].facts["unlinked"], ["2026-07-13-acme-known-fix.md"])

    def test_public_promotion_links_tracked_without_editing_private_or_hub(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            hub = service(root, "hub")
            tracked = service(root, "tracked")
            private = service(root, "private")
            hub_before, private_before = hub.read_bytes(), private.read_bytes()
            incoming = lesson(root, "tracked")
            draft = root / "failures" / "_drafts" / incoming.name
            draft.parent.mkdir()
            incoming.rename(draft)
            result = MemoryLifecycle(root).promote(draft)
            self.assertTrue(result.promoted, result.issues)
            self.assertIn(incoming.relative_to(root).as_posix(), tracked.read_text())
            self.assertEqual(hub.read_bytes(), hub_before)
            self.assertEqual(private.read_bytes(), private_before)

    def test_public_promotion_links_private_when_tracked_is_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            private = service(root, "private")
            incoming = lesson(root, "tracked")
            draft = root / "failures" / "_drafts" / incoming.name
            draft.parent.mkdir()
            incoming.rename(draft)
            result = MemoryLifecycle(root).promote(draft)
            self.assertTrue(result.promoted, result.issues)
            self.assertIn(incoming.relative_to(root).as_posix(), private.read_text())

    def test_promotion_requires_explicit_true_for_public_destination(self):
        for kind in ("service", "failure"):
            for flag in ("true", "false", None):
                with self.subTest(kind=kind, flag=flag), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory).resolve()
                    incoming = service(root, "tracked") if kind == "service" else lesson(root, "tracked")
                    draft = incoming.parent / "_drafts" / incoming.name
                    draft.parent.mkdir()
                    text = incoming.read_text()
                    text = text.replace("distributable: true\n", "" if flag is None else f"distributable: {flag}\n")
                    draft.write_text(text, encoding="utf-8")
                    incoming.unlink()
                    result = MemoryLifecycle(root).promote(draft)
                    expected = incoming if flag == "true" else incoming.parent / "_private" / incoming.name
                    self.assertTrue(result.promoted, result.issues)
                    self.assertEqual(result.path, expected)
                    self.assertEqual(expected.read_text(), text)
                    self.assertFalse(draft.exists())
                    if flag != "true":
                        self.assertFalse(incoming.exists())
                    self.assertTrue(ContractCatalog(root).validate_workspace().ok)

    def test_created_drafts_explicitly_opt_out_of_distribution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            lifecycle = MemoryLifecycle(root)
            drafts = (lifecycle.create_procedure_draft("acme"),
                      lifecycle.create_lesson_draft("acme", "known-fix"))
            for draft in drafts:
                self.assertIs(lifecycle.catalog.validate_note(draft.path).data.get("distributable"), False)


if __name__ == "__main__":
    unittest.main()
