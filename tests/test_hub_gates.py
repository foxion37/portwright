"""Pure-contract and transport tests for the Hub writer/gate seam.

Only lib/portwright/hub_contracts.py, jev.py and memory.py are exercised: no
network, no git, no D1, no external service. Allowed shapes are assembled at
runtime so no secret-shaped literal lives in this file.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from portwright import hub_contracts as hc
from portwright import jev
from portwright.memory import SECRET_PATTERNS


DOC_URL = "https://docs.example.com/guide"
UUID = "0f8fad5b-d9cb-469f-a165-70867728950e"
CREATED = int(datetime(2026, 9, 25, 3, 0, tzinfo=timezone.utc).timestamp())

BASE_META = {
    "name": "example",
    "kind": "service",
    "last_verified": "2026-09-01",
    "distributable": True,
}


def note_text(body: str = "Step one.\n", meta: dict | None = None, *, frontmatter: bool = True) -> str:
    if not frontmatter:
        return body
    fields = dict(BASE_META if meta is None else meta)
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, bool):
            value = "true" if value else "false"
        lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n" + body


def index_note(
    note_id: str,
    grade: str,
    revision: str,
    *,
    size: int = 512,
    file_digest: str | None = None,
) -> dict:
    kind, slug = hc.split_note_id(note_id)
    service_id = slug if kind == "procedure" else "example"
    stem = service_id if kind == "procedure" else slug
    path = f"{grade}/{service_id}/notes/{stem}.md"
    return {
        "note_id": note_id,
        "kind": kind,
        "service_id": service_id,
        "revision": revision,
        "grade": grade,
        "path": path,
        "uri": f"skill://gisul/commons/{path}",
        "file_digest": file_digest or revision,
        "size": size,
    }


REV_A = "sha256:" + "a" * 64
REV_B = "sha256:" + "b" * 64
REV_C = "sha256:" + "c" * 64
COMMIT = "d" * 40


def index_document(notes: list[dict], *, audience: str = "public", commit: str = COMMIT) -> dict:
    return {"schema_version": 1, "audience": audience, "commit": commit, "notes": notes}


class CanonicalJsonTests(unittest.TestCase):
    def test_sorted_compact_and_utf8(self) -> None:
        self.assertEqual(hc.canonical_json({"b": 1, "a": "é"}), b'{"a":"\xc3\xa9","b":1}')

    def test_rejects_nan_and_infinity(self) -> None:
        for value in (float("nan"), float("inf"), {"x": float("-inf")}):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    hc.canonical_json(value)

    def test_digest_prefix_and_hex(self) -> None:
        self.assertEqual(
            hc.sha256_digest("abc"),
            "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )
        self.assertEqual(hc.canonical_digest({"a": 1}), hc.sha256_digest(b'{"a":1}'))


class NoteRevisionTests(unittest.TestCase):
    def test_line_endings_and_terminal_newline_are_not_content(self) -> None:
        base = note_text("Step one.\nStep two.\n")
        for variant in (
            base.replace("\n", "\r\n"),
            base.replace("\n", "\r"),
            base.rstrip("\n"),
            base + "\n\n\n",
        ):
            with self.subTest(variant=repr(variant[-6:])):
                self.assertEqual(
                    hc.note_revision("procedure", "example", variant, DOC_URL),
                    hc.note_revision("procedure", "example", base, DOC_URL),
                )

    def test_internal_markdown_whitespace_is_content(self) -> None:
        first = hc.note_revision("procedure", "example", note_text("a  b\n\n\nc\n"), DOC_URL)
        second = hc.note_revision("procedure", "example", note_text("a b\n\nc\n"), DOC_URL)
        self.assertNotEqual(first, second)

    def test_server_owned_fields_do_not_change_the_revision(self) -> None:
        authored = note_text("Step one.\n")
        plain = hc.note_revision("procedure", "example", authored, DOC_URL)
        served = note_text(
            "Step one.\n",
            {
                **BASE_META,
                "grade": "stable",
                "revision": REV_A,
                "intake_id": UUID,
                "doc_digest": REV_B,
                "gate_version": hc.GATE_VERSION,
                "gate_digest": REV_C,
                "operator_review": "review-receipt-1",
            },
        )
        self.assertEqual(plain, hc.note_revision("procedure", "example", served, DOC_URL))

    def test_grade_promotion_keeps_the_revision(self) -> None:
        authored = note_text("Step one.\n", {**BASE_META, "grade": "trial"})
        promoted = hc.render_note(authored, {"grade": "stable"})
        self.assertEqual(
            hc.note_revision("procedure", "example", authored, DOC_URL),
            hc.note_revision("procedure", "example", promoted, DOC_URL),
        )
        self.assertEqual(hc.parse_note(promoted)["metadata"]["grade"], "stable")

    def test_key_order_does_not_change_the_revision(self) -> None:
        first = hc.note_revision("procedure", "example", note_text("B\n", dict(BASE_META)), DOC_URL)
        reversed_meta = {key: BASE_META[key] for key in reversed(list(BASE_META))}
        second = hc.note_revision("procedure", "example", note_text("B\n", reversed_meta), DOC_URL)
        self.assertEqual(first, second)

    def test_doc_url_from_argument_survives_being_added_later(self) -> None:
        authored = note_text("Step one.\n")
        with_url = note_text("Step one.\n", {**BASE_META, "doc_url": DOC_URL})
        self.assertEqual(
            hc.note_revision("procedure", "example", authored, DOC_URL),
            hc.note_revision("procedure", "example", with_url, DOC_URL),
        )

    def test_semantic_changes_make_a_new_revision(self) -> None:
        base = note_text("Step one.\n")
        changes = (
            note_text("Step two.\n"),
            note_text("Step one.\n", {**BASE_META, "last_verified": "2026-09-02"}),
            note_text("Step one.\n", {**BASE_META, "name": "other"}),
        )
        plain = hc.note_revision("procedure", "example", base, DOC_URL)
        for changed in changes:
            with self.subTest(changed=changed.splitlines()[1]):
                self.assertNotEqual(plain, hc.note_revision("procedure", "example", changed, DOC_URL))

    def test_revision_shape_and_determinism(self) -> None:
        revision = hc.note_revision("lesson", "example", note_text("C\n"), DOC_URL)
        self.assertTrue(hc.REVISION_RE.fullmatch(revision), revision)
        self.assertEqual(revision, hc.note_revision("lesson", "example", note_text("C\n"), DOC_URL))

    def test_doc_url_mismatch_with_frontmatter_is_rejected(self) -> None:
        text = note_text("Step one.\n", {**BASE_META, "doc_url": "https://docs.example.com/other"})
        with self.assertRaises(ValueError) as caught:
            hc.note_revision("procedure", "example", text, DOC_URL)
        self.assertEqual(str(caught.exception), "doc_url_mismatch")

    def test_rejects_kind_service_and_failure(self) -> None:
        for kind in ("service", "failure", ""):
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError) as caught:
                    hc.note_revision(kind, "example", note_text("A\n"), DOC_URL)
                self.assertEqual(str(caught.exception), "kind_invalid")

    def test_rejects_forbidden_and_malformed_metadata(self) -> None:
        cases = (
            ({**BASE_META, "distributable": False}, "distributable_false"),
            ({**BASE_META, "profile_id": "personal"}, "profile_forbidden"),
            ({**BASE_META, "profile_ids": ["personal"]}, "profile_forbidden"),
            ({**BASE_META, "grade": "preview"}, "grade_invalid"),
            ({**BASE_META, "gate_version": "hub-gates-v0"}, "gate_version_invalid"),
            ({**BASE_META, "intake_id": "NOT-A-UUID"}, "intake_id_invalid"),
            ({**BASE_META, "last_verified": "2026-09-01T00:00:00Z"}, "date_invalid"),
            ({**BASE_META, "revision": "not-a-digest"}, "revision_invalid"),
            ({**BASE_META, "distributable": "yes"}, "distributable_false"),
        )
        for meta, expected in cases:
            with self.subTest(expected=expected, meta=sorted(meta)):
                with self.assertRaises(ValueError) as caught:
                    hc.note_revision("procedure", "example", note_text("A\n", meta), DOC_URL)
                self.assertEqual(str(caught.exception), expected)

    def test_rejects_non_json_metadata_values(self) -> None:
        # The stdlib frontmatter reader yields only strings/bools/lists/dicts, so the
        # type rule is pinned on the validator itself.
        self.assertEqual(hc.validate_note_metadata({"ratio": 1.5}), ["metadata_type_invalid"])
        self.assertEqual(hc.validate_note_metadata({"ratio": None}), ["metadata_type_invalid"])
        self.assertEqual(hc.validate_note_metadata({"tags": ["ok", 1.5]}), ["metadata_type_invalid"])
        self.assertEqual(hc.validate_note_metadata({"tags": ["ok", 2]}), [])

    def test_json_bool_metadata_is_allowed_and_hashed(self) -> None:
        self.assertEqual(hc.validate_note_metadata({"experimental": True}), [])
        self.assertEqual(hc.validate_note_metadata({"tags": ["a", False]}), [])
        without = hc.note_revision("procedure", "example", note_text("A\n"), DOC_URL)
        with_flag = hc.note_revision(
            "procedure", "example", note_text("A\n", {**BASE_META, "experimental": True}), DOC_URL
        )
        self.assertNotEqual(without, with_flag)

    def test_duplicate_frontmatter_keys_are_rejected_before_hashing_or_writing(self) -> None:
        # Later duplicate wins in the reader while an update rewrites the first
        # occurrence, so a repeated grade/revision could be smuggled past the writer.
        duplicated = (
            "---\nname: example\ngrade: trial\nrevision: " + REV_A + "\ngrade: stable\n---\nA.\n"
        )
        with self.assertRaises(ValueError) as caught:
            hc.note_revision("procedure", "example", duplicated, DOC_URL)
        self.assertEqual(str(caught.exception), "duplicate_field")
        with self.assertRaises(ValueError) as caught:
            hc.parse_note(duplicated)
        self.assertEqual(str(caught.exception), "duplicate_field")
        with self.assertRaises(ValueError) as caught:
            hc.render_note(duplicated, {"grade": "trial"})
        self.assertEqual(str(caught.exception), "duplicate_field")
        for text in (
            "---\nname: example\nname: other\n---\nA.\n",
            "---\nname: example\n\nname: other\n---\nA.\n",
        ):
            with self.subTest(text=text):
                with self.assertRaises(ValueError) as caught:
                    hc.parse_note(text)
                self.assertEqual(str(caught.exception), "duplicate_field")
        commented = "---\n# name: example\nname: example\n---\nA.\n"
        self.assertEqual(hc.parse_note(commented)["metadata"]["name"], "example")
        nested = "---\nname: example\nendpoint:\n  base_url: https://a.example\n  base_url: https://b.example\n---\nA.\n"
        self.assertEqual(hc.parse_note(nested)["metadata"]["endpoint"]["base_url"], "https://b.example")

    def test_requires_frontmatter_and_strict_paths(self) -> None:
        with self.assertRaises(ValueError) as caught:
            hc.note_revision("procedure", "example", "no frontmatter\n", DOC_URL)
        self.assertEqual(str(caught.exception), "frontmatter_required")
        with self.assertRaises(ValueError) as caught:
            hc.note_revision("procedure", "Bad_ID", note_text("A\n"), DOC_URL)
        self.assertEqual(str(caught.exception), "service_id_invalid")

    def test_revision_object_shape(self) -> None:
        obj = hc.note_semantics("procedure", "example", note_text("A\n"), DOC_URL)
        self.assertEqual(set(obj), {"kind", "service_id", "metadata", "body", "doc_url"})
        self.assertEqual(obj["body"], "A\n")
        self.assertNotIn("distributable", obj["metadata"])
        self.assertEqual(obj["doc_url"], DOC_URL)


class DocUrlTests(unittest.TestCase):
    def test_accepts_canonical_https_and_explicit_443(self) -> None:
        for url in (DOC_URL, "https://docs.example.com:443/guide", "https://xn--bcher-kva.example/x"):
            with self.subTest(url=url):
                self.assertIsNone(hc.canonical_doc_url_issue(url))

    def test_rejects_non_canonical_urls(self) -> None:
        cases = (
            "http://docs.example.com/x",
            "https://docs.example.com:8443/x",
            "https://user:pw@docs.example.com/x",
            "https://docs.example.com/x?a=1",
            "https://docs.example.com/x#frag",
            "https://docs.example.com/%2e%2e/x",
            "https://93.184.216.34/x",
            "https://[fd00::1]/x",
            "https://internal/x",
            "https://docs.internal/x",
            "https://localhost/x",
            "https://DOCS.example.com/x",
            "https://docs.example.com./x",
            "https://bücher.example/x",
            "https://docs.example.com" + "/x" * 1100,
            "",
            None,
            42,
        )
        for url in cases:
            with self.subTest(url=url):
                self.assertEqual(hc.canonical_doc_url_issue(url), "doc_url_invalid")

    def test_rejects_url_whitespace_and_control_characters(self) -> None:
        for character in (" ", "\t", "\n", "\r", "\x01", "\x7f", "\u00a0"):
            with self.subTest(codepoint=ord(character)):
                self.assertEqual(
                    hc.canonical_doc_url_issue(f"https://docs.example.com/a{character}b"),
                    "doc_url_invalid",
                )


class ParseAndRenderTests(unittest.TestCase):
    def test_parse_normalizes_body_and_keeps_internal_blank_lines(self) -> None:
        parsed = hc.parse_note("---\nname: example\n---\r\nA.\r\n\r\nB.\r\n\r\n")
        self.assertTrue(parsed["has_frontmatter"])
        self.assertEqual(parsed["metadata"]["name"], "example")
        self.assertEqual(parsed["body"], "A.\n\nB.\n")

    def test_parse_without_frontmatter(self) -> None:
        parsed = hc.parse_note("just text\n")
        self.assertFalse(parsed["has_frontmatter"])
        self.assertEqual(parsed["metadata"], {})
        self.assertEqual(parsed["body"], "just text\n")

    def test_render_preserves_untouched_lines_and_round_trips_scalars(self) -> None:
        original = (
            "---\n"
            "# keep this comment\n"
            "name: example\n"
            "\n"
            "tags:\n"
            "  - one\n"
            "doc_url: https://docs.example.com/guide\n"
            "---\n"
            "A.\n"
        )
        rendered = hc.render_note(
            original,
            {"grade": "trial", "revision": REV_A, "distributable": True, "gate_version": hc.GATE_VERSION},
            drop=("operator_review",),
        )
        parsed = hc.parse_note(rendered)
        self.assertEqual(parsed["metadata"]["grade"], "trial")
        self.assertEqual(parsed["metadata"]["revision"], REV_A)
        self.assertIs(parsed["metadata"]["distributable"], True)
        self.assertEqual(parsed["metadata"]["gate_version"], hc.GATE_VERSION)
        self.assertEqual(parsed["metadata"]["name"], "example")
        self.assertEqual(parsed["metadata"]["tags"], ["one"])
        self.assertEqual(parsed["body"], "A.\n")
        self.assertIn("# keep this comment\n", rendered)
        self.assertNotIn("operator_review", rendered)
        self.assertTrue(rendered.endswith("---\nA.\n"))

    def test_render_drop_and_missing_frontmatter(self) -> None:
        text = note_text("A\n", {**BASE_META, "operator_review": "x"})
        self.assertNotIn("operator_review", hc.render_note(text, drop=("operator_review",)))
        with self.assertRaises(ValueError):
            hc.render_note("body only\n", {"grade": "trial"})

    def test_render_only_changes_the_named_keys(self) -> None:
        text = note_text("A\n")
        self.assertEqual(hc.render_note(text, {}), text)


class SubmissionTests(unittest.TestCase):
    def payload(self, **overrides) -> dict:
        base = {
            "request_id": UUID,
            "service_id": "example",
            "kind": "procedure",
            "body": note_text("A\n"),
            "doc_url": DOC_URL,
            "success_evidence": {"action": " ran the cli ", "outcome": " printed OK "},
        }
        base.update(overrides)
        return base

    def test_valid_payload_normalizes_evidence(self) -> None:
        result = hc.validate_submission(self.payload())
        self.assertTrue(result["ok"], result["errors"])
        self.assertIsNone(result["reason_code"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["fields"]["success_evidence"], {"action": "ran the cli", "outcome": "printed OK"})
        self.assertNotIn("note_id", result["fields"])
        self.assertEqual(result["fields"]["kind"], "procedure")

    def test_utf8_byte_limit_is_exact(self) -> None:
        body = "é" * 1024  # 2048 UTF-8 bytes
        self.assertTrue(hc.validate_submission(self.payload(body=body))["ok"])
        too_big = hc.validate_submission(self.payload(body=body + "a"))
        self.assertIn("body:too_large", too_big["errors"])

    def test_structural_rejections(self) -> None:
        cases = (
            (self.payload(extra=1), "unknown_field"),
            (self.payload(kind="service"), "kind:invalid"),
            (self.payload(service_id="Bad_ID"), "service_id:invalid"),
            (self.payload(request_id=UUID.upper()), "request_id:invalid"),
            (self.payload(body=""), "body:empty"),
            (self.payload(body="   \n"), "body:empty"),
            (self.payload(body="ok\x00"), "body:nul_byte"),
            (self.payload(body=7), "body:not_string"),
            (self.payload(doc_url="https://docs.example.com:8443/x"), "doc_url:invalid"),
            (self.payload(doc_url="https://docs.example.com/x?a=1"), "doc_url:invalid"),
            (self.payload(success_evidence={"action": "a"}), "success_evidence:unknown_field"),
            (self.payload(success_evidence={"action": "a", "outcome": "b", "note": "c"}), "success_evidence:unknown_field"),
            (self.payload(success_evidence={"action": "  ", "outcome": "b"}), "success_evidence.action:empty"),
            (self.payload(success_evidence={"action": "a", "outcome": "b" * 2049}), "success_evidence.outcome:too_large"),
            (self.payload(expected_revision=REV_A), "note_id:missing"),
            (self.payload(note_id="service/example"), "expected_revision:missing"),
            (self.payload(note_id="failure/other-thing", expected_revision=REV_A), "note_id:invalid"),
            (self.payload(note_id="service/other"), "note_id:invalid"),
            (self.payload(note_id="service/example", expected_revision="sha256:" + "A" * 64), "expected_revision:invalid"),
        )
        for payload, expected in cases:
            with self.subTest(expected=expected):
                result = hc.validate_submission(payload)
                self.assertFalse(result["ok"])
                self.assertEqual(result["reason_code"], "invalid_params")
                self.assertEqual(result["fields"], {})
                self.assertIn(expected, result["errors"])

    def test_non_object_payload(self) -> None:
        for payload in ([], "text", None, 7):
            with self.subTest(payload=payload):
                self.assertEqual(hc.validate_submission(payload)["errors"], ["payload:not_object"])

    def test_submission_errors_never_echo_input(self) -> None:
        token = "ghp_" + "Z" * 36
        result = hc.validate_submission(self.payload(body=token + "\x00", doc_url="https://docs.example.com/x?t=" + token))
        self.assertFalse(result["ok"])
        self.assertNotIn(token, json.dumps(result))

    def test_matching_note_id_passes(self) -> None:
        result = hc.validate_submission(
            self.payload(note_id="service/example", expected_revision=REV_A)
        )
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["fields"]["note_id"], "service/example")

    def test_lesson_note_id_is_not_tied_to_service_id(self) -> None:
        result = hc.validate_submission(
            self.payload(
                kind="lesson",
                note_id="failure/2026-09-25-example-0f8fad5b-d9cb-469f-a165-70867728950e",
                expected_revision=REV_A,
            )
        )
        self.assertTrue(result["ok"], result["errors"])


class PathTests(unittest.TestCase):
    def test_generated_paths(self) -> None:
        cases = {
            (): "services/example.md",
            ("lesson",): "failures/2026-09-25-example-0f8fad5b-d9cb-469f-a165-70867728950e.md",
        }
        self.assertEqual(hc.note_relative_path("procedure", "example", UUID, CREATED), cases[()])
        self.assertEqual(hc.note_relative_path("lesson", "example", UUID, CREATED), cases[("lesson",)])

    def test_sibling_paths_for_stable_updates(self) -> None:
        self.assertEqual(
            hc.note_relative_path("procedure", "example", UUID, CREATED, stable_sibling=True),
            "trial/services/example.md",
        )
        stem = "2026-08-13-magnific-kling-v2-1-rejects-aspect-ratio"
        self.assertEqual(
            hc.note_relative_path("lesson", "example", UUID, CREATED, stem=stem, stable_sibling=True),
            f"trial/failures/{stem}.md",
        )

    def test_stem_override_and_md_suffix(self) -> None:
        self.assertEqual(
            hc.note_relative_path("lesson", "example", UUID, CREATED, stem="2026-01-01-kept-name.md"),
            "failures/2026-01-01-kept-name.md",
        )

    def test_utc_date_boundary(self) -> None:
        late = int(datetime(2026, 9, 24, 23, 59, 59, tzinfo=timezone.utc).timestamp())
        self.assertIn("failures/2026-09-24-", hc.note_relative_path("lesson", "example", UUID, late))

    def test_rejects_unsafe_arguments(self) -> None:
        cases = (
            ("procedure", "example", UUID, CREATED, {"stem": "x"}),
            ("procedure", "../escape", UUID, CREATED, {}),
            ("procedure", "a/b", UUID, CREATED, {}),
            ("procedure", "x" * 65, UUID, CREATED, {}),
            ("procedure", "Example", UUID, CREATED, {}),
            ("procedure", "example", UUID.upper(), CREATED, {}),
            ("procedure", "example", "not-a-uuid", CREATED, {}),
            ("procedure", "example", UUID, True, {}),
            ("procedure", "example", UUID, 0, {}),
            ("procedure", "example", UUID, -5, {}),
            ("lesson", "example", UUID, CREATED, {"stem": "../escape"}),
            ("lesson", "example", UUID, CREATED, {"stem": "a/b"}),
            ("lesson", "example", UUID, CREATED, {"stem": ".hidden"}),
            ("lesson", "example", UUID, CREATED, {"stem": "UPPER"}),
            ("service", "example", UUID, CREATED, {}),
        )
        for kind, service_id, intake_id, created, kwargs in cases:
            with self.subTest(kind=kind, kwargs=kwargs):
                with self.assertRaises(ValueError):
                    hc.note_relative_path(kind, service_id, intake_id, created, **kwargs)

    def test_note_identity_helpers(self) -> None:
        self.assertEqual(hc.note_id_from_path("services/example.md"), "service/example")
        self.assertEqual(hc.note_id_from_path("failures/2026-01-01-x.md"), "failure/2026-01-01-x")
        self.assertEqual(hc.note_id_from_path("trial/services/example.md"), "service/example")
        self.assertEqual(hc.split_note_id("service/example"), ("procedure", "example"))
        self.assertEqual(hc.split_note_id("failure/2026-01-01-x"), ("lesson", "2026-01-01-x"))
        self.assertEqual(hc.note_id_for("procedure", "example"), "service/example")
        self.assertEqual(hc.note_id_for("lesson", "example", "2026-01-01-x"), "failure/2026-01-01-x")
        for bad in ("services/example", "other/example.md", "services/UPPER.md", "service/example", "services/x.md/x"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    hc.note_id_from_path(bad)
        for bad in ("nope", "service/", "service/UPPER", "/service/x"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    hc.split_note_id(bad)

    def test_contained_path_and_unsafe_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = hc.contained_path(root, "services/example.md")
            self.assertEqual(target, root.resolve() / "services/example.md")
            for relative in ("../escape.md", "/etc/passwd", "services/../../x.md", "a\\b.md", "services/.hidden.md",
                             "services/%2e%2e.md", "services//x.md", "services/./x.md"):
                with self.subTest(relative=relative):
                    self.assertEqual(hc.unsafe_path_reason(root, relative), "path-invalid")
                    with self.assertRaises(ValueError):
                        hc.contained_path(root, relative)
            executable = root / "services" / "example.md"
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_text("x\n", encoding="utf-8")
            executable.chmod(0o755)
            self.assertEqual(hc.unsafe_path_reason(root, "services/example.md"), "path-executable")
            executable.chmod(0o644)
            self.assertIsNone(hc.unsafe_path_reason(root, "services/example.md"))

    def test_symlinked_component_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as outside:
            root = Path(raw)
            (root / "services").symlink_to(Path(outside))
            self.assertEqual(hc.unsafe_path_reason(root, "services/example.md"), "path-symlink")

    def test_submodule_directory_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "services").mkdir()
            (root / "services" / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
            self.assertEqual(hc.unsafe_path_reason(root, "services/example.md"), "path-submodule")


class NoteIndexTests(unittest.TestCase):
    def test_single_note_index_passes(self) -> None:
        result = hc.validate_note_index(index_document([index_note("service/example", "trial", REV_A)]))
        self.assertTrue(result["ok"], result["errors"])
        self.assertFalse(result["inventory_checked"])
        self.assertEqual(result["notes"][0]["note_id"], "service/example")

    def test_stable_and_trial_siblings_share_a_note_id(self) -> None:
        notes = [
            index_note("service/example", "stable", REV_A),
            index_note("service/example", "trial", REV_B),
        ]
        result = hc.validate_note_index(index_document(notes))
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual([note["grade"] for note in result["notes"]], ["stable", "trial"])

    def test_same_note_two_paths_of_the_same_grade_fail(self) -> None:
        notes = [
            index_note("service/example", "trial", REV_A),
            index_note("service/example", "trial", REV_B),
        ]
        result = hc.validate_note_index(index_document(notes))
        self.assertFalse(result["ok"])
        self.assertIn("notes[1]:duplicate_note_grade", result["errors"])

    def test_same_revision_in_both_grades_fails(self) -> None:
        notes = [
            index_note("service/example", "stable", REV_A),
            index_note("service/example", "trial", REV_A),
        ]
        self.assertIn("notes[1]:duplicate_note_revision", hc.validate_note_index(index_document(notes))["errors"])

    def test_shape_rejections(self) -> None:
        cases = (
            ({**index_document([index_note("service/example", "trial", REV_A)]), "schema_version": 2}, "schema_version:invalid"),
            ({**index_document([index_note("service/example", "trial", REV_A)]), "audience": "personal"}, "audience:invalid"),
            ({**index_document([index_note("service/example", "trial", REV_A)]), "commit": "D" * 40}, "commit:invalid"),
            (index_document([]), None),
            ({"schema_version": 1, "audience": "public", "commit": COMMIT}, "notes:missing"),
            (index_document([{"note_id": "service/example"}]), "notes[0]:unknown_field"),
            (index_document([index_note("service/example", "trial", REV_A, size=0)]), "notes[0]:size_invalid"),
            (index_document([index_note("service/example", "trial", "not-a-revision")]), "notes[0]:revision_invalid"),
            (index_document([index_note("service/example", "trial", REV_A, file_digest="x")]), "notes[0]:file_digest_invalid"),
        )
        for payload, expected in cases:
            with self.subTest(expected=expected):
                result = hc.validate_note_index(payload)
                if expected is None:
                    self.assertTrue(result["ok"], result["errors"])
                    continue
                self.assertIn(expected, result["errors"])
        for payload in ([], "text", None):
            with self.subTest(payload=payload):
                self.assertEqual(hc.validate_note_index(payload)["errors"], ["payload:not_object"])

    def test_path_uri_and_kind_consistency(self) -> None:
        note = index_note("service/example", "stable", REV_A)
        wrong_path = {**note, "path": "trial/example/notes/example.md"}
        self.assertIn("notes[0]:path_invalid", hc.validate_note_index(index_document([wrong_path]))["errors"])
        wrong_uri = {**note, "uri": "skill://gisul/personal/stable/example/notes/example.md"}
        self.assertIn("notes[0]:uri_invalid", hc.validate_note_index(index_document([wrong_uri]))["errors"])
        wrong_kind = {**note, "kind": "lesson"}
        self.assertIn("notes[0]:kind_mismatch", hc.validate_note_index(index_document([wrong_kind]))["errors"])
        lesson = index_note("failure/2026-09-25-example-0f8fad5b-d9cb-469f-a165-70867728950e", "trial", REV_A)
        self.assertTrue(hc.validate_note_index(index_document([lesson]))["ok"])

    def test_inventory_cross_check(self) -> None:
        stable = index_note("service/example", "stable", REV_A)
        trial = index_note("service/example", "trial", REV_B)
        inventory = {
            "audience": "public",
            "files": [
                {"path": stable["path"], "digest": stable["file_digest"], "size": stable["size"]},
                {"path": trial["path"], "digest": trial["file_digest"], "size": trial["size"]},
                {"path": "stable/example/SKILL.md", "digest": REV_C, "size": 100},
            ],
            "skills": [
                {
                    "uri": "skill://gisul/commons/stable/example/SKILL.md",
                    "grade": "stable",
                    "note_refs": [{"note_id": "service/example", "revision": REV_A}],
                },
                {
                    "uri": "skill://gisul/commons/trial/example/SKILL.md",
                    "grade": "trial",
                    "note_refs": [{"note_id": "service/example", "revision": REV_B}],
                },
            ],
        }
        index = index_document([stable, trial])
        result = hc.validate_note_index(index, inventory=inventory)
        self.assertTrue(result["ok"], result["errors"])
        self.assertTrue(result["inventory_checked"])

        broken = {
            "audience": "company",
            "files": [{"path": stable["path"], "digest": REV_C, "size": stable["size"]}],
            "skills": [
                {"uri": "skill://gisul/commons/trial/example/SKILL.md", "grade": "stable", "note_refs": []}
            ],
        }
        errors = hc.validate_note_index(index, inventory=broken)["errors"]
        for expected in ("inventory:audience_mismatch", "inventory:note_missing", "index:notes_incomplete",
                         "skill:grade_mismatch", "skill:missing", "skill:note_refs_mismatch"):
            self.assertIn(expected, errors)

    def test_index_errors_never_echo_input(self) -> None:
        token = "ghp_" + "Q" * 36
        note = index_note("service/example", "trial", REV_A)
        payload = index_document([{**note, "path": f"trial/{token}/notes/example.md"}])
        result = hc.validate_note_index(payload)
        self.assertFalse(result["ok"])
        self.assertNotIn(token, json.dumps(result))


class ScreeningTests(unittest.TestCase):
    def test_identifier_patterns_compile_and_skip_classification(self) -> None:
        entries = [
            {"id": "a", "regex": r"\bprivate\b", "flags": "i"},
            {"id": "b", "regex": r"\bclassify\b", "scope": "classification"},
            {"id": "c", "regex": r"\bhost\b"},
        ]
        compiled = hc.identifier_patterns(entries)
        self.assertEqual(hc.screening_issues("classify", identifiers=compiled), [])
        self.assertEqual(hc.screening_issues("PRIVATE host", identifiers=compiled), ["identifier"])

    def test_malformed_identifier_rules_fail_closed(self) -> None:
        for entries in ([{"id": "a", "regex": "("}], [{"id": "a"}], ["not-an-object"], [None]):
            with self.subTest(entries=entries):
                with self.assertRaises(ValueError) as caught:
                    hc.identifier_patterns(entries)
                self.assertEqual(str(caught.exception), "identifier_rule_invalid")

    def test_screening_codes_without_echoing_text(self) -> None:
        secret = "ghp_" + "A" * 36
        private = "/Users/" + "someone" + "/Vault"
        rules = hc.identifier_patterns([{"id": "home", "regex": r"/Users/[a-z]+/Vault"}])
        self.assertEqual(hc.screening_issues(secret, secrets=SECRET_PATTERNS), ["secret"])
        self.assertEqual(hc.screening_issues(private, identifiers=rules), ["identifier"])
        self.assertEqual(hc.screening_issues(f"{secret} {private}", secrets=SECRET_PATTERNS, identifiers=rules),
                         ["secret", "identifier"])
        self.assertEqual(hc.screening_issues("ordinary documentation text", secrets=SECRET_PATTERNS, identifiers=rules), [])
        self.assertEqual(hc.screening_issues(None, secrets=SECRET_PATTERNS), ["not_text"])

    def test_shared_screen_checks_raw_and_one_percent_decode_only(self) -> None:
        from portwright import hub_pipeline

        token = "AK" + "IA" + "A" * 16
        cases = (
            (token, ["secret"]),
            ("%41" + token[1:], ["secret"]),
            ("%2541" + token[1:], []),
            ("ordinary documentation", []),
        )
        for index, (text, expected) in enumerate(cases):
            with self.subTest(case=index):
                self.assertEqual(hc.screening_issues(text, secrets=SECRET_PATTERNS), expected)
                self.assertEqual(hub_pipeline.screening_issues(text), expected)

    def test_public_identifier_policy_catches_generic_private_details(self) -> None:
        policy = json.loads((ROOT / "install" / "identifier-policy.json").read_text())
        rules = hc.identifier_patterns(entry for entry in policy["patterns"] if entry.get("public"))
        cases = ("someone" + "@example.com", "10.0." + "0.5", "server." + "internal",
                 "fd00:" + ":1234", "https://user:pass" + "@docs.example.com")
        for index, text in enumerate(cases):
            with self.subTest(case=index):
                self.assertIn("identifier", hc.screening_issues(text, identifiers=rules))

    def test_home_path_rule_ignores_api_paths_but_keeps_real_homes(self) -> None:
        policy = json.loads((ROOT / "install" / "identifier-policy.json").read_text())
        rules = hc.identifier_patterns(entry for entry in policy["patterns"] if entry["id"] == "absolute-home")
        # Built at runtime so the public export's home-path substitution cannot rewrite them.
        homes = ("run /" + "Users/someone/app", "file:///" + "Users/someone/app", "`/" + "home/someone/.config`",
                 "C:/" + "Users/someone/", "under /" + "users/someone/cache")
        for text in homes:
            with self.subTest(text=text):
                self.assertEqual(hc.screening_issues(text, identifiers=rules), ["identifier"])
        for text in ("POST gmail/v1/" + "users/me/messages/send", "https://api.github.com/" + "users/octocat/repos"):
            with self.subTest(text=text):
                self.assertEqual(hc.screening_issues(text, identifiers=rules), [])


class _FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def read(self) -> bytes:
        return self.payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False


class JevTransportTests(unittest.TestCase):
    def client(self, **kwargs) -> jev.JevClient:
        return jev.JevClient(api_key="test-key-not-real", **kwargs)

    def test_before_send_sees_the_exact_transport_bytes_before_auth(self) -> None:
        captured: list[bytes] = []
        response = _FakeResponse(json.dumps({"answers": {"supported": {"type": "noul", "noul": 0.95}}}).encode())
        with patch.object(jev.urllib.request, "urlopen", return_value=response) as opened:
            judgment = self.client(before_send=captured.append).ask(
                "hub-gate", {"note": "n"}, {"supported": {"type": "noul"}}
            )
        self.assertEqual(judgment.status, "ok")
        self.assertEqual(len(captured), 1)
        request = opened.call_args.args[0]
        self.assertEqual(request.data, captured[0])
        self.assertEqual(json.loads(captured[0]), {"state": {"note": "n"}, "model": jev.MODEL, "questions": {"supported": {"type": "noul"}}})
        self.assertNotIn(b"test-key-not-real", captured[0])

    def test_hook_failure_stops_the_request(self) -> None:
        def refuse(body: bytes) -> None:
            raise RuntimeError("screening refused")

        with patch.object(jev.urllib.request, "urlopen") as opened:
            with self.assertRaises(RuntimeError):
                self.client(before_send=refuse).ask("hub-gate", {"note": "n"}, {"q": {"type": "noul"}})
        opened.assert_not_called()

    def test_fixture_replay_never_calls_the_hook(self) -> None:
        captured: list[bytes] = []
        with tempfile.TemporaryDirectory() as raw:
            fixtures = Path(raw)
            (fixtures / "tag.json").write_text(
                json.dumps({"request": {}, "response": {"answers": {"q": {"type": "noul", "noul": 0.1}}}}),
                encoding="utf-8",
            )
            client = jev.JevClient(fixtures=fixtures, before_send=captured.append)
            judgment = client.ask("tag", {"note": "n"}, {"q": {"type": "noul"}})
        self.assertEqual(judgment.source, "fixture")
        self.assertEqual(captured, [])

    def test_recording_is_impossible_with_a_hook(self) -> None:
        with self.assertRaises(ValueError):
            jev.JevClient(api_key="k", fixtures=Path("/tmp"), record=True, before_send=lambda body: None)
        with tempfile.TemporaryDirectory() as raw:
            with patch.dict(os.environ, {"PORTWRIGHT_JEV_RECORD": "1", jev.FIXTURES_ENV: raw}, clear=False):
                with self.assertRaises(ValueError):
                    jev.JevClient.from_env(before_send=lambda body: None)
        # The guard is on the record switch itself, even when no fixture directory exists.
        with patch.dict(os.environ, {"PORTWRIGHT_JEV_RECORD": "1"}, clear=True):
            with self.assertRaises(ValueError):
                jev.JevClient.from_env(before_send=lambda body: None)

    def test_from_env_hook_survives_without_recording(self) -> None:
        with patch.dict(os.environ, {"PORTWRIGHT_JEV_RECORD": ""}, clear=False):
            client = jev.JevClient.from_env(before_send=lambda body: None)
        self.assertIsNotNone(client.before_send)
        self.assertFalse(client.record)

    def test_no_request_without_credential(self) -> None:
        captured: list[bytes] = []
        with patch.object(jev.urllib.request, "urlopen") as opened:
            judgment = jev.JevClient(before_send=captured.append).ask("hub-gate", {}, {"q": {"type": "noul"}})
        self.assertEqual(judgment.status, "unknown")
        self.assertEqual(captured, [])
        opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
