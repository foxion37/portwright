#!/usr/bin/env python3
"""Gate exact built bytes before publication, per audience.

Shared (public/company) bundles are verified deterministically. The bundle must be an
exact rebuild of its content commit (with the same withheld revision set); every shipped
note must reproduce its revision and carry a Gate-Proof commit trailer in its path history
that recomputes its gate_digest under the current policy of its service. Free text that
no gate proof covers is refused, so a model decision can only ever come from the
pipeline's budget-wrapped gate.

Personal bundles carry no free text either: every package is either an exact match to
the approved personal inventory or a byte-identical copy of the code guide skills.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

from portwright import hub_contracts  # noqa: E402
from portwright.memory import SECRET_PATTERNS  # noqa: E402
from build_skill_bundles import PACKAGED_SKILLS, build, identifier_hits, read_tracked, tracked_files  # noqa: E402
from publish_release import LOCAL_FILES, validated_inventory  # noqa: E402
from portwright.hub_pipeline import CommonsRepo, attest  # noqa: E402

PERSONAL_APPROVAL = ROOT / "install" / "personal-skills-inventory.json"
SHARED_AUDIENCES = ("public", "company")
DOMAINS_FILE = "policy/official-doc-domains.json"


def personal_approved(entry: dict, approvals: dict[str, dict]) -> bool:
    path = PurePosixPath(entry["path"])
    if len(path.parts) != 3 or path.parts[0] != "personal" or path.parts[2] != "SKILL.md":
        return False
    approved = approvals.get(path.parts[1])
    if approved is None:
        return False
    expected = sorted((f["path"], f["sha256"], f["bytes"]) for f in approved["files"])
    try:
        actual = sorted((str(PurePosixPath(f["path"]).relative_to(path.parent)), f["sha256"], f["size"]) for f in entry["files"])
    except (KeyError, ValueError, TypeError):
        return False
    return actual == expected


def code_guide_matches(out: Path, name: str, entry: dict) -> bool:
    """A code guide package must equal the code checkout's own tracked bytes."""
    source_root = f"skills/{name}"
    expected = {Path(relative).relative_to(source_root).as_posix(): read_tracked(relative) for relative in tracked_files(source_root)}
    try:
        actual = {PurePosixPath(item["path"]).relative_to(name).as_posix(): (out / item["path"]).read_bytes() for item in entry["files"]}
    except (KeyError, ValueError, OSError):
        return False
    return actual == expected


def check_personal(out: Path) -> dict:
    """Only approved personal packages and fixed code guides may ship."""
    out = Path(out).resolve()
    inventory = validated_inventory(out)
    try:
        approvals = {entry["name"]: entry for entry in json.loads(PERSONAL_APPROVAL.read_text(encoding="utf-8"))["skills"]}
    except (OSError, ValueError, KeyError, TypeError):
        raise SystemExit(f"unreadable personal approval inventory: {PERSONAL_APPROVAL}") from None
    failed = 0
    for name in approvals:
        matches = [entry for entry in inventory["skills"].values() if entry["path"] == f"personal/{name}/SKILL.md"]
        if len(matches) != 1 or not personal_approved(matches[0], approvals):
            raise SystemExit(f"approved personal package missing, renamed, or changed: {name}")
    for name, entry in inventory["skills"].items():
        # All resources, not just SKILL.md, participate in the local screen.
        files = [{"path": item["path"], "text": (out / item["path"]).read_text(encoding="utf-8")} for item in entry["files"]]
        if any(pattern.search(item["text"]) for item in files for pattern in SECRET_PATTERNS):
            print(f"{name}: blocked by local secret screen")
            failed += 1
            continue
        personal_like = entry.get("personal") or str(entry["path"]).startswith("personal/")
        if not personal_like and any(identifier_hits(item["text"]) for item in files):
            print(f"{name}: blocked by local identifier screen")
            failed += 1
            continue
        if entry.get("origin") == "code" and name in PACKAGED_SKILLS:
            if code_guide_matches(out, name, entry):
                print(f"{name}: fixed code guide matches the code checkout")
            else:
                print(f"{name}: code guide differs from the code checkout")
                failed += 1
            continue
        if personal_like:
            if personal_approved(entry, approvals):
                print(f"{name}: approved immutable personal snapshot; no model call")
            else:
                print(f"{name}: missing or changed personal approval")
                failed += 1
            continue
        print(f"{name}: not an approved personal package or code guide")
        failed += 1
    print(f"gate: {len(inventory['skills']) - failed} ok, {failed} failed")
    if failed:
        raise SystemExit(f"personal bundle gate failed for {failed} package(s)")
    return {"ok": True, "audience": "personal", "skills": len(inventory["skills"])}


def tree(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file() and path.relative_to(root).as_posix() not in LOCAL_FILES}


def check_shared(out: Path, content_root: Path, audience: str, *, withheld: frozenset = frozenset()) -> dict:
    """Deterministic verification of one shared bundle against its commit and git proofs.

    `withheld` is the exact (note_id, revision) set the pipeline held back from the build;
    the rebuild uses the same set, so a bundle built with any other set is refused."""
    if audience not in SHARED_AUDIENCES:
        raise SystemExit(f"the bundle gate has no shared contract for audience: {audience}")
    out = Path(out).resolve()
    content_root = Path(content_root).resolve()
    manifest = validated_inventory(out)
    if manifest.get("audience") != audience:
        raise SystemExit("bundle inventory audience differs from the requested audience")
    try:
        index = json.loads((out / "note-index.json").read_text(encoding="utf-8"))
        domains_path = content_root / DOMAINS_FILE
        domains = json.loads(domains_path.read_text(encoding="utf-8")) if domains_path.is_file() else {}
    except (OSError, ValueError):
        raise SystemExit("bundle note index or official-domain policy is unreadable") from None
    with tempfile.TemporaryDirectory(prefix="portwright-gate-") as tmp:
        rebuilt = Path(tmp) / "bundle"
        regenerated = build(rebuilt, content_root=content_root, audience=audience, withheld=frozenset(withheld))
        if regenerated.get("commit") != manifest["commit"] or tree(rebuilt) != tree(out):
            raise SystemExit("the bundle is not an exact rebuild of its content commit")
    repo = CommonsRepo(content_root)
    for note in index["notes"]:
        verify_note(out, note, repo, domains)
    return {"ok": True, "audience": audience, "commit": manifest["commit"], "skills": len(manifest["skills"]), "notes": len(index["notes"])}


def verify_note(out: Path, note: dict, repo: CommonsRepo, domains: dict) -> None:
    """One shipped note must reproduce its revision and carry a valid Gate-Proof in git."""
    try:
        text = (out / note["path"]).read_text(encoding="utf-8")
        meta = hub_contracts.parse_note(text)["metadata"]
        revision = hub_contracts.note_revision(note["kind"], note["service_id"], text, str(meta.get("doc_url", "")))
    except (OSError, ValueError, KeyError, TypeError):
        raise SystemExit(f"shipped note cannot be verified: {note.get('note_id')}") from None
    if revision != note["revision"] or str(meta.get("grade", "")) != note["grade"]:
        raise SystemExit(f"shipped note does not reproduce its indexed revision and grade: {note['note_id']}")
    reason = attest(repo, {"note_id": note["note_id"], "revision": revision, "service_id": note["service_id"],
                           "gate_version": meta.get("gate_version"), "gate_digest": meta.get("gate_digest"),
                           "doc_digest": meta.get("doc_digest")}, domains)
    if reason != "ok":
        raise SystemExit(f"shipped note lacks a valid gate proof ({reason}): {note['note_id']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", nargs="?", default=str(ROOT / "build" / "skill-bundles"))
    parser.add_argument("--audience", required=True, choices=("personal", "public", "company"))
    parser.add_argument("--content-root", default=None, help="commons checkout (public/company)")
    args = parser.parse_args(argv)
    if args.audience == "personal":
        result = check_personal(Path(args.out))
    elif not args.content_root:
        raise SystemExit("--content-root is required for the shared audiences")
    else:
        result = check_shared(Path(args.out), Path(args.content_root), args.audience)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
