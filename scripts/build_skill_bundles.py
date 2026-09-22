#!/usr/bin/env python3
"""Build SEP-2640 skill bundles from distributable Portwright notes (G2).

Output: <out>/<service-id>/SKILL.md for every Git-tracked, valid services/<id>.md with
`distributable: true`, with that Service's active tracked Lessons appended; the two
packaged skills with every tracked supporting file (references, agents, license,
notice); and any tracked skills/personal/<name>/ packages emitted at personal/<name>/.
Anything under _private, _drafts, profiles, or _evidence is a build failure, as are
symlinks, unreadable, binary, or oversized resources and distributable notes that fail
validation. Frontmatter uses `name` (== directory) and `description` per SEP-2640.

Every emitted byte is scanned locally with SECRET_PATTERNS and listed in inventory.json
(path, sha256, size) bound to the immutable source commit; the publisher uploads only
what this inventory lists. A watch-sources.json index is emitted when distributable
Procedures carry freshness_evidence.url. The output directory is staged and delivered
atomically; a pre-existing non-empty directory without the build marker is refused.
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

from portwright.contracts import (  # noqa: E402
    ContractCatalog,
    SERVICE_ID_RE,
    parse_frontmatter,
    split_frontmatter,
)
from portwright.memory import SECRET_PATTERNS  # noqa: E402

try:  # optional strict frontmatter check; the publisher is the authoritative gate
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from publish_release import _node_yaml as _optional_yaml  # noqa: E402
except ImportError:
    _optional_yaml = None

FORBIDDEN_PARTS = {"_private", "_drafts", "_evidence", "profiles"}
PACKAGED_SKILLS = ("portwright-tool-use", "portwright-tool-memory")
PERSONAL_ROOT = "skills/personal"
RESERVED_NAMES = {"personal", *PACKAGED_SKILLS}
MARKER = ".portwright-build"
MAX_RESOURCE_BYTES = 1024 * 1024
SEGMENT_RE = re.compile(r"[A-Za-z0-9._~-]+")


def tracked_files(*prefixes: str) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z", "--", *prefixes],
        capture_output=True, text=True, check=True,
    ).stdout
    return sorted(p for p in out.split("\0") if p)


def read_tracked(relative: str) -> bytes:
    source = ROOT / relative
    if source.is_symlink() or not source.is_file():
        raise SystemExit(f"refusing non-regular resource in bundle: {relative}")
    try:
        return source.read_bytes()
    except OSError as error:
        raise SystemExit(f"refusing unreadable resource in bundle: {relative} ({error})")


def guard(relative: str, data: bytes) -> str:
    """Screen one included file; returns its UTF-8 text. Any refusal aborts the build."""
    if FORBIDDEN_PARTS & set(Path(relative).parts):
        raise SystemExit(f"refusing private or draft content in bundle: {relative}")
    if len(data) > MAX_RESOURCE_BYTES:
        raise SystemExit(f"refusing oversized resource in bundle: {relative}")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise SystemExit(f"refusing binary resource in bundle: {relative}")
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise SystemExit(f"refusing note with suspected secret material: {relative}")
    return text


def emit(staging: Path, relative: str, data: bytes, files: list[dict]) -> None:
    for part in Path(relative).parts:
        if part in {".", ".."} or not SEGMENT_RE.fullmatch(part):
            raise SystemExit(f"refusing non-canonical bundle path: {relative}")
    target = staging / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    files.append({"path": relative, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})


def copy_package(source_root: str, dest_root: str, staging: Path, files: list[dict]) -> None:
    for relative in tracked_files(source_root):
        data = read_tracked(relative)
        guard(relative, data)
        dest = f"{dest_root}/{Path(relative).relative_to(source_root).as_posix()}"
        emit(staging, dest, data, files)


def personal_packages() -> dict[str, list[str]]:
    """Tracked files under skills/personal/<name>/; anything else under skills/ that is
    not a packaged skill is unclassified distributable content and fails the build."""
    packages: dict[str, list[str]] = {}
    for relative in tracked_files("skills"):
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


def build(out: Path) -> dict:
    catalog = ContractCatalog(ROOT)
    check_destination(out)
    staging = out.with_name(f".{out.name}.tmp-{os.getpid()}")
    if staging.exists() or staging.is_symlink():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    files: list[dict] = []
    try:
        lessons: dict[str, list[tuple[str, str]]] = {}
        services: list[tuple[str, dict, str]] = []
        skipped_drafts: list[str] = []
        skipped: list[str] = []
        watch: list[tuple[str, str]] = []
        for relative in tracked_files("services", "failures"):
            if FORBIDDEN_PARTS & set(Path(relative).parts):
                skipped_drafts.append(relative)  # tracked drafts: explicitly skipped, never published
                continue
            if not relative.endswith(".md") or relative.endswith("_TEMPLATE.md"):
                continue
            note = catalog.validate_note(ROOT / relative)
            if note.data.get("distributable") is not True:
                skipped.append(relative)
                continue
            if not note.ok:
                issues = "; ".join(issue.render() for issue in note.issues)
                raise SystemExit(f"refusing invalid distributable note: {relative} ({issues})")
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
            content = ("\n".join(lines).rstrip() + "\n").encode("utf-8")
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
            copy_package(f"{PERSONAL_ROOT}/{name}", f"personal/{name}", staging, files)
            digest = hashlib.sha256((staging / "personal" / name / "SKILL.md").read_bytes()).hexdigest()
            manifest[name] = {"path": f"personal/{name}/SKILL.md", "sha256": digest, "lessons": 0, "personal": True, "files": files[before:]}
        sources = [{"skill": skill_id, "url": url} for skill_id, url in sorted(watch)]
        if sources:
            payload = (json.dumps({"sources": sources}, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            emit(staging, "watch-sources.json", payload, files)
        inventory = {
            "commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip(),
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(ROOT / "build" / "skill-bundles"))
    args = parser.parse_args()
    out = Path(args.out)
    if out.is_symlink():
        raise SystemExit(f"refusing output destination: {out}")
    inventory = build(out.resolve())
    print(json.dumps({"commit": inventory["commit"][:7], "skills": len(inventory["skills"]), "skipped": len(inventory["skipped_not_distributable"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
