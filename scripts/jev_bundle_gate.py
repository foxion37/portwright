#!/usr/bin/env python3
"""Gate exact built bytes before publication.

Personal packages require an exact match to the user's approved whole-package
inventory and are never sent to a model. Public Procedure bundles pass the local
secret screen before a secondary JEV check. Model checks never truncate input.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from portwright.jev import JevClient
from portwright.memory import SECRET_PATTERNS
from publish_release import validated_inventory

THRESHOLD = 0.5
MAX_MODEL_BYTES = 24_000
ALLOW_FILE = ROOT / "install" / "bundle-gate-allow.json"
PERSONAL_APPROVAL = ROOT / "install" / "personal-skills-inventory.json"


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


def main(argv: list[str]) -> int:
    out = Path(argv[0] if argv else ROOT / "build" / "skill-bundles").resolve()
    inventory = validated_inventory(out)
    allow = json.loads(ALLOW_FILE.read_text(encoding="utf-8"))["allow"] if ALLOW_FILE.is_file() else {}
    approved = json.loads(PERSONAL_APPROVAL.read_text(encoding="utf-8"))["skills"] if PERSONAL_APPROVAL.is_file() else []
    approvals = {entry["name"]: entry for entry in approved}
    for name in approvals:
        matches = [entry for entry in inventory["skills"].values() if entry["path"] == f"personal/{name}/SKILL.md"]
        if len(matches) != 1 or not personal_approved(matches[0], approvals):
            raise SystemExit(f"approved personal package missing, renamed, or changed: {name}")
    client = JevClient.from_env()
    failed = 0
    for skill_id, entry in inventory["skills"].items():
        # All resources, not just SKILL.md, participate in the local screen.
        files = [{"path": f["path"], "text": (out / f["path"]).read_text(encoding="utf-8")} for f in entry["files"]]
        if any(pattern.search(item["text"]) for item in files for pattern in SECRET_PATTERNS):
            print(f"{skill_id}: blocked by local secret screen")
            failed += 1
            continue
        if entry.get("personal") or entry["path"].startswith("personal/"):
            if personal_approved(entry, approvals):
                print(f"{skill_id}: approved immutable personal snapshot; no model call")
            else:
                print(f"{skill_id}: missing or changed personal approval")
                failed += 1
            continue
        if sum(len(item["text"].encode("utf-8")) for item in files) > MAX_MODEL_BYTES:
            print(f"{skill_id}: package exceeds model review bound; review required")
            failed += 1
            continue
        judgment = client.ask(
            f"bundle-gate-{skill_id}",
            {"skill_id": skill_id, "files": files},
            {"personal": {
                "type": "noul",
                "instructions": "Do the package files contain identifiers tied to a specific private person's environment: their login name, private host/IP, named home path, vault/item identifier, or internal project? Public upstream author attribution and generic tilde-relative installation defaults do not count.",
                "criteria": {"true": "specific private environment identifiers", "false": "generic reusable instructions and public source attribution only"},
            }},
        )
        answer = judgment.answer("personal")
        try:
            value = float(answer["noul"]) if answer else float("nan")
        except (KeyError, TypeError, ValueError):
            value = float("nan")
        if not math.isfinite(value) or not 0 <= value <= 1:
            print(f"{skill_id}: valid model judgment unavailable")
            failed += 1
            continue
        override = allow.get(skill_id, {})
        # Older name-only exceptions are deliberately ignored.
        pinned = override.get("files") == entry["files"]
        blocked = value >= THRESHOLD and not pinned
        failed += int(blocked)
        print(f"{skill_id}: personal={value:.2f} {'FAIL' if blocked else 'ok'}")
    print(f"gate: {len(inventory['skills']) - failed} ok, {failed} failed")
    return int(failed != 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
