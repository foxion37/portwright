#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def assert_contains_any(
    test_case: unittest.TestCase,
    text: str,
    options: tuple[str, ...],
    message: str,
) -> None:
    lowered = text.lower()
    if not any(option.lower() in lowered for option in options):
        test_case.fail(f"{message}\nExpected one of: {options}")


def assert_heading(
    test_case: unittest.TestCase,
    text: str,
    patterns: tuple[str, ...],
    message: str,
) -> None:
    if not any(re.search(pattern, text, re.MULTILINE) for pattern in patterns):
        test_case.fail(f"{message}\nExpected one of: {patterns}")


def extract_trigger_line(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if not stripped:
            continue
        if "use when" in lowered or lowered.startswith("trigger") or "when to use" in lowered:
            return stripped
    return None


class ReadmePackagingTests(unittest.TestCase):
    def test_readmes_are_bilingual_beginner_guides_with_cover(self) -> None:
        english_path = ROOT / "README.md"
        korean_path = ROOT / "README_KR.md"

        self.assertTrue(english_path.is_file(), f"Missing file: {english_path}")
        self.assertTrue(korean_path.is_file(), f"Missing file: {korean_path}")

        english = read_text(english_path)
        korean = read_text(korean_path)

        self.assertIn(
            "assets/portwright-cover.png",
            english,
            "README.md should reference the packaged cover asset.",
        )
        self.assertIn(
            "assets/portwright-cover.png",
            korean,
            "README_KR.md should reference the packaged cover asset.",
        )

        self.assertIn(
            "README_KR.md",
            english,
            "README.md should point readers to the Korean companion file.",
        )
        self.assertIn(
            "README.md",
            korean,
            "README_KR.md should point readers to the English companion file.",
        )

        assert_heading(
            self,
            english,
            (r"^##\s+Install\b", r"^##\s+Installation\b"),
            "README.md should have an install section for beginners.",
        )
        assert_heading(
            self,
            english,
            (r"^##\s+Use\b", r"^##\s+Usage\b", r"^##\s+How To Use\b"),
            "README.md should have a use/usage section for beginners.",
        )
        assert_heading(
            self,
            korean,
            (r"^##\s+설치\b", r"^##\s+설치 방법\b"),
            "README_KR.md should have an install section for beginners.",
        )
        assert_heading(
            self,
            korean,
            (r"^##\s+사용\b", r"^##\s+사용 방법\b"),
            "README_KR.md should have a use/usage section for beginners.",
        )

        assert_contains_any(
            self,
            english,
            (
                "beginner",
                "non-developer",
                "nondeveloper",
                "no coding required",
                "you do not need to code",
                "step by step",
            ),
            "README.md should explicitly reassure beginners or non-developers.",
        )
        assert_contains_any(
            self,
            korean,
            (
                "비개발자",
                "초보",
                "코드를 몰라도",
                "코딩이 없어도",
                "차근차근",
            ),
            "README_KR.md should explicitly reassure beginners or non-developers.",
        )


class SkillsPackagingTests(unittest.TestCase):
    def test_split_skills_and_codex_adapter_are_packaged(self) -> None:
        use_skill_path = ROOT / "skills" / "portwright-tool-use" / "SKILL.md"
        memory_skill_path = ROOT / "skills" / "portwright-tool-memory" / "SKILL.md"
        codex_adapter_path = ROOT / "install" / "adapters" / "codex.md"

        self.assertTrue(
            use_skill_path.is_file(),
            f"Missing packaged use skill: {use_skill_path}",
        )
        self.assertTrue(
            memory_skill_path.is_file(),
            f"Missing packaged memory skill: {memory_skill_path}",
        )

        use_skill = read_text(use_skill_path)
        memory_skill = read_text(memory_skill_path)
        codex_adapter = read_text(codex_adapter_path)

        use_trigger = extract_trigger_line(use_skill)
        memory_trigger = extract_trigger_line(memory_skill)
        self.assertIsNotNone(
            use_trigger,
            "Use skill should declare a trigger or 'Use when' line.",
        )
        self.assertIsNotNone(
            memory_trigger,
            "Memory skill should declare a trigger or 'Use when' line.",
        )
        self.assertNotEqual(
            use_trigger,
            memory_trigger,
            "The two split skills should not share the same trigger description.",
        )

        assert_contains_any(
            self,
            memory_skill,
            ("draft first", "write a draft first", "prepare a draft"),
            "Memory skill should enforce a draft-first workflow.",
        )
        assert_contains_any(
            self,
            memory_skill,
            ("redact", "redaction", "sanitize", "mask"),
            "Memory skill should mention redaction or sanitization.",
        )
        assert_contains_any(
            self,
            memory_skill,
            (
                "no secrets",
                "never include secrets",
                "do not store secrets",
                "do not write secrets",
            ),
            "Memory skill should forbid storing secrets.",
        )

        assert_contains_any(
            self,
            use_skill,
            (
                "read existing memory",
                "consult existing memory",
                "read the cached procedure",
                "read the existing memory",
            ),
            "Use skill should say it reads existing memory before acting.",
        )
        assert_contains_any(
            self,
            use_skill,
            (
                "do not write durable memory",
                "don't write durable memory",
                "must not write durable memory",
                "read-only with respect to memory",
            ),
            "Use skill should say it does not write durable memory.",
        )

        self.assertIn(
            "portwright-tool-use",
            codex_adapter,
            "install/adapters/codex.md should mention the packaged use skill.",
        )
        self.assertIn(
            "portwright-tool-memory",
            codex_adapter,
            "install/adapters/codex.md should mention the packaged memory skill.",
        )


class CanonicalPathPackagingTests(unittest.TestCase):
    def test_install_surfaces_use_tools_canonical_path(self) -> None:
        files = (
            ROOT / "bin" / "portwright",
            ROOT / "install" / "README.md",
            ROOT / "install" / "adapters" / "claude-code.md",
            ROOT / "install" / "adapters" / "codex.md",
            ROOT / "install" / "adapters" / "cursor.md",
            ROOT / "install" / "adapters" / "gemini-cli.md",
            ROOT / "install" / "adapters" / "hermes.md",
            ROOT / "install" / "portwright-reminder.sh",
            ROOT / "install" / "snippets" / "portwright.block.md",
            ROOT / "skills" / "portwright-tool-use" / "SKILL.md",
            ROOT / "skills" / "portwright-tool-memory" / "SKILL.md",
        )

        combined = "\n".join(read_text(path) for path in files)
        old_home = "developer/" + "projects/portwright"
        canonical_home = "developer/" + "tools/portwright"
        self.assertNotIn(
            old_home,
            combined,
            "Packaged install surfaces should not point at the old projects path.",
        )
        self.assertIn(
            canonical_home,
            combined,
            "Packaged install surfaces should point at the canonical tools path.",
        )


class RepoContractPackagingTests(unittest.TestCase):
    def test_memory_templates_match_v1_frontmatter_contract(self) -> None:
        memory_policy = read_text(
            ROOT
            / "skills"
            / "portwright-tool-memory"
            / "references"
            / "memory-policy.md"
        )

        old_connection_key = "connection" + ":"
        draft_status = "status: " + "draft"
        self.assertNotIn(
            old_connection_key,
            memory_policy,
            "Memory draft templates should use endpoint, not the old connection key.",
        )
        self.assertIn(
            "endpoint:",
            memory_policy,
            "Memory draft templates should match services/_TEMPLATE.md.",
        )
        self.assertNotIn(
            draft_status,
            memory_policy,
            "Draft templates should stay schema-compatible and use active|stale frontmatter status.",
        )

    def test_use_skill_does_not_wire_future_policy_or_allowlist(self) -> None:
        use_skill = read_text(ROOT / "skills" / "portwright-tool-use" / "SKILL.md")
        checklist = read_text(
            ROOT
            / "skills"
            / "portwright-tool-use"
            / "references"
            / "use-checklist.md"
        )
        combined = f"{use_skill}\n{checklist}"
        future_policy_path = "policy/" + "tiers.yaml"
        future_allowlist_path = "allowlist/" + "users.yaml"

        self.assertNotIn(
            future_policy_path,
            combined,
            "Packaged v1 use skill should not wire FUTURE-scoped policy tiers.",
        )
        self.assertNotIn(
            future_allowlist_path,
            combined,
            "Packaged v1 use skill should not wire FUTURE-scoped allowlist checks.",
        )


def extract_string_collection(source: str, name: str) -> set[str]:
    """Parse a flat list/set literal of strings assigned to `name` in source."""
    match = re.search(
        re.escape(name) + r"\s*=\s*[\[{](?P<body>[^\]}]*)[\]}]",
        source,
    )
    if match is None:
        return set()
    return set(re.findall(r"""['"]([^'"]+)['"]""", match.group("body")))


class SchemaLintParityTests(unittest.TestCase):
    def test_endpoint_type_enum_matches_service_schema(self) -> None:
        schema = json.loads(
            read_text(ROOT / "install" / "schema" / "service.schema.json")
        )
        schema_enum = set(
            schema["properties"]["endpoint"]["properties"]["type"]["enum"]
        )
        self.assertEqual(
            schema_enum,
            {"api", "cli", "mcp", "oauth"},
            "endpoint.type enum should be the v1.1 contract set "
            "['api', 'cli', 'mcp', 'oauth'].",
        )

    def test_service_required_keys_match_service_schema(self) -> None:
        schema = json.loads(
            read_text(ROOT / "install" / "schema" / "service.schema.json")
        )
        self.assertEqual(
            set(schema["required"]),
            {"id", "display_name", "version_tag", "last_verified", "endpoint", "human_steps", "agent_can", "status"},
        )

    def test_failure_required_keys_match_failure_schema(self) -> None:
        schema = json.loads(
            read_text(ROOT / "install" / "schema" / "failure.schema.json")
        )
        self.assertEqual(
            set(schema["required"]),
            {"date", "service", "service_version", "status"},
        )


class AdapterPackagingTests(unittest.TestCase):
    ADAPTERS = (
        "claude-code",
        "codex",
        "cursor",
        "gemini-cli",
        "hermes",
        "oh-my-pi",
        "opencode",
        "vscode",
    )
    # claude-code wires a hook instead of a pasted block, so every other
    # adapter must document the portwright:start managed-block marker.
    MARKER_ADAPTERS = (
        "codex",
        "cursor",
        "gemini-cli",
        "hermes",
        "oh-my-pi",
        "opencode",
        "vscode",
    )

    def test_all_adapters_exist_and_use_canonical_path(self) -> None:
        canonical_home = "developer/" + "tools/portwright"
        for name in self.ADAPTERS:
            path = ROOT / "install" / "adapters" / f"{name}.md"
            with self.subTest(adapter=name):
                self.assertTrue(path.is_file(), f"Missing packaged adapter: {path}")
                self.assertIn(
                    canonical_home,
                    read_text(path),
                    f"install/adapters/{name}.md should use the canonical "
                    "tools path.",
                )

    def test_managed_block_adapters_document_start_marker(self) -> None:
        for name in self.MARKER_ADAPTERS:
            path = ROOT / "install" / "adapters" / f"{name}.md"
            with self.subTest(adapter=name):
                self.assertTrue(path.is_file(), f"Missing packaged adapter: {path}")
                self.assertIn(
                    "portwright:start",
                    read_text(path),
                    f"install/adapters/{name}.md should describe the "
                    "portwright:start managed-block marker.",
                )


class LintStrictContractTests(unittest.TestCase):
    def test_lint_wires_strict_flag_and_message(self) -> None:
        run = subprocess.run(
            [str(ROOT / "bin" / "portwright"), "lint", "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(run.returncode, 0)
        self.assertIn(
            "--strict",
            run.stdout,
            "bin/portwright lint should accept a --strict flag.",
        )

    def test_strict_fails_stale_note_and_default_passes(self) -> None:
        # Behavioral contract: run the real CLI against a synthetic home.
        stale_note = "\n".join(
            [
                "---",
                "id: dummy",
                "display_name: Dummy",
                'version_tag: "v1"',
                'last_verified: "2026-06-12"',
                "endpoint:",
                "  type: cli",
                '  server: "dummy-cli"',
                "human_steps:",
                "  - none",
                "agent_can:",
                "  - everything",
                "status: stale",
                "---",
                "## 한 줄 요약",
                "test",
                "",
            ]
        )
        with tempfile.TemporaryDirectory() as home:
            services = Path(home) / "services"
            services.mkdir()
            (services / "dummy.md").write_text(stale_note, encoding="utf-8")

            default_run = subprocess.run(
                [str(ROOT / "bin" / "portwright"), "lint", "--home", home],
                capture_output=True,
                text=True,
            )
            strict_run = subprocess.run(
                [
                    str(ROOT / "bin" / "portwright"),
                    "lint",
                    "--strict",
                    "--home",
                    home,
                ],
                capture_output=True,
                text=True,
            )

        self.assertEqual(
            default_run.returncode,
            0,
            f"default lint should PASS a stale note: {default_run.stdout}",
        )
        self.assertEqual(
            strict_run.returncode,
            1,
            f"--strict lint should FAIL a stale note: {strict_run.stdout}",
        )
        self.assertIn("strict mode", strict_run.stdout)

    def test_dangling_home_flag_errors_instead_of_hanging(self) -> None:
        run = subprocess.run(
            [str(ROOT / "bin" / "portwright"), "lint", "--strict", "--home"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(run.returncode, 2)
        self.assertIn("argument --home: expected one argument", run.stderr)


def load_suite(case: str) -> unittest.TestSuite:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    if case in ("all", "readme"):
        suite.addTests(loader.loadTestsFromTestCase(ReadmePackagingTests))
    if case in ("all", "skills"):
        suite.addTests(loader.loadTestsFromTestCase(SkillsPackagingTests))
    if case in ("all", "paths"):
        suite.addTests(loader.loadTestsFromTestCase(CanonicalPathPackagingTests))
    if case in ("all", "contract"):
        suite.addTests(loader.loadTestsFromTestCase(RepoContractPackagingTests))
    if case in ("all", "parity"):
        suite.addTests(loader.loadTestsFromTestCase(SchemaLintParityTests))
    if case in ("all", "adapters"):
        suite.addTests(loader.loadTestsFromTestCase(AdapterPackagingTests))
    if case in ("all", "strict"):
        suite.addTests(loader.loadTestsFromTestCase(LintStrictContractTests))

    return suite


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        choices=(
            "all",
            "readme",
            "skills",
            "paths",
            "contract",
            "parity",
            "adapters",
            "strict",
        ),
        default="all",
        help="Run a focused packaging case or the full suite.",
    )
    args = parser.parse_args(argv)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(load_suite(args.case))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())


class OptionalFrontmatterTests(unittest.TestCase):
    """A template's empty optional key must mean absent, not an invalid value."""

    def test_empty_optional_field_validates(self) -> None:
        sys.path.insert(0, str(ROOT / "lib"))
        from portwright.contracts import ContractCatalog

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "failures").mkdir()
            note = root / "failures" / "2026-09-22-example-thing.md"
            note.write_text(
                "---\n"
                "date: 2026-09-22\n"
                "service: example\n"
                "service_version: cli 1.0\n"
                "status: active\n"
                "profile_id:\n"
                "distributable: true\n"
                "---\n\n"
                "## 무엇을 시도했나\n본문\n\n"
                "## 어떤 에러가 났나\n본문\n\n"
                "## 진짜 원인\n본문\n\n"
                "## 고친 방법 (다음엔 이대로)\n본문\n",
                encoding="utf-8",
            )
            result = ContractCatalog(root).validate_note(note)
        self.assertEqual([issue.render() for issue in result.issues], [])
        self.assertIn(result.data.get("profile_id"), (None, []))
