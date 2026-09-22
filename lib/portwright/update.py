from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .contracts import ContractCatalog
from .doc_cache import refresh_document


@dataclass
class UpdateReport:
    current_version: str
    latest_version: str
    notes_pulled: int
    stale_candidates: int
    revalidated: int
    private_excluded: list[str]
    unclassified: list[str]
    refused_conflicts: list[str]
    dry_run: bool
    docs_planned: list[str] = field(default_factory=list)
    docs_fetched: list[str] = field(default_factory=list)
    docs_changed: list[str] = field(default_factory=list)
    docs_failed: list[str] = field(default_factory=list)
    docs_unconfigured: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
    )
    if result.returncode:
        # Git diagnostics can contain authenticated remote URLs or file contents.
        raise RuntimeError(f"git {args[0]} failed (exit {result.returncode})")
    return result.stdout


def _paths(root: Path, *args: str) -> set[str]:
    return set(_git(root, *args).split("\0")) - {""}


def _is_note(path: str) -> bool:
    parts = Path(path).parts
    return (
        len(parts) >= 2 and parts[0] in {"services", "failures"}
        and path.endswith(".md") and parts[-1] != "_TEMPLATE.md"
        and "_drafts" not in parts and "_private" not in parts
    )


def run_update(root: Path, *, dry_run: bool) -> UpdateReport:
    root = root.resolve()
    current_version = (root / "VERSION").read_text(encoding="utf-8").strip()
    before = _git(root, "rev-parse", "HEAD").strip()
    latest_version = current_version
    changed: set[str] = set()
    fetched = False
    try:
        _git(root, "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main")
    except RuntimeError:
        if not dry_run:
            raise
    else:
        target = _git(root, "rev-parse", "origin/main").strip()
        latest_version = _git(root, "show", f"{target}:VERSION").strip()
        base = _git(root, "merge-base", "HEAD", target).strip()
        # A locally ahead checkout has nothing to pull; do not count its own work.
        changed = _paths(root, "diff", "--name-only", "--no-renames", "-z", base, target)
        fetched = True

    conflicts: list[str] = []
    if changed:
        dirty = _paths(root, "diff", "--name-only", "--no-renames", "-z")
        dirty |= _paths(root, "diff", "--cached", "--name-only", "--no-renames", "-z")
        # Include ignored files: Git otherwise permits replacing them during checkout.
        dirty |= _paths(root, "ls-files", "--others", "-z")
        conflicts = sorted(
            path for path in dirty
            if any(path == incoming or path.startswith(incoming + "/")
                   or incoming.startswith(path + "/") for incoming in changed)
        )

    notes_pulled = sum(_is_note(path) for path in changed) if dry_run else 0
    if not dry_run and fetched and not conflicts:
        # Pull the inspected snapshot, not a second fetch of a moving upstream.
        _git(root, "pull", "--ff-only", "--no-rebase", "--no-autostash", ".", target)
        notes_pulled = sum(
            _is_note(path)
            for path in _paths(root, "diff", "--name-only", "--no-renames", "-z", before, "HEAD")
        )
        current_version = (root / "VERSION").read_text(encoding="utf-8").strip()

    catalog = ContractCatalog(root)
    private_excluded = sorted(
        path.relative_to(root).as_posix()
        for path in catalog.iter_notes()
        if "_private" in path.relative_to(root).parts
    )
    unclassified: list[str] = []
    stale_candidates = revalidated = 0
    cutoff = date.today() - timedelta(days=90)
    docs_planned: list[str] = []
    docs_fetched: list[str] = []
    docs_changed: list[str] = []
    docs_failed: list[str] = []
    docs_unconfigured: list[str] = []
    for relative in sorted(_paths(root, "ls-files", "-z", "--", "services", "failures")):
        if not _is_note(relative):
            continue
        note = catalog.validate_note(root / relative)
        # Both parser generations (bool and string scalars) count as classified.
        if note.data.get("distributable") is None:
            unclassified.append(relative)
        try:
            aged = date.fromisoformat(note.data.get("last_verified", "")) <= cutoff
        except (TypeError, ValueError):
            aged = False
        if note.data.get("status") == "stale" or aged:
            stale_candidates += 1
            revalidated += note.ok
        if note.kind != "service":
            continue
        source = note.data.get("freshness_evidence")
        url = source.get("url") if isinstance(source, dict) else None
        if not note.ok or note.data.get("distributable") is not True or not isinstance(url, str) or not url:
            docs_unconfigured.append(relative)
            continue
        service_id = note.data["id"]
        docs_planned.append(service_id)
        if dry_run or conflicts:
            continue
        evidence = refresh_document(root, service_id, url)
        if evidence["status"] == "fetched":
            docs_fetched.append(service_id)
            if evidence.get("changed"):
                docs_changed.append(service_id)
        else:
            docs_failed.append(f"{service_id}: {evidence.get('error', 'fetch-failed')}")

    return UpdateReport(
        current_version=current_version, latest_version=latest_version,
        notes_pulled=notes_pulled, stale_candidates=stale_candidates,
        revalidated=revalidated, private_excluded=private_excluded,
        unclassified=unclassified, refused_conflicts=conflicts, dry_run=dry_run,
        docs_planned=docs_planned, docs_fetched=docs_fetched, docs_changed=docs_changed,
        docs_failed=docs_failed, docs_unconfigured=docs_unconfigured,
    )


def render_update(report: UpdateReport) -> str:
    lines: list[str] = []
    for field, value in report.to_dict().items():
        if isinstance(value, list):
            lines.append(f"{field}: {len(value)}")
            lines.extend(f"  - {path}" for path in value)
        elif isinstance(value, bool):
            lines.append(f"{field}: {str(value).lower()}")
        else:
            lines.append(f"{field}: {value}")
    return "\n".join(lines)
