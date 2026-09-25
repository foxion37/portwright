#!/usr/bin/env python3
"""Run the commons writer; prepare migration evidence without contacting a Hub.

Prepare once with --migration-source PATH --prepare-migration-evidence [PATH].
Complete each empty action/outcome from observed evidence before using
--migration-source PATH --migration-evidence PATH with the normal writer flags.
No credentials, note bodies or provider errors are printed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from portwright.contracts import distributable_notes
from portwright.hub_contracts import canonical_doc_url_issue, identifier_patterns, screening_issues
from portwright.memory import SECRET_PATTERNS
from portwright.hub_pipeline import PipelineError

IDENTIFIERS = identifier_patterns(json.loads(
    (ROOT / "install" / "identifier-policy.json").read_text(encoding="utf-8"))["patterns"])


def _public_url(value: object) -> str:
    if canonical_doc_url_issue(value) is not None:
        return ""
    if screening_issues(value, secrets=SECRET_PATTERNS, identifiers=IDENTIFIERS):
        return ""
    return value


def migration_evidence(source: Path) -> list[dict]:
    """Select tracked distributable notes only; never infer successful execution."""
    source = source.resolve(strict=True)
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "ls-files", "-z", "--", "services", "failures"],
        cwd=source, capture_output=True, check=True,
    )
    paths = result.stdout.decode("utf-8").split("\0")
    notes = [note for note in distributable_notes(source, filter(None, paths))
             if not ({"profile_id", "profile_ids"} & note.data.keys())]
    urls: dict[str, str] = {}
    for note in notes:
        if note.kind == "service":
            evidence = note.data.get("freshness_evidence", {})
            url = _public_url(evidence.get("url")) if isinstance(evidence, dict) else ""
            urls[note.note_id] = url or _public_url(note.data.get("doc_url"))
    return [{"source_path": note.relative_path,
             "doc_url": _public_url(note.data.get("doc_url")) or urls.get(
                 note.note_id if note.kind == "service" else note.data.get("service"), ""),
             "success_evidence": {"action": "", "outcome": ""}}
            for note in notes]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commons", type=Path)
    parser.add_argument("--code-root", type=Path, default=ROOT)
    parser.add_argument("--audience", choices=("public", "company"))
    parser.add_argument("--retry-held", action="store_true")
    parser.add_argument("--recall-only", action="store_true")
    parser.add_argument("--migration-source", type=Path)
    parser.add_argument("--migration-evidence", type=Path)
    parser.add_argument("--prepare-migration-evidence", type=Path, nargs="?",
                        const=Path("_local/hub/migration-evidence.json"))
    parser.add_argument("--review-note-id")
    parser.add_argument("--review-revision")
    parser.add_argument("--review-decision", choices=("promote", "recall"))
    parser.add_argument("--approve-review", action="store_true")
    parser.add_argument("--expected-events-digest")
    args = parser.parse_args(argv)
    if args.code_root.resolve() != ROOT:
        parser.error("code-root must be the executing code checkout")
    try:
        if args.prepare_migration_evidence is not None:
            if (args.migration_source is None or args.migration_evidence or args.commons
                    or args.review_note_id or args.recall_only or args.retry_held):
                parser.error("evidence preparation requires only migration-source and output")
            rows = migration_evidence(args.migration_source)
            output = args.prepare_migration_evidence
            for parent in (output, *output.parents):
                if parent.is_symlink():
                    raise ValueError("unsafe_output")
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as stream:
                json.dump(rows, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
            print(json.dumps({"notes": len(rows), "urls_filled": sum(bool(row["doc_url"]) for row in rows)}))
            return 0
        if args.commons is None or args.audience is None:
            parser.error("commons and audience are required")
        origin = os.environ.get("HUB_ORIGIN", "")
        from portwright.hub_pipeline import run_pipeline, review_revision, migrate_notes
        review = (args.review_note_id, args.review_revision, args.review_decision)
        if any(review) or args.approve_review or args.expected_events_digest:
            if not all(review) or args.recall_only or args.retry_held or args.migration_source or args.migration_evidence:
                parser.error("review requires note-id, revision and decision, without writer modes")
            if args.approve_review and not args.expected_events_digest:
                parser.error("review approval requires expected-events-digest")
            result = review_revision(args.commons, ROOT, origin, args.audience,
                                     *review, approve=args.approve_review,
                                     expected_events_digest=args.expected_events_digest)
        elif args.migration_source or args.migration_evidence:
            if not args.migration_source or not args.migration_evidence or args.recall_only or args.retry_held:
                parser.error("migration requires source and evidence without other writer modes")
            result = migrate_notes(args.commons, ROOT, origin, args.audience,
                                   args.migration_source, args.migration_evidence)
        else:
            result = run_pipeline(args.commons, ROOT, origin, args.audience,
                                  retry_held=args.retry_held, recall_only=args.recall_only)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
        return int(bool(result.get("errors")))
    except (OSError, ValueError, RuntimeError, PipelineError, subprocess.SubprocessError):
        print("HUB_PIPELINE_FAILED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
