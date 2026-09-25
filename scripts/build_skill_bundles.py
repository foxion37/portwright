#!/usr/bin/env python3
"""Build SEP-2640 skill bundles from Portwright notes (G2).

`build(out, *, content_root, audience)` has three modes:
- `personal`: v1 inventory for the personal bucket. Only approved personal packages
  (exact bytes from install/personal-skills-inventory.json) and the fixed code guide
  skills; note bundles are not rebuilt for the personal bucket.
- `public`/`company`: v2 inventory for a shared Hub. `content_root` is the commons
  checkout: `services/`, `failures/` and their `trial/` siblings, split into `stable/`
  and `trial/` packages that keep the raw note bytes under `notes/` and carry
  note-index.json. `skills/`, `_private`, `profiles` and personal approvals are never
  read in this mode.
- `audience=None` (API only, historical M1): tracked distributable notes from the code
  root plus packaged and personal skills, v1 inventory and inline SKILL.md assembly.

Output: <out>/<package>/SKILL.md plus its supporting files and, in shared mode, the
digest-bound note-index.json. Anything under _private, _drafts, _hub, profiles, or
_evidence is a build failure, as are symlinks, unreadable, binary, or oversized
resources and notes that fail their mode's contract. Frontmatter uses `name` (==
directory) and `description` per SEP-2640.

Every emitted byte is scanned locally with SECRET_PATTERNS and listed in inventory.json
bound to the content HEAD commit; the publisher uploads only what this inventory lists.
The output directory is staged and delivered atomically; a pre-existing non-empty
directory without the build marker is refused.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from portwright import hub_contracts  # noqa: E402
from portwright.contracts import (  # noqa: E402
    SERVICE_ID_RE,
    distributable_notes,
    note_kind,
    parse_frontmatter,
    split_frontmatter,
)
from portwright.memory import SECRET_PATTERNS  # noqa: E402
SECRET_PATTERN_IDS = tuple(
    item["id"] for item in json.loads((ROOT / "install" / "secret-patterns.json").read_text(encoding="utf-8"))["patterns"]
)

IDENTIFIER_POLICY = json.loads((ROOT / "install" / "identifier-policy.json").read_text(encoding="utf-8"))
IDENTIFIER_PATTERNS = tuple(
    (item["id"], re.compile(item["regex"], re.IGNORECASE if item.get("flags") == "i" else 0))
    for item in IDENTIFIER_POLICY["patterns"]
)


def identifier_hits(text: str) -> list[str]:
    return [
        name for item, (name, pattern) in zip(IDENTIFIER_POLICY["patterns"], IDENTIFIER_PATTERNS)
        if item.get("scope") != "classification" and pattern.search(text)
    ]


def anonymize(text: str) -> str:
    for old, new in IDENTIFIER_POLICY["substitutions"]:
        text = text.replace(old, new)
    for item, (_, pattern) in zip(IDENTIFIER_POLICY["patterns"], IDENTIFIER_PATTERNS):
        if "replacement" in item:
            text = pattern.sub(item["replacement"], text)
    return text



try:  # optional strict frontmatter check; the publisher is the authoritative gate
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from publish_release import _node_yaml as _optional_yaml  # noqa: E402
except ImportError:
    _optional_yaml = None

FORBIDDEN_PARTS = {"_private", "_drafts", "_evidence", "_hub", "profiles"}
PACKAGED_SKILLS = ("portwright-tool-use", "portwright-tool-memory")
PERSONAL_ROOT = "skills/personal"
RESERVED_NAMES = {"personal", *PACKAGED_SKILLS}
MARKER = ".portwright-build"
MAX_RESOURCE_BYTES = 1024 * 1024
SEGMENT_RE = re.compile(r"[A-Za-z0-9._~-]+")

AUDIENCES = ("personal", "public", "company")
SHARED_AUDIENCES = ("public", "company")
# The shared Hub content source; hub_contracts.validate_note_index enforces it.
NOTE_URI_SOURCE = "commons"
# commons directory -> (wire kind, note_id namespace), per the M2 design contract
NOTE_DIRS = {"services": ("procedure", "service"), "failures": ("lesson", "failure")}
NOTE_GRADES = ("stable", "trial")
DIGEST_RE = re.compile(r"sha256:[a-f0-9]{64}")
TRIAL = "trial"
APPROVAL_FILE = ROOT / "install" / "personal-skills-inventory.json"


def tracked_files(*prefixes: str, root: Path | None = None) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(root or ROOT), "ls-files", "-z", "--", *prefixes],
        capture_output=True, text=True, check=True,
    ).stdout
    return sorted(p for p in out.split("\0") if p)


def read_tracked(relative: str, *, root: Path | None = None) -> bytes:
    source = (root or ROOT) / relative
    if source.is_symlink() or not source.is_file():
        raise SystemExit(f"refusing non-regular resource in bundle: {relative}")
    try:
        return source.read_bytes()
    except OSError as error:
        raise SystemExit(f"refusing unreadable resource in bundle: {relative} ({error})")


def git_head(root: Path) -> str:
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


def require_clean(root: Path, prefixes: tuple[str, ...]) -> None:
    """Refuse staged, modified or untracked input, so the labelled commit is the shipped
    bytes rather than whatever the working tree happens to hold."""
    changed = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "-z", "--", *prefixes],
        capture_output=True, text=True, check=True,
    ).stdout
    if changed.strip("\0"):
        entry = changed.split("\0")[0]
        raise SystemExit(f"refusing to build from content that differs from HEAD: {entry[3:]}")


def guard(relative: str, data: bytes, *, public: bool = False) -> str:
    """Screen one included file; returns its UTF-8 text. Any refusal aborts the build."""
    if FORBIDDEN_PARTS & set(Path(relative).parts):
        raise SystemExit(f"refusing private or draft content in bundle: {relative}")
    if len(data) > MAX_RESOURCE_BYTES:
        raise SystemExit(f"refusing oversized resource in bundle: {relative}")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise SystemExit(f"refusing binary resource in bundle: {relative}")
    for name, pattern in zip(SECRET_PATTERN_IDS, SECRET_PATTERNS):
        if pattern.search(text):
            raise SystemExit(f"refusing note with suspected secret material: {relative} ({name})")
    if public:
        hits = identifier_hits(text)
        if hits:
            raise SystemExit(f"refusing personal identifier in public bundle: {relative} ({', '.join(hits)})")
    return text


def emit(staging: Path, relative: str, data: bytes, files: list[dict]) -> None:
    for part in Path(relative).parts:
        if part in {".", ".."} or not SEGMENT_RE.fullmatch(part):
            raise SystemExit(f"refusing non-canonical bundle path: {relative}")
    target = staging / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    files.append({"path": relative, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})


def copy_package(source_root: str, dest_root: str, staging: Path, files: list[dict], *, public: bool = True, root: Path | None = None) -> None:
    for relative in tracked_files(source_root, root=root):
        data = read_tracked(relative, root=root)
        guard(relative, data, public=public)
        dest = f"{dest_root}/{Path(relative).relative_to(source_root).as_posix()}"
        emit(staging, dest, data, files)


def personal_packages(root: Path | None = None) -> dict[str, list[str]]:
    """Tracked files under skills/personal/<name>/; anything else under skills/ that is
    not a packaged skill is unclassified distributable content and fails the build."""
    packages: dict[str, list[str]] = {}
    for relative in tracked_files("skills", root=root):
        parts = Path(relative).parts
        if len(parts) >= 3 and parts[1] in PACKAGED_SKILLS:
            continue
        if len(parts) >= 4 and parts[1] == "personal":
            packages.setdefault(parts[2], []).append(relative)
            continue
        raise SystemExit(f"refusing unclassified tracked skill content: {relative}")
    return packages


def check_personal_frontmatter(name: str, text: str) -> None:
    """Lenient identity check only: every frontmatter value ships byte-identical, so
    the builder never rewrites or subset-parses it. The publisher performs the strict
    YAML parse the Worker compares against; when that parser is importable here we
    run it early, but its absence never blocks a build."""
    front, _ = split_frontmatter(text)
    if front is None:
        raise SystemExit(f"personal skill {name}: SKILL.md needs a frontmatter block")
    top = {line.split(":", 1)[0].strip(): line.split(":", 1)[1].strip() for line in front if line == line.lstrip() and ":" in line}
    raw_name = top.get("name", "")
    if raw_name[:1] in "\"'" and raw_name[-1:] == raw_name[:1]:
        raw_name = raw_name[1:-1]
    if raw_name != name:
        raise SystemExit(f"personal skill {name}: frontmatter name must equal the directory name")
    if "description" not in top:
        raise SystemExit(f"personal skill {name}: frontmatter needs a description")
    if _optional_yaml is not None:
        parsed = _optional_yaml("\n".join(front))
        if parsed is not None and (not isinstance(parsed, dict) or parsed.get("name") != name or not parsed.get("description")):
            raise SystemExit(f"personal skill {name}: YAML frontmatter needs name: {name} and a description")


def check_destination(out: Path) -> None:
    if out.is_symlink() or (out.exists() and not out.is_dir()):
        raise SystemExit(f"refusing output destination: {out}")
    if out.exists() and any(out.iterdir()) and not (out / MARKER).is_file():
        raise SystemExit(f"refusing to replace non-empty directory without {MARKER}: {out}")


def deliver(staging: Path, out: Path) -> None:
    """Atomically replace an empty or previously-built output directory; never
    recursively delete a destination we did not build."""
    check_destination(out)
    previous = out.with_name(f".{out.name}.previous-{os.getpid()}")
    try:
        if out.exists():
            out.rename(previous)
        staging.rename(out)
    except OSError as error:
        if previous.exists() and not out.exists():
            previous.rename(out)
        raise SystemExit(f"cannot deliver bundle output to {out}: {error}")
    if previous.exists():
        shutil.rmtree(previous)


def stage(out: Path) -> Path:
    """A fresh sibling staging directory for one build."""
    check_destination(out)
    staging = out.with_name(f".{out.name}.tmp-{os.getpid()}")
    if staging.exists() or staging.is_symlink():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    return staging


def canon_note(relative: str, content_root: Path, excluded: frozenset = frozenset()) -> dict | None:
    """One commons canon note (grade from the path); None for the skipped template."""
    parts = Path(relative).parts
    grade = "stable"
    if parts[:1] == (TRIAL,):
        grade, parts = TRIAL, parts[1:]
    if len(parts) != 2 or parts[0] not in NOTE_DIRS or not parts[1].endswith(".md"):
        raise SystemExit(f"refusing unrecognized commons note path: {relative}")
    if parts[1] == "_TEMPLATE.md":
        return None
    stem = Path(parts[1]).stem
    if not SEGMENT_RE.fullmatch(stem):
        raise SystemExit(f"refusing non-canonical commons note name: {relative}")
    data = read_tracked(relative, root=content_root)
    try:
        parsed = hub_contracts.parse_note(data.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise SystemExit(f"unreadable commons note: {relative}") from None
    meta = parsed["metadata"]
    if (hub_contracts.note_id_from_path(relative), meta.get("revision")) in excluded:
        return None
    text = guard(relative, data, public=True)
    if meta.get("distributable") is not True:
        raise SystemExit(f"commons note lacks distributable: true: {relative}")
    note_grade = meta.get("grade")
    if note_grade not in NOTE_GRADES:
        raise SystemExit(f"commons note grade is missing or unknown: {relative}")
    # A first submission starts as trial at the canonical path; only an update of an
    # already-stable note uses the trial/ sibling, which is always trial.
    if grade == TRIAL and note_grade != TRIAL:
        raise SystemExit(f"commons note grade does not match its trial path: {relative}")
    for field in ("revision", "doc_digest", "gate_digest"):
        if not DIGEST_RE.fullmatch(str(meta.get(field, ""))):
            raise SystemExit(f"commons note {field} is missing or malformed: {relative}")
    for field in ("gate_version", "intake_id"):
        if not str(meta.get(field, "")).strip():
            raise SystemExit(f"commons note lacks {field}: {relative}")
    if not str(meta.get("doc_url", "")).startswith("https://"):
        raise SystemExit(f"commons note doc_url must be an https URL: {relative}")
    wire_kind, _ = NOTE_DIRS[parts[0]]
    if wire_kind == "lesson" and meta.get("status") != "active":
        return None
    service_id = stem if wire_kind == "procedure" else str(meta.get("service", ""))
    if not SERVICE_ID_RE.fullmatch(service_id):
        raise SystemExit(f"commons note has an invalid service id: {relative}")
    try:
        revision = hub_contracts.note_revision(wire_kind, service_id, text, str(meta["doc_url"]))
    except ValueError:
        raise SystemExit(f"commons note cannot be canonicalized: {relative}") from None
    if revision != str(meta["revision"]):
        raise SystemExit(f"commons note revision does not match its bytes: {relative}")
    try:
        note_id = hub_contracts.note_id_from_path(relative)
    except ValueError:
        raise SystemExit(f"refusing unrecognized commons note path: {relative}") from None
    return {
        "relative": relative, "grade": note_grade, "wire_kind": wire_kind,
        "note_id": note_id, "service_id": service_id,
        "stem": stem, "body": parsed["body"].rstrip("\n"), "data": data,
        "revision": revision, "intake_id": str(meta["intake_id"]),
        "gate_digest": str(meta["gate_digest"]),
        "display_name": re.sub(r"\s+", " ", str(meta.get("display_name") or service_id)).strip(),
        "digest": "sha256:" + hashlib.sha256(data).hexdigest(), "size": len(data),
    }


def recalled_notes(content_root: Path) -> set[tuple[str, str]]:
    """(note_id, revision) pairs marked recalled in the commons tree."""
    markers: set[tuple[str, str]] = set()
    for relative in tracked_files("recalls", root=content_root):
        parts = Path(relative).parts
        if len(parts) != 4 or parts[0] != "recalls" or parts[1] not in NOTE_DIRS or not re.fullmatch(r"[a-f0-9]{64}\.json", parts[3]):
            raise SystemExit(f"refusing unrecognized commons recall path: {relative}")
        try:
            marker = json.loads(read_tracked(relative, root=content_root).decode("utf-8"))
            note_id, revision = str(marker["note_id"]), str(marker["revision"])
        except (UnicodeDecodeError, ValueError, KeyError, TypeError):
            raise SystemExit(f"unreadable commons recall marker: {relative}") from None
        if not note_id or not DIGEST_RE.fullmatch(revision):
            raise SystemExit(f"malformed commons recall marker: {relative}")
        markers.add((note_id, revision))
    return markers


def render_skill(service_id: str, grade: str, procedure: dict | None, lessons: list[dict]) -> bytes:
    """The fixed SKILL.md template: identity, grade, note references, then note bodies."""
    refs = ([procedure] if procedure else []) + lessons
    title = procedure["display_name"] if procedure else service_id
    description = re.sub(r"\s+", " ", f"Portwright {grade} notes for {title}.")[:240]
    lines = [
        "---", f"name: {service_id}", f"description: {json.dumps(description, ensure_ascii=False)}",
        f"grade: {grade}", "---", "", f"# {title}", "", "## Note references", "",
    ]
    lines += [f"- {note['note_id']} {note['revision']}" for note in refs]
    if procedure:
        lines += ["", procedure["body"], ""]
    if lessons:
        lines += ["", f"## Lessons ({grade})", ""]
        for note in lessons:
            lines += [f"### {note['note_id']}", "", note["body"], ""]
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def build_shared(out: Path, content_root: Path, audience: str, withheld: frozenset = frozenset()) -> dict:
    """v2 bundle set for one shared audience: stable/trial packages plus the note index.
    Recalled and `withheld` (note_id, revision) pairs are left out; siblings still ship."""
    commit = git_head(content_root)
    require_clean(content_root, ("services", "failures", f"{TRIAL}/services", f"{TRIAL}/failures", "recalls", "policy"))
    tracked = tracked_files("services", "failures", f"{TRIAL}/services", f"{TRIAL}/failures", root=content_root)
    excluded = recalled_notes(content_root) | set(withheld)
    notes = [note for relative in tracked if (note := canon_note(relative, content_root, excluded)) is not None]
    packages: dict[tuple[str, str], dict] = {}
    for note in notes:
        package = packages.setdefault((note["grade"], note["service_id"]), {"procedure": None, "lessons": []})
        if note["wire_kind"] == "procedure":
            package["procedure"] = note
        else:
            package["lessons"].append(note)
    staging = stage(out)
    files: list[dict] = []
    index_notes: list[dict] = []
    manifest: dict[str, dict] = {}
    try:
        for (grade, service_id), package in sorted(packages.items()):
            procedure = package["procedure"]
            lessons = sorted(package["lessons"], key=lambda note: note["note_id"])
            members = ([procedure] if procedure else []) + lessons
            root = f"{grade}/{service_id}"
            content = render_skill(service_id, grade, procedure, lessons)
            guard(f"{root}/SKILL.md", content, public=True)
            before = len(files)
            emit(staging, f"{root}/SKILL.md", content, files)
            for note in members:
                path = f"{root}/notes/{note['stem']}.md"
                emit(staging, path, note["data"], files)
                index_notes.append({
                    "note_id": note["note_id"], "kind": note["wire_kind"], "service_id": service_id,
                    "revision": note["revision"], "grade": grade, "path": path,
                    "uri": f"skill://gisul/{NOTE_URI_SOURCE}/{path}",
                    "file_digest": note["digest"], "size": note["size"],
                })
            manifest[root] = {
                "path": f"{root}/SKILL.md", "sha256": hashlib.sha256(content).hexdigest(),
                "grade": grade,
                "note_refs": [{"note_id": note["note_id"], "revision": note["revision"]} for note in members],
                "files": files[before:],
            }
        index_notes.sort(key=lambda item: (item["note_id"], item["grade"]))
        note_index = {"schema_version": 1, "audience": audience, "commit": commit, "notes": index_notes}
        verdict = hub_contracts.validate_note_index(note_index)
        if not verdict["ok"]:
            raise SystemExit("note index is not valid: " + ", ".join(verdict["errors"]))
        index_bytes = (json.dumps(note_index, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        emit(staging, "note-index.json", index_bytes, files)
        inventory = {
            "schema_version": 2,
            "audience": audience,
            "commit": commit,
            "skills": manifest,
            "files": files,
            "note_index": {"path": "note-index.json", "digest": "sha256:" + hashlib.sha256(index_bytes).hexdigest(), "size": len(index_bytes)},
        }
        (staging / "inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (staging / MARKER).write_text("portwright skill bundle output; safe to replace\n", encoding="utf-8")
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    deliver(staging, out)
    return inventory


def load_approvals() -> dict[str, dict]:
    if not APPROVAL_FILE.is_file():
        return {}
    try:
        entries = json.loads(APPROVAL_FILE.read_text(encoding="utf-8"))["skills"]
    except (OSError, ValueError, KeyError, TypeError):
        raise SystemExit(f"unreadable personal approval inventory: {APPROVAL_FILE}") from None
    if not isinstance(entries, list):
        raise SystemExit(f"malformed personal approval inventory: {APPROVAL_FILE}")
    return {str(entry["name"]): entry for entry in entries if isinstance(entry, dict) and "name" in entry}


def approved_members(name: str, members: list[str], approvals: dict[str, dict], content_root: Path) -> dict[str, tuple[str, bytes]]:
    """{source relative: (bundle relative, bytes)} for one personal package, or a refusal.
    The complete package must equal the approved inventory byte for byte."""
    approved = approvals.get(name)
    if approved is None:
        raise SystemExit(f"personal skill is not in the approved inventory: {name}")
    source_root = f"{PERSONAL_ROOT}/{name}"
    package = {}
    for relative in members:
        package[relative] = (Path(relative).relative_to(source_root).as_posix(), read_tracked(relative, root=content_root))
    try:
        expected = sorted((str(item["path"]), str(item["sha256"]), int(item["bytes"])) for item in approved["files"])
    except (KeyError, TypeError, ValueError):
        raise SystemExit(f"malformed approval entry for personal skill: {name}") from None
    actual = sorted((dest, hashlib.sha256(data).hexdigest(), len(data)) for dest, data in package.values())
    if actual != expected:
        raise SystemExit(f"personal skill bytes differ from the approved inventory: {name}")
    return package


def build_personal(out: Path, content_root: Path) -> dict:
    """v1 bundle set: approved personal packages plus the fixed code guide skills."""
    commit = git_head(content_root)
    require_clean(content_root, ("skills",))
    approvals = load_approvals()
    staging = stage(out)
    files: list[dict] = []
    manifest: dict[str, dict] = {}
    try:
        for skill in PACKAGED_SKILLS:
            source_root = f"skills/{skill}"
            if f"{source_root}/SKILL.md" not in tracked_files(source_root):
                raise SystemExit(f"packaged skill lacks tracked SKILL.md: {source_root}")
            before = len(files)
            copy_package(source_root, skill, staging, files, public=False)
            digest = hashlib.sha256((staging / skill / "SKILL.md").read_bytes()).hexdigest()
            manifest[skill] = {"path": f"{skill}/SKILL.md", "sha256": digest, "origin": "code", "files": files[before:]}
        for name, members in sorted(personal_packages(content_root).items()):
            if not SERVICE_ID_RE.fullmatch(name):
                raise SystemExit(f"invalid personal skill name: {name}")
            if name in manifest or name in RESERVED_NAMES:
                raise SystemExit(f"personal skill name collides with another bundle skill: {name}")
            package = approved_members(name, members, approvals, content_root)
            skill_source = f"{PERSONAL_ROOT}/{name}/SKILL.md"
            if skill_source not in package:
                raise SystemExit(f"personal skill lacks tracked SKILL.md: {PERSONAL_ROOT}/{name}")
            before = len(files)
            skill_text = None
            for source, (dest, data) in sorted(package.items()):
                text = guard(source, data, public=False)
                if dest == "SKILL.md":
                    skill_text = text
                emit(staging, f"personal/{name}/{dest}", data, files)
            check_personal_frontmatter(name, skill_text)
            digest = hashlib.sha256((staging / "personal" / name / "SKILL.md").read_bytes()).hexdigest()
            manifest[name] = {"path": f"personal/{name}/SKILL.md", "sha256": digest, "personal": True, "files": files[before:]}
        inventory = {
            "commit": commit,
            "skills": manifest,
            "files": files,
            "watch_sources": [],
            "skipped_not_distributable": [],
            "skipped_private_or_draft": [],
        }
        (staging / "inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (staging / MARKER).write_text("portwright skill bundle output; safe to replace\n", encoding="utf-8")
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    deliver(staging, out)
    return inventory


def build(out: Path, *, content_root: Path | None = None, audience: str | None = None,
          withheld: frozenset = frozenset()) -> dict:
    """Build one bundle set. `audience=None` is the historical M1 API only; the CLI
    always names an audience. `withheld` (shared only) holds back unproven revisions."""
    if audience is None:
        return build_legacy(out)
    if audience not in AUDIENCES:
        raise SystemExit(f"unknown audience: {audience}")
    content = Path(content_root).resolve() if content_root else ROOT
    if audience == "personal":
        return build_personal(out, content)
    return build_shared(out, content, audience, withheld)


def build_legacy(out: Path) -> dict:
    tracked = tracked_files("services", "failures")
    staging = stage(out)
    files: list[dict] = []
    try:
        lessons: dict[str, list[tuple[str, str]]] = {}
        services: list[tuple[str, dict, str]] = []
        watch: list[tuple[str, str]] = []
        skipped_drafts = [relative for relative in tracked if FORBIDDEN_PARTS & set(Path(relative).parts)]
        try:
            notes = distributable_notes(ROOT, tracked)
        except ValueError as error:
            raise SystemExit(str(error)) from None
        chosen = {note.relative_path for note in notes}
        skipped = [relative for relative in tracked
                   if note_kind(relative) in {"service", "failure"}
                   and relative not in chosen and relative not in skipped_drafts]
        for note in notes:
            relative = note.relative_path
            guard(relative, read_tracked(relative))
            body = "\n".join(note.body).strip()
            if note.kind == "service":
                services.append((note.data["id"], note.data, body))
            elif note.data.get("status") == "active":
                lessons.setdefault(note.data["service"], []).append((Path(relative).name, body))
        manifest: dict[str, dict] = {}
        for service_id, data, body in services:
            if not SERVICE_ID_RE.fullmatch(service_id) or service_id in RESERVED_NAMES:
                raise SystemExit(f"invalid service id in bundle: {service_id}")
            if service_id in manifest:
                raise SystemExit(f"duplicate service id in bundle: {service_id}")
            description = f"Portwright procedure for {data['display_name']} (verified {data['last_verified']}, {data['version_tag']}). Use before calling or instructing this service."
            description = re.sub(r"\s+", " ", description)[:240]
            lines = ["---", f"name: {service_id}", f"description: {json.dumps(description, ensure_ascii=False)}", "---", "", f"# {data['display_name']}", "", body]
            related = lessons.get(service_id, [])
            if related:
                lines += ["", "## Active Lessons (tracked)", ""]
                for name, lesson_body in related:
                    lines += [f"### {name}", "", lesson_body, ""]
            content = anonymize("\n".join(lines).rstrip() + "\n").encode("utf-8")
            guard(relative, content, public=True)
            emit(staging, f"{service_id}/SKILL.md", content, files)
            manifest[service_id] = {"path": f"{service_id}/SKILL.md", "sha256": hashlib.sha256(content).hexdigest(), "lessons": len(related), "files": files[-1:]}
            evidence = data.get("freshness_evidence")
            if isinstance(evidence, dict) and evidence.get("url"):
                url = evidence["url"]
                if not isinstance(url, str) or not url.startswith("https://"):
                    raise SystemExit(f"refusing non-https freshness_evidence url in {service_id}")
                watch.append((service_id, url))
        for skill in PACKAGED_SKILLS:
            source_root = f"skills/{skill}"
            if f"{source_root}/SKILL.md" not in tracked_files(source_root):
                raise SystemExit(f"packaged skill lacks tracked SKILL.md: {source_root}")
            before = len(files)
            copy_package(source_root, skill, staging, files)
            digest = hashlib.sha256((staging / skill / "SKILL.md").read_bytes()).hexdigest()
            manifest[skill] = {"path": f"{skill}/SKILL.md", "sha256": digest, "lessons": 0, "files": files[before:]}
        for name, members in sorted(personal_packages().items()):
            if not SERVICE_ID_RE.fullmatch(name):
                raise SystemExit(f"invalid personal skill name: {name}")
            if name in manifest or name in RESERVED_NAMES:
                raise SystemExit(f"personal skill name collides with another bundle skill: {name}")
            skill_md = f"{PERSONAL_ROOT}/{name}/SKILL.md"
            if skill_md not in members:
                raise SystemExit(f"personal skill lacks tracked SKILL.md: {PERSONAL_ROOT}/{name}")
            check_personal_frontmatter(name, guard(skill_md, read_tracked(skill_md)))
            before = len(files)
            copy_package(f"{PERSONAL_ROOT}/{name}", f"personal/{name}", staging, files, public=False)
            digest = hashlib.sha256((staging / "personal" / name / "SKILL.md").read_bytes()).hexdigest()
            manifest[name] = {"path": f"personal/{name}/SKILL.md", "sha256": digest, "lessons": 0, "personal": True, "files": files[before:]}
        sources = [{"skill": skill_id, "url": url} for skill_id, url in sorted(watch)]
        if sources:
            payload = (json.dumps({"sources": sources}, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            guard("watch-sources.json", payload, public=True)
            emit(staging, "watch-sources.json", payload, files)
        inventory = {
            "commit": git_head(ROOT),
            "skills": manifest,
            "files": files,
            "watch_sources": sources,
            "skipped_not_distributable": skipped,
            "skipped_private_or_draft": skipped_drafts,
        }
        (staging / "inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (staging / MARKER).write_text("portwright skill bundle output; safe to replace\n", encoding="utf-8")
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    deliver(staging, out)
    return inventory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(ROOT / "build" / "skill-bundles"))
    parser.add_argument("--content-root", default=None, help="commons checkout for public/company; code root otherwise")
    parser.add_argument("--audience", required=True, choices=AUDIENCES)
    args = parser.parse_args(argv)
    out = Path(args.out)
    if out.is_symlink():
        raise SystemExit(f"refusing output destination: {out}")
    if args.audience in SHARED_AUDIENCES and not args.content_root:
        raise SystemExit("--content-root is required for the public and company audiences")
    out = out.resolve()
    inventory = build(out, content_root=Path(args.content_root) if args.content_root else None, audience=args.audience)
    summary = {"commit": inventory["commit"][:7], "audience": args.audience, "skills": len(inventory["skills"])}
    if inventory.get("schema_version") == 2:
        summary["notes"] = len(json.loads((out / "note-index.json").read_text(encoding="utf-8"))["notes"])
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
