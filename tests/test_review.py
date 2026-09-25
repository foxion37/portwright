"""Improvement review: counting must drive the findings, and the ledger must stay metadata-only."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from portwright.review import Ledger, collect, prioritize, render, write_report  # noqa: E402

TODAY = date(2026, 9, 22)


def procedure(service: str, *, verified: str = "2026-09-01", body: str = "", evidence: bool = False) -> str:
    lines = [
        "---", f"id: {service}", f'display_name: "{service}"', 'version_tag: "1.0"',
        f'last_verified: "{verified}"', "endpoint:", "  type: cli", f'  server: "{service}"',
        "human_steps:", "  - 없음", "agent_can:", "  - 실행", "status: active", "distributable: false",
    ]
    if evidence:
        lines += ["freshness_evidence:", "  url: https://example.com/doc"]
    lines += ["---", "", "## 한 줄 요약", "요약", "", "## 정답 절차", "1. 실행", "", "## 하지 말 것", "- 금지", "", "## 관련 실패 기록", body or "- 없음", ""]
    return "\n".join(lines)


def lesson(service: str, day: str) -> str:
    return "\n".join([
        "---", f'date: "{day}"', f"service: {service}", 'service_version: "1.0"', "status: active",
        "distributable: false", "---", "", "## 무엇을 시도했나", "시도", "", "## 어떤 에러가 났나", "에러",
        "", "## 진짜 원인", "확인된 원인", "", "## 고친 방법 (다음엔 이대로)", "이렇게", "",
    ])


class ReviewTest(unittest.TestCase):
    def home(self, directory: str) -> Path:
        home = Path(directory)
        (home / "services").mkdir()
        (home / "failures").mkdir()
        return home

    def test_repeated_recent_failures_are_a_procedure_defect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self.home(directory)
            (home / "services" / "acme.md").write_text(procedure("acme"), encoding="utf-8")
            for day in ("2026-09-01", "2026-09-10"):
                (home / "failures" / f"{day}-acme-thing.md").write_text(lesson("acme", day), encoding="utf-8")
            findings = collect(home, days=90, today=TODAY)
        kinds = {(f.kind, f.service) for f in findings}
        self.assertIn(("procedure-defect", "acme"), kinds)

    def test_old_failures_alone_are_not_a_defect(self) -> None:
        """Repeated failures that stopped were fixed; flagging them is noise."""
        with tempfile.TemporaryDirectory() as directory:
            home = self.home(directory)
            (home / "services" / "acme.md").write_text(
                procedure("acme", body="- failures/2026-01-0{}-acme-thing.md".format(1)), encoding="utf-8")
            for day in ("2026-01-01", "2026-01-02", "2026-01-03"):
                (home / "failures" / f"{day}-acme-thing.md").write_text(lesson("acme", day), encoding="utf-8")
            findings = collect(home, days=90, today=TODAY)
        self.assertEqual([f.kind for f in findings if f.kind == "procedure-defect"], [])

    def test_repeated_cache_miss_and_unknown_freshness_come_from_the_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self.home(directory)
            (home / "services" / "acme.md").write_text(procedure("acme"), encoding="utf-8")
            ledger = Ledger(home)
            for _ in range(3):
                ledger.record(service="acme", freshness="unknown", tier="confirm", intent="call", procedure="services/acme.md")
            for _ in range(2):
                ledger.record(service="ghost", freshness="unknown", tier="confirm", intent="call", procedure=None)
            findings = collect(home, days=90, today=TODAY)
        kinds = {(f.kind, f.service) for f in findings}
        self.assertIn(("no-evidence", "acme"), kinds)
        self.assertIn(("cache-gap", "ghost"), kinds)

    def test_declared_evidence_url_silences_the_unknown_finding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self.home(directory)
            (home / "services" / "acme.md").write_text(procedure("acme", evidence=True), encoding="utf-8")
            ledger = Ledger(home)
            for _ in range(4):
                ledger.record(service="acme", freshness="unknown", tier="confirm", intent="call", procedure="services/acme.md")
            findings = collect(home, days=90, today=TODAY)
        self.assertNotIn("no-evidence", [f.kind for f in findings])

    def test_ledger_records_metadata_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self.home(directory)
            ledger = Ledger(home)
            ledger.record(service="acme", profile="p", freshness="fresh", tier="auto", intent="call",
                          procedure="services/acme.md", cwd="/tmp/private-project", token="shh")
            row = json.loads(ledger.path.read_text(encoding="utf-8").strip())
        self.assertEqual(set(row), {"date", "service", "profile", "freshness", "tier", "intent", "procedure"})

    def test_corrupt_ledger_line_is_skipped_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self.home(directory)
            ledger = Ledger(home)
            ledger.record(service="acme", freshness="fresh", tier="auto", intent="call", procedure="services/acme.md")
            with ledger.path.open("a", encoding="utf-8") as handle:
                handle.write("{truncated\n")
            self.assertEqual(len(ledger.entries(90)), 1)

    def test_stale_procedure_needs_actual_use(self) -> None:
        old = (TODAY - timedelta(days=200)).isoformat()
        with tempfile.TemporaryDirectory() as directory:
            home = self.home(directory)
            (home / "services" / "acme.md").write_text(procedure("acme", verified=old), encoding="utf-8")
            unused = collect(home, days=90, today=TODAY)
            Ledger(home).record(service="acme", freshness="unknown", tier="confirm", intent="call", procedure="services/acme.md")
            used = collect(home, days=90, today=TODAY)
        self.assertNotIn("stale", [f.kind for f in unused])
        self.assertIn("stale", [f.kind for f in used])

    def test_jev_absent_keeps_deterministic_order_and_all_findings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self.home(directory)
            (home / "services" / "acme.md").write_text(procedure("acme"), encoding="utf-8")
            for day in ("2026-09-01", "2026-09-10"):
                (home / "failures" / f"{day}-acme-thing.md").write_text(lesson("acme", day), encoding="utf-8")
            findings = collect(home, days=90, today=TODAY)
            ordered, note = prioritize(findings, None)
        self.assertEqual(ordered, findings)
        self.assertIn("deterministic", note)

    def test_report_is_written_under_the_private_local_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self.home(directory)
            text = render([], days=90, ledger_rows=0, order="deterministic order (no JEV)")
            path = write_report(home, text, today=TODAY)
        self.assertEqual(path.relative_to(home.resolve()).parts, ("_local", "reviews", "2026-09-22.md"))


if __name__ == "__main__":
    unittest.main()
