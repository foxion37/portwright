from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable


SERVICE_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ROOT_CAUSE_RE = re.compile(r"진짜 원인|root cause", re.IGNORECASE)
DATE_FIELDS = {"last_verified", "date"}
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
NOTE_KINDS = {"services": "service", "failures": "failure", "profiles": "profile"}
SOURCE_DIRS = {"private": "_private", "tracked": "", "hub": "_hub", "draft": "_drafts"}


def resource_path(root: Path, relative: str) -> Path:
    """Prefer a content-home override, otherwise use the packaged resource."""
    local = root / relative
    return local if local.is_file() else PACKAGE_ROOT / relative


def note_kind(relative: str | Path) -> str | None:
    """Recognize note files even when misplaced, so checks and export cannot miss them."""
    path = Path(relative)
    parts = path.parts
    if path.is_absolute() or ".." in parts or len(parts) < 2:
        return None
    if path.suffix != ".md" or path.name == "_TEMPLATE.md" or parts[0] not in NOTE_KINDS:
        return None
    return NOTE_KINDS[parts[0]]


def note_location(relative: str | Path) -> tuple[str, str] | None:
    """Classify a lexical note path, including paths not yet present on disk."""
    kind = note_kind(relative)
    if kind is None:
        return None
    parts = Path(relative).parts
    if len(parts) == 2:
        return kind, "profile" if kind == "profile" else "tracked"
    if kind == "profile":
        return None
    for source, directory in SOURCE_DIRS.items():
        if directory and parts[1] == directory and not any(part.startswith("_") for part in parts[2:-1]):
            return kind, source
    return None


@dataclass(frozen=True)
class Issue:
    field: str
    message: str
    hint: str = ""

    def render(self) -> str:
        suffix = f"; {self.hint}" if self.hint else ""
        return f"{self.field}: {self.message}{suffix}"


@dataclass
class NoteResult:
    path: Path
    relative_path: str
    kind: str
    data: dict[str, Any] = field(default_factory=dict)
    body: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def source(self) -> str | None:
        location = note_location(self.relative_path)
        return location[1] if location else None

    @property
    def note_id(self) -> str:
        value = self.data.get("id") if self.kind != "failure" else None
        return value if isinstance(value, str) and value else self.path.stem


@dataclass
class ValidationReport:
    root: Path
    results: list[NoteResult]
    warnings: list[str] = field(default_factory=list)

    @property
    def failures(self) -> list[NoteResult]:
        return [result for result in self.results if not result.ok]

    @property
    def passed(self) -> int:
        return len(self.results) - len(self.failures)

    @property
    def ok(self) -> bool:
        return not self.failures


def _strip_inline_comment(value: str) -> str:
    in_single = False
    in_double = False
    for index, char in enumerate(value):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            return value[:index]
    return value


def _clean_scalar(value: str) -> str:
    value = _strip_inline_comment(value).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return value.strip()


def split_frontmatter(text: str) -> tuple[list[str] | None, list[str]]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, lines
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return lines[1:index], lines[index + 1 :]
    return None, lines


def _scalar(value: str) -> Any:
    return {"true": True, "false": False}.get(value, value)


def parse_frontmatter(lines: Iterable[str]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    current: str | None = None
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        if indent == 0:
            if ":" not in stripped:
                current = None
                continue
            key, _, raw_value = stripped.partition(":")
            key = key.strip()
            value = _clean_scalar(raw_value)
            current = key
            if value:
                data[key] = _scalar(value)
                current = None
            else:
                data[key] = [] if key != "endpoint" else {}
        elif current and stripped.startswith("- "):
            if not isinstance(data.get(current), list):
                data[current] = []
            data[current].append(_clean_scalar(stripped[2:]))
        elif current and ":" in stripped:
            if not isinstance(data.get(current), dict):
                data[current] = {}
            key, _, raw_value = stripped.partition(":")
            data[current][key.strip()] = _scalar(_clean_scalar(raw_value))
    return data


def _valid_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))


def _root_cause(body: list[str]) -> str | None:
    for index, line in enumerate(body):
        if not line.lstrip().startswith("#") or not ROOT_CAUSE_RE.search(line):
            continue
        values: list[str] = []
        for candidate in body[index + 1 :]:
            if candidate.lstrip().startswith("#"):
                break
            if candidate.strip():
                values.append(candidate.strip())
        return "\n".join(values) if values else ""
    return None


class ContractCatalog:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self._schemas = {
            "service": self._load_schema("service"),
            "failure": self._load_schema("failure"),
            "profile": self._load_schema("profile"),
        }

    def _load_schema(self, kind: str) -> dict[str, Any]:
        return json.loads(resource_path(self.root, f"install/schema/{kind}.schema.json").read_text(encoding="utf-8"))

    def _kind(self, path: Path) -> str:
        relative = path.absolute().relative_to(self.root)
        if relative.parts[0] == "services":
            return "service"
        if relative.parts[0] == "failures":
            return "failure"
        if relative.parts[0] == "profiles":
            return "profile"
        raise ValueError(f"note must live under services/, failures/, or profiles/: {path}")

    def source_root(self, kind: str, source: str) -> Path:
        directory = next((name for name, value in NOTE_KINDS.items() if value == kind), None)
        if directory is None or (kind == "profile" and source != "profile"):
            raise ValueError("unknown note kind or source")
        if kind != "profile" and source not in SOURCE_DIRS:
            raise ValueError("unknown note source")
        path = self.root / directory
        if kind != "profile":
            path /= SOURCE_DIRS[source]
        self._safe_components(path)
        return path

    def _safe_components(self, path: Path) -> None:
        try:
            parts = path.relative_to(self.root).parts
        except ValueError:
            raise ValueError("note path must be inside the Portwright root") from None
        if ".." in parts:
            raise ValueError("note path must not contain traversal")
        current = self.root
        for part in parts:
            current /= part
            if current.is_symlink():
                raise ValueError("symlink note directories or files are not allowed")
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("note path resolves outside the Portwright root")

    def note_path(self, raw: str, *, include_drafts: bool = False) -> Path:
        location = note_location(raw)
        if location is None or (location[1] == "draft" and not include_drafts):
            raise ValueError("path must be a relative catalog .md note")
        path = self.root / raw
        self._safe_components(path)
        if not path.is_file():
            raise ValueError("note not found")
        return path

    def _roots(self, include_drafts: bool) -> Iterable[tuple[str, str, Path]]:
        for directory, kind in NOTE_KINDS.items():
            sources = {"profile": ""} if kind == "profile" else SOURCE_DIRS
            for source, suffix in sources.items():
                if source != "draft" or include_drafts:
                    yield kind, source, self.root / directory / suffix

    def directory_issues(self, *, include_drafts: bool = True) -> list[NoteResult]:
        results: list[NoteResult] = []
        for kind, source, path in self._roots(include_drafts):
            try:
                self.source_root(kind, source)
            except ValueError as error:
                results.append(NoteResult(path, path.relative_to(self.root).as_posix(), kind,
                                          issues=[Issue("directory", str(error))]))
        return results

    def iter_notes(self, *, include_drafts: bool = True) -> list[Path]:
        paths: list[Path] = []
        for kind, source, directory in self._roots(include_drafts):
            try:
                self.source_root(kind, source)
            except ValueError:
                continue
            if not directory.is_dir():
                continue
            for parent, dirs, files in os.walk(directory, followlinks=False):
                dirs[:] = sorted(name for name in dirs
                                 if source not in {"tracked", "profile"}
                                 and not name.startswith("_") and not (Path(parent) / name).is_symlink())
                for name in files:
                    path = Path(parent) / name
                    if note_location(path.relative_to(self.root)):
                        paths.append(path)
        return sorted(paths)

    @staticmethod
    def _identities(results: list[NoteResult]) -> dict[tuple[str, str], list[NoteResult]]:
        groups: dict[tuple[str, str], list[NoteResult]] = {}
        for note in results:
            if note.source in {"private", "tracked", "hub", "profile"}:
                groups.setdefault((note.kind, note.note_id), []).append(note)
        for group in groups.values():
            by_source: dict[str, list[NoteResult]] = {}
            for note in group:
                by_source.setdefault(note.source, []).append(note)
            for source, duplicates in by_source.items():
                if len(duplicates) > 1:
                    for note in duplicates:
                        note.issues.append(Issue("duplicate_id", f"{note.note_id} occurs more than once in {source}"))
        return groups

    def notes(self, kind: str | None = None, *, sources: tuple[str, ...] | None = None) -> list[NoteResult]:
        """Filter sources, then resolve each kind/id by private > tracked > hub.

        Invalid winners remain visible; status/validity never selects a lower copy
        of that identity. Unsafe roots are omitted (see directory_issues), drafts
        are check-only, and location/flag consistency is a workspace check.
        """
        results = []
        for path in self.iter_notes(include_drafts=False):
            note_kind, source = note_location(path.relative_to(self.root))
            if (kind is None or kind == note_kind) and (sources is None or source in sources):
                results.append(self.validate_note(path))
        groups = self._identities(results)
        priority = {"private": 0, "tracked": 1, "hub": 2, "profile": 0}
        return [min(group, key=lambda note: (priority[note.source], note.relative_path))
                for _, group in sorted(groups.items())]

    def lookup(self, kind: str, note_id: str, *, sources: tuple[str, ...] | None = None) -> NoteResult | None:
        return next((note for note in self.notes(kind, sources=sources) if note.note_id == note_id), None)

    def validate_note(
        self,
        path: Path,
        *,
        strict: bool = False,
        promotion: bool = False,
    ) -> NoteResult:
        lexical_path = path.absolute()
        kind = self._kind(lexical_path)
        relative = lexical_path.relative_to(self.root).as_posix()
        if not lexical_path.parent.resolve().is_relative_to(self.root):
            return NoteResult(
                path=lexical_path,
                relative_path=relative,
                kind=kind,
                issues=[Issue("file", "note parent resolves outside the Portwright root")],
            )
        if lexical_path.is_symlink():
            return NoteResult(
                path=lexical_path,
                relative_path=relative,
                kind=kind,
                issues=[Issue("file", "symlink notes are not allowed")],
            )
        path = lexical_path.resolve()
        result = NoteResult(path=path, relative_path=relative, kind=kind)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            result.issues.append(Issue("file", f"cannot read UTF-8 text ({error})"))
            return result

        frontmatter, body = split_frontmatter(text)
        result.body = body
        if frontmatter is None:
            result.issues.append(Issue("frontmatter", "missing block between the first two --- lines"))
            return result
        result.data = parse_frontmatter(frontmatter)
        schema = self._schemas[kind]
        self._validate_schema(result, schema)

        status = result.data.get("status")
        if strict and status == "stale":
            result.issues.append(Issue("status", "stale note is rejected in strict mode"))

        if kind == "service":
            self._validate_service(result)
        elif kind == "failure":
            self._validate_failure(result, promotion=promotion)
        else:
            self._validate_profile(result)

        if promotion:
            self._validate_placeholders(result)
        return result

    def _validate_schema(self, result: NoteResult, schema: dict[str, Any]) -> None:
        data = result.data
        for key in schema.get("required", []):
            if key not in data:
                result.issues.append(Issue(key, "missing required field"))
        for key, definition in schema.get("properties", {}).items():
            if key not in data:
                continue
            value = data[key]
            # A bare `key:` parses as None or [];
            # for a non-list field that means the optional key was left blank.
            blank = value is None or (value == [] and definition.get("type") != "array")
            if blank and key not in schema.get("required", []):
                continue
            expected = definition.get("type")
            if expected == "string" and not isinstance(value, str):
                result.issues.append(Issue(key, "must be a string"))
                continue
            if expected == "boolean" and not isinstance(value, bool):
                result.issues.append(Issue(key, "must be true or false"))
                continue
            if expected == "array" and not isinstance(value, list):
                result.issues.append(Issue(key, "must be a YAML list"))
                continue
            if expected == "object" and not isinstance(value, dict):
                result.issues.append(Issue(key, "must be a mapping"))
                continue
            if "enum" in definition and value not in definition["enum"]:
                allowed = ", ".join(definition["enum"])
                result.issues.append(Issue(key, f"{value!r} is not allowed", f"allowed: {allowed}"))
            if expected == "array" and isinstance(value, list):
                if not value:
                    result.issues.append(Issue(key, "must contain at least one item"))
                elif any(not isinstance(item, str) or not item for item in value):
                    result.issues.append(Issue(key, "every item must be a non-empty string"))
            if expected == "object" and isinstance(value, dict):
                for nested in definition.get("required", []):
                    if not value.get(nested):
                        result.issues.append(Issue(f"{key}.{nested}", "missing required field"))
                for nested, nested_definition in definition.get("properties", {}).items():
                    nested_value = value.get(nested)
                    if nested_value is not None and "enum" in nested_definition:
                        if nested_value not in nested_definition["enum"]:
                            allowed = ", ".join(nested_definition["enum"])
                            result.issues.append(
                                Issue(f"{key}.{nested}", f"{nested_value!r} is not allowed", f"allowed: {allowed}")
                            )
        for field_name in DATE_FIELDS:
            if field_name in data and not _valid_date(data[field_name]):
                result.issues.append(Issue(field_name, "must be a real YYYY-MM-DD date"))

    def _validate_service(self, result: NoteResult) -> None:
        service_id = result.data.get("id")
        if isinstance(service_id, str):
            if not SERVICE_ID_RE.fullmatch(service_id):
                result.issues.append(Issue("id", "must be lowercase kebab-case"))
            if result.path.stem != service_id:
                result.issues.append(Issue("filename", f"must be {service_id}.md to match id"))

    def _validate_profile(self, result: NoteResult) -> None:
        profile_id = result.data.get("id")
        if isinstance(profile_id, str):
            if not SERVICE_ID_RE.fullmatch(profile_id):
                result.issues.append(Issue("id", "must be lowercase kebab-case"))
            if result.path.stem != profile_id:
                result.issues.append(Issue("filename", f"must be {profile_id}.md to match id"))
        project_path = result.data.get("project_path")
        if isinstance(project_path, str) and not project_path.startswith(("/", "~")):
            result.issues.append(Issue("project_path", "must be an absolute path"))
        for item in result.data.get("databases", []) or []:
            if isinstance(item, str) and ":" not in item:
                result.issues.append(Issue("databases", f"{item!r} must be kind:name"))

    def _validate_failure(self, result: NoteResult, *, promotion: bool) -> None:
        failure_date = result.data.get("date")
        service = result.data.get("service")
        if isinstance(service, str) and not SERVICE_ID_RE.fullmatch(service):
            result.issues.append(Issue("service", "must be lowercase kebab-case"))
        if isinstance(failure_date, str) and isinstance(service, str):
            prefix = f"{failure_date}-{service}-"
            if not result.path.name.startswith(prefix) or result.path.name == f"{prefix}.md":
                result.issues.append(
                    Issue("filename", f"must match YYYY-MM-DD-<service>-<slug>.md and start with {prefix}")
                )
        root_cause = _root_cause(result.body)
        if root_cause is None:
            result.issues.append(Issue("root_cause", "missing '진짜 원인' or 'root cause' heading"))
        elif not root_cause:
            result.issues.append(Issue("root_cause", "section must not be empty"))
        elif promotion and "unconfirmed" in root_cause.lower():
            result.issues.append(Issue("root_cause", "unconfirmed draft cannot be promoted"))

    def _validate_placeholders(self, result: NoteResult) -> None:
        text = result.path.read_text(encoding="utf-8")
        markers = ("<TODO", "<replace", "TODO:", "[REDACT THIS")
        for marker in markers:
            if marker.lower() in text.lower():
                result.issues.append(Issue("draft", f"unresolved placeholder {marker!r}"))

    def validate_workspace(self, *, strict: bool = False, include_drafts: bool = True) -> ValidationReport:
        results = [self.validate_note(path, strict=strict)
                   for path in self.iter_notes(include_drafts=include_drafts)]
        groups = self._identities(results)
        warnings = []
        for (kind, note_id), group in sorted(groups.items()):
            if len({note.source for note in group}) > 1:
                paths = ", ".join(note.relative_path for note in group)
                warnings.append(f"{kind} {note_id}: cross-source override (private > tracked > hub): {paths}")
        for note in results:
            if note.source == "private" and note.data.get("distributable") is True:
                note.issues.append(Issue("distributable", "private notes must not be distributable"))
            elif note.source == "tracked" and note.data.get("distributable") is not True:
                note.issues.append(Issue("distributable", "tracked notes must declare true"))
        results.extend(self.directory_issues(include_drafts=include_drafts))
        for kind in NOTE_KINDS.values():
            try:
                base = self.source_root(kind, "profile" if kind == "profile" else "tracked")
            except ValueError:
                continue
            for parent, dirs, files in os.walk(base, followlinks=False):
                dirs[:] = sorted(name for name in dirs
                                 if not name.startswith("_") and not (Path(parent) / name).is_symlink())
                if Path(parent) == base:
                    continue
                for name in sorted(files):
                    path = Path(parent) / name
                    relative = path.relative_to(self.root).as_posix()
                    if note_kind(relative):
                        results.append(NoteResult(path, relative, kind, issues=[
                            Issue("location", "notes outside named sources must be top-level files"),
                        ]))
        return ValidationReport(root=self.root, results=results, warnings=warnings)


def is_distributable(note: NoteResult) -> bool:
    """The publication boundary: valid, explicitly public notes in the tracked source only."""
    return note.source == "tracked" and note.ok and note.data.get("distributable") is True


def distributable_notes(root: Path, paths: Iterable[str | Path]) -> list[NoteResult]:
    """Select publication notes from Git's path list without reading private, Hub or draft content."""
    catalog = ContractCatalog(root)
    selected = []
    relatives: set[str] = set()
    for raw in paths:
        path = Path(raw)
        if path.is_absolute():
            path = path.relative_to(catalog.root)
        relatives.add(path.as_posix())
    for relative in sorted(relatives):
        location = note_location(relative)
        if location is None or location[1] != "tracked":
            continue
        try:
            catalog.note_path(relative)
        except ValueError as error:
            raise ValueError(f"{relative}: {error}") from None
        note = catalog.validate_note(catalog.root / relative)
        if note.data.get("distributable") is True and not note.ok:
            issues = "; ".join(issue.render() for issue in note.issues)
            raise ValueError(f"refusing invalid distributable note: {relative} ({issues})")
        if is_distributable(note):
            selected.append(note)
    return selected


def render_report(report: ValidationReport) -> str:
    lines = [f"portwright check — home: {report.root}"]
    if not report.results:
        lines.append("  (no notes found under services/ or failures/)")
    width = max((len(result.relative_path) for result in report.results), default=0)
    for result in report.results:
        tag = "PASS" if result.ok else "FAIL"
        line = f"  [{tag}] {result.relative_path.ljust(width)}"
        if result.issues:
            line += "  — " + "; ".join(issue.render() for issue in result.issues)
        lines.append(line)
    lines.extend(f"  [WARN] {warning}" for warning in report.warnings)
    if report.warnings:
        lines.append(f"  {len(report.warnings)} warnings")
    lines.append(f"  {report.passed} passed, {len(report.failures)} failed")
    return "\n".join(lines)
