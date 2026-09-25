from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .contracts import ContractCatalog, Issue, SERVICE_ID_RE


SLUG_RE = SERVICE_ID_RE
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
SECRET_PATTERNS = tuple(
    re.compile(item["regex"], re.IGNORECASE if item.get("flags") == "i" else 0)
    for item in json.loads((PACKAGE_ROOT / "install" / "secret-patterns.json").read_text(encoding="utf-8"))["patterns"]
)


@dataclass(frozen=True)
class MemoryResult:
    path: Path
    created: bool = False
    promoted: bool = False
    issues: tuple[Issue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues


class MemoryLifecycle:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.catalog = ContractCatalog(self.root)

    def create_procedure_draft(self, service_id: str, *, today: date | None = None) -> MemoryResult:
        self._validate_identity(service_id, "service id")
        stamp = (today or date.today()).isoformat()
        path = self.root / "services" / "_drafts" / f"{service_id}.md"
        text = f'''---
id: {service_id}
display_name: "<TODO display name>"
version_tag: "<TODO verified version>"
last_verified: "{stamp}"
endpoint:
  type: mcp
  server: "<TODO MCP server, API, OAuth app, or CLI>"
human_steps:
  - "<TODO genuinely human-only step, or 없음>"
agent_can:
  - "<TODO actions the Agent performs itself>"
status: active
distributable: false
---

## 한 줄 요약
<TODO current Procedure summary>

## 정답 절차
1. <TODO>

## 하지 말 것
- <TODO needless delegation to avoid>

## 관련 실패 기록
- 없음
'''
        return self._create_once(path, text)

    def create_lesson_draft(
        self,
        service_id: str,
        slug: str,
        *,
        today: date | None = None,
    ) -> MemoryResult:
        self._validate_identity(service_id, "service id")
        self._validate_identity(slug, "slug")
        stamp = (today or date.today()).isoformat()
        path = self.root / "failures" / "_drafts" / f"{stamp}-{service_id}-{slug}.md"
        text = f'''---
date: "{stamp}"
service: {service_id}
service_version: "<TODO affected version>"
status: active
distributable: false
---

## 증상
<TODO observable Failure>

## 진짜 원인
unconfirmed

## 해결
<TODO confirmed correction>

## 다음에 할 일
<TODO reusable Lesson>
'''
        return self._create_once(path, text)

    def _validate_identity(self, value: str, label: str) -> None:
        if not SLUG_RE.fullmatch(value):
            raise ValueError(f"{label} must be lowercase kebab-case")

    def _create_once(self, path: Path, text: str) -> MemoryResult:
        safety_issue = self._memory_path_issue(path)
        if safety_issue:
            return MemoryResult(path=path, issues=(safety_issue,))
        path.parent.mkdir(parents=True, exist_ok=True)
        safety_issue = self._memory_path_issue(path)
        if safety_issue:
            return MemoryResult(path=path, issues=(safety_issue,))
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            return MemoryResult(path=path, created=False)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        return MemoryResult(path=path, created=True)

    def review(self, draft: Path) -> MemoryResult:
        lexical_draft = draft.absolute()
        if lexical_draft.is_symlink():
            return MemoryResult(lexical_draft, issues=(Issue("path", "symlink drafts are not allowed"),))
        draft = lexical_draft.resolve()
        issues: list[Issue] = []
        try:
            relative = draft.relative_to(self.root)
        except ValueError:
            return MemoryResult(draft, issues=(Issue("path", "draft must be inside the Portwright root"),))
        valid_shape = len(relative.parts) == 3 and relative.parts[0] in {"services", "failures"} and relative.parts[1] == "_drafts"
        if not valid_shape:
            issues.append(Issue("path", "promotion source must live directly under services/_drafts or failures/_drafts"))
            return MemoryResult(draft, issues=tuple(issues))
        if not draft.is_file():
            issues.append(Issue("path", "draft file does not exist"))
            return MemoryResult(draft, issues=tuple(issues))
        note = self.catalog.validate_note(draft, promotion=True)
        issues.extend(note.issues)
        text = draft.read_text(encoding="utf-8")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                issues.append(Issue("secret", "suspected secret material blocks promotion"))
                break
        return MemoryResult(path=draft, issues=tuple(issues))

    def promote(self, draft: Path, *, replace: bool = False) -> MemoryResult:
        review = self.review(draft)
        if not review.ok:
            return review
        draft = review.path
        note = self.catalog.validate_note(draft)
        private = note.data.get("distributable") is not True
        try:
            destination = self.catalog.source_root(note.kind, "private" if private else "tracked") / draft.name
        except ValueError as error:
            return MemoryResult(path=draft, issues=(Issue("destination", str(error)),))
        safety_issue = self._memory_path_issue(destination)
        if safety_issue:
            return MemoryResult(path=draft, issues=(safety_issue,))
        if destination.exists() and not replace:
            return MemoryResult(
                path=draft,
                issues=(Issue("destination", f"{destination.relative_to(self.root)} already exists; use --replace explicitly"),),
            )

        destination.parent.mkdir(parents=True, exist_ok=True)
        backup_text = destination.read_text(encoding="utf-8") if destination.exists() else None
        if destination.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            shutil.copy2(destination, destination.with_name(f"{destination.name}.bak-{stamp}"))
        safety_issue = self._memory_path_issue(destination)
        if safety_issue:
            return MemoryResult(path=draft, issues=(safety_issue,))
        os.replace(draft, destination)
        try:
            if note.kind == "failure":
                self._link_lesson(destination)
        except Exception:
            os.replace(destination, draft)
            if backup_text is not None:
                destination.write_text(backup_text, encoding="utf-8")
            raise
        return MemoryResult(path=destination, promoted=True)

    def _link_lesson(self, lesson: Path) -> None:
        note = self.catalog.validate_note(lesson)
        service_id = note.data.get("service")
        if not isinstance(service_id, str):
            return
        # Private Lessons only link privately; public Lessons prefer the distributed
        # Procedure, falling back to private. Hub downloads are never written.
        sources = ("private",) if note.source == "private" else ("tracked", "private")
        # lookup skips unsafe roots; raise here so promote() rolls back instead.
        for source in sources:
            self.catalog.source_root("service", source)
        target = None
        for source in sources:
            target = self.catalog.lookup("service", service_id, sources=(source,))
            if target is not None:
                break
        if target is None:
            return
        procedure = target.path
        safety_issue = self._memory_path_issue(procedure)
        if safety_issue:
            raise RuntimeError(safety_issue.render())
        link = lesson.relative_to(self.root).as_posix()
        text = procedure.read_text(encoding="utf-8")
        if link in text:
            return
        heading = "## 관련 실패 기록"
        if heading in text:
            lines = text.splitlines()
            heading_index = lines.index(heading)
            insert_at = len(lines)
            for index in range(heading_index + 1, len(lines)):
                if lines[index].startswith("## "):
                    insert_at = index
                    break
            lines.insert(insert_at, f"- {link}")
            updated = "\n".join(lines).rstrip() + "\n"
        else:
            updated = text.rstrip() + f"\n\n{heading}\n- {link}\n"
        self._atomic_write(procedure, updated)

    def _atomic_write(self, path: Path, text: str) -> None:
        safety_issue = self._memory_path_issue(path)
        if safety_issue:
            raise RuntimeError(safety_issue.render())
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(text)
            safety_issue = self._memory_path_issue(path)
            if safety_issue:
                raise RuntimeError(safety_issue.render())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _memory_path_issue(self, path: Path) -> Issue | None:
        if path.is_symlink():
            return Issue("path", "symlink Memory paths are not allowed")
        if not path.parent.resolve().is_relative_to(self.root):
            return Issue("path", "Memory path resolves outside the Portwright root")
        for parent in path.parents:
            if parent == self.root:
                break
            if parent.is_symlink():
                return Issue("path", "symlink Memory directories are not allowed")
        return None
