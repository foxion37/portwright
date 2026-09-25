from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "portwright"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(CLI), *args], capture_output=True, text=True)


def valid_service(service_id: str = "vercel") -> str:
    return "\n".join([
        "---",
        f"id: {service_id}",
        f"display_name: {service_id.title()}",
        'version_tag: "v1"',
        'last_verified: "2026-07-13"',
        "endpoint:",
        "  type: cli",
        f'  server: "{service_id}"',
        "human_steps:",
        "  - 없음",
        "agent_can:",
        "  - 직접 실행",
        "status: active",
        "---",
        "## 한 줄 요약",
        "검증용 Procedure",
        "",
    ])


def valid_lesson(service_id: str = "vercel", slug: str = "known-fix") -> tuple[str, str]:
    name = f"2026-07-13-{service_id}-{slug}.md"
    text = "\n".join([
        "---",
        'date: "2026-07-13"',
        f"service: {service_id}",
        'service_version: "v1"',
        "status: active",
        "---",
        "## 진짜 원인",
        "확인된 설정 오류",
        "",
        "## 해결",
        "올바른 설정을 사용한다.",
        "",
    ])
    return name, text


class ContractInterfaceTests(unittest.TestCase):
    def test_check_validates_nested_types_dates_and_filename_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            services = home / "services"
            services.mkdir()
            broken = valid_service("vercel").replace("type: cli", "type: shell")
            (services / "wrong-name.md").write_text(broken, encoding="utf-8")
            run = run_cli("check", "--home", directory)
        self.assertEqual(run.returncode, 1)
        self.assertIn("endpoint.type", run.stdout)
        self.assertIn("filename", run.stdout)

    def test_check_includes_drafts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            draft = Path(directory) / "services" / "_drafts" / "draft.md"
            draft.parent.mkdir(parents=True)
            draft.write_text("not frontmatter", encoding="utf-8")
            run = run_cli("check", "--home", directory)
        self.assertEqual(run.returncode, 1)
        self.assertIn("services/_drafts/draft.md", run.stdout)

    def test_service_template_matches_v1_contract_language(self) -> None:
        template = (ROOT / "services" / "_TEMPLATE.md").read_text(encoding="utf-8")
        self.assertIn("api | cli | mcp | oauth", template)
        self.assertNotIn("감사로그에서 자동 갱신", template)
        self.assertNotIn("게이트웨이로 마지막으로 성공", template)

    def test_check_rejects_symlink_note_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            home = Path(directory)
            (home / "services").symlink_to(outside, target_is_directory=True)
            run = run_cli("check", "--home", directory)
        self.assertEqual(run.returncode, 1)
        self.assertIn("symlink note directories", run.stdout)


class PreflightInterfaceTests(unittest.TestCase):
    def test_preflight_resolves_frontmatter_linked_lessons(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "services").mkdir()
            (home / "failures").mkdir()
            (home / "services" / "vercel.md").write_text(valid_service(), encoding="utf-8")
            name, lesson = valid_lesson()
            (home / "failures" / name).write_text(lesson, encoding="utf-8")
            run = run_cli("preflight", "vercel", "--json", "--home", directory)
        self.assertEqual(run.returncode, 0)
        decision = json.loads(run.stdout)
        self.assertEqual(decision["state"], "ready")
        self.assertEqual(decision["procedure"], "services/vercel.md")
        self.assertEqual(decision["active_lessons"], [f"failures/{name}"])

    def test_preflight_cache_miss_is_derive_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = run_cli("preflight", "github", "--json", "--home", directory)
        self.assertEqual(run.returncode, 0)
        self.assertEqual(json.loads(run.stdout)["state"], "derive-required")

    def test_instruct_always_requires_current_docs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "services").mkdir()
            (home / "services" / "vercel.md").write_text(valid_service(), encoding="utf-8")
            run = run_cli("preflight", "vercel", "--intent", "instruct", "--json", "--home", directory)
        self.assertEqual(json.loads(run.stdout)["state"], "verify-required")

    def test_invalid_procedure_is_never_reported_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "services").mkdir()
            (home / "services" / "vercel.md").write_text("broken", encoding="utf-8")
            run = run_cli("preflight", "vercel", "--json", "--home", directory)
        decision = json.loads(run.stdout)
        self.assertEqual(decision["state"], "cache-invalid")
        self.assertEqual(decision["invalid_entries"], ["services/vercel.md"])

    def test_preflight_rejects_symlink_note_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            home = Path(directory)
            outside_path = Path(outside)
            (outside_path / "vercel.md").write_text(valid_service(), encoding="utf-8")
            (home / "services").symlink_to(outside, target_is_directory=True)
            run = run_cli("preflight", "vercel", "--json", "--home", directory)
        decision = json.loads(run.stdout)
        self.assertEqual(decision["state"], "cache-invalid")


class MemoryLifecycleInterfaceTests(unittest.TestCase):
    def test_draft_creation_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = run_cli("memory", "draft", "procedure", "figma", "--home", directory)
            second = run_cli("memory", "draft", "procedure", "figma", "--home", directory)
        self.assertIn("DRAFT CREATED", first.stdout)
        self.assertIn("DRAFT EXISTS", second.stdout)

    def test_unconfirmed_lesson_cannot_be_promoted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            created = run_cli("memory", "draft", "lesson", "vercel", "known-fix", "--home", directory)
            self.assertEqual(created.returncode, 0)
            # 초안 파일명은 실행 당일 날짜로 만들어진다 — 날짜를 박아 두면
            # 그 날 하루만 통과하고 다음 날부터 "draft file does not exist" 로 깨진다.
            draft = f"failures/_drafts/{date.today().isoformat()}-vercel-known-fix.md"
            promoted = run_cli(
                "memory", "promote",
                draft,
                "--home", directory,
            )
        self.assertEqual(promoted.returncode, 1)
        self.assertIn("unconfirmed", promoted.stdout)

    def test_common_secret_formats_block_promotion(self) -> None:
        name, lesson = valid_lesson()
        secret_lines = (
            "Authorization: Bearer sample-token-value-12345",
            "password=sample-password",
            "postgres://user:sample-password@localhost/db",
            "AIza00000000000000000000000000000000000",
            "glpat-00000000000000000000",
            "pypi-00000000000000000000",
            "AccountKey=00000000000000000000000000000000",
        )
        for secret_line in secret_lines:
            with self.subTest(secret_line=secret_line), tempfile.TemporaryDirectory() as directory:
                draft = Path(directory) / "failures" / "_drafts" / name
                draft.parent.mkdir(parents=True)
                draft.write_text(f"{lesson}\n{secret_line}\n", encoding="utf-8")
                run = run_cli("memory", "review", f"failures/_drafts/{name}", "--home", directory)
            self.assertEqual(run.returncode, 1)
            self.assertIn("suspected secret", run.stdout)

    def test_nested_drafts_directory_is_not_promotable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            draft = Path(directory) / "services" / "nested" / "_drafts" / "vercel.md"
            draft.parent.mkdir(parents=True)
            draft.write_text(valid_service(), encoding="utf-8")
            run = run_cli("memory", "review", "services/nested/_drafts/vercel.md", "--home", directory)
        self.assertEqual(run.returncode, 1)
        self.assertIn("directly under", run.stdout)

    def test_promote_moves_lesson_and_links_procedure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "services").mkdir()
            (home / "failures" / "_drafts").mkdir(parents=True)
            procedure = home / "services" / "vercel.md"
            procedure.write_text(valid_service(), encoding="utf-8")
            name, lesson = valid_lesson()
            lesson = lesson.replace("status: active", "status: active\ndistributable: true")
            draft = home / "failures" / "_drafts" / name
            draft.write_text(lesson, encoding="utf-8")
            run = run_cli("memory", "promote", f"failures/_drafts/{name}", "--home", directory)
            destination = home / "failures" / name
            destination_exists = destination.is_file()
            updated = procedure.read_text(encoding="utf-8")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertTrue(destination_exists)
        self.assertIn(f"failures/{name}", updated)

    def test_draft_creation_refuses_symlink_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            home = Path(directory)
            (home / "services").mkdir()
            (home / "services" / "_drafts").symlink_to(outside, target_is_directory=True)
            run = run_cli("memory", "draft", "procedure", "figma", "--home", directory)
            outside_entries = list(Path(outside).iterdir())
        self.assertEqual(run.returncode, 1)
        self.assertIn("outside the Portwright root", run.stdout)
        self.assertEqual(outside_entries, [])


class ClientManagerInterfaceTests(unittest.TestCase):
    def test_codex_install_is_idempotent_and_remove_preserves_user_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            user_home = Path(directory)
            agents = user_home / ".codex" / "AGENTS.md"
            agents.parent.mkdir(parents=True)
            agents.write_text("user rule\n", encoding="utf-8")
            first = run_cli("client", "install", "codex", "--home", str(ROOT), "--user-home", directory)
            second = run_cli("client", "install", "codex", "--home", str(ROOT), "--user-home", directory)
            installed = agents.read_text(encoding="utf-8")
            removed = run_cli("client", "remove", "codex", "--home", str(ROOT), "--user-home", directory)
            final = agents.read_text(encoding="utf-8")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(installed.count("<!-- portwright:start"), 1)
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertEqual(final, "user rule\n")

    def test_installed_block_uses_the_selected_portwright_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            user_home = Path(directory)
            run = run_cli("client", "install", "hermes", "--home", str(ROOT), "--user-home", directory)
            installed = (user_home / ".hermes" / "SOUL.md").read_text(encoding="utf-8")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn(f"{ROOT}/bin/portwright preflight", installed)
        self.assertNotIn("{{PORTWRIGHT_HOME}}", installed)

    def test_new_managed_clients_are_idempotent_and_preserve_user_text(self) -> None:
        clients = (
            ("opencode", ".config/opencode/AGENTS.md", "user rule\n"),
            ("oh-my-pi", ".omp/agent/AGENTS.md", "user rule\n"),
            (
                "vscode",
                ".copilot/instructions/portwright.instructions.md",
                '---\napplyTo: "**"\n---\n\nuser rule\n',
            ),
        )
        for client_id, relative_target, original in clients:
            with self.subTest(client=client_id), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / relative_target
                target.parent.mkdir(parents=True)
                target.write_text(original, encoding="utf-8")
                first = run_cli(
                    "client", "install", client_id,
                    "--home", str(ROOT), "--user-home", directory,
                )
                self.assertEqual(first.returncode, 0, first.stderr)
                second = run_cli(
                    "client", "install", client_id,
                    "--home", str(ROOT), "--user-home", directory,
                )
                self.assertEqual(second.returncode, 0, second.stderr)
                installed = target.read_text(encoding="utf-8")
                removed = run_cli(
                    "client", "remove", client_id,
                    "--home", str(ROOT), "--user-home", directory,
                )
                final = target.read_text(encoding="utf-8")
                self.assertEqual(installed.count("<!-- portwright:start"), 1)
                self.assertEqual(removed.returncode, 0, removed.stderr)
                self.assertEqual(final, original)

    def test_vscode_install_creates_an_always_applied_profile_instruction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = run_cli(
                "client", "install", "vscode",
                "--home", str(ROOT), "--user-home", directory,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            instruction = (
                Path(directory)
                / ".copilot"
                / "instructions"
                / "portwright.instructions.md"
            ).read_text(encoding="utf-8")
        self.assertTrue(instruction.startswith('---\napplyTo: "**"\n---\n\n'))
        self.assertEqual(instruction.count("<!-- portwright:start"), 1)

    def test_vscode_install_refuses_an_existing_file_without_required_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = (
                Path(directory)
                / ".copilot"
                / "instructions"
                / "portwright.instructions.md"
            )
            target.parent.mkdir(parents=True)
            target.write_text("keep this user text\n", encoding="utf-8")
            run = run_cli(
                "client", "install", "vscode",
                "--home", str(ROOT), "--user-home", directory,
            )
            preserved = target.read_text(encoding="utf-8")
        self.assertEqual(run.returncode, 2)
        self.assertIn("required preamble", run.stderr)
        self.assertEqual(preserved, "keep this user text\n")

    def test_claude_install_refuses_broken_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / ".claude" / "settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text("{broken", encoding="utf-8")
            run = run_cli("client", "install", "claude-code", "--home", str(ROOT), "--user-home", directory)
        self.assertEqual(run.returncode, 2)
        self.assertIn("invalid JSON", run.stderr)

    def test_claude_install_refuses_wrong_hook_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / ".claude" / "settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text('{"hooks": []}', encoding="utf-8")
            run = run_cli("client", "install", "claude-code", "--home", str(ROOT), "--user-home", directory)
        self.assertEqual(run.returncode, 2)
        self.assertIn("expected 'hooks'", run.stderr)

    def test_claude_remove_refuses_wrong_hook_shape_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / ".claude" / "settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text('{"hooks": []}', encoding="utf-8")
            run = run_cli("client", "remove", "claude-code", "--home", str(ROOT), "--user-home", directory)
        self.assertEqual(run.returncode, 2)
        self.assertIn("expected 'hooks'", run.stderr)
        self.assertNotIn("Traceback", run.stderr)

    def test_install_collapses_duplicate_managed_blocks(self) -> None:
        block = (ROOT / "install" / "snippets" / "portwright.block.md").read_text(encoding="utf-8")
        block = block.replace("{{PORTWRIGHT_HOME}}", str(ROOT))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / ".hermes" / "SOUL.md"
            target.parent.mkdir(parents=True)
            target.write_text(f"user\n{block}\n{block}\n", encoding="utf-8")
            run = run_cli("client", "install", "hermes", "--home", str(ROOT), "--user-home", directory)
            updated = target.read_text(encoding="utf-8")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(updated.count("<!-- portwright:start"), 1)
        self.assertIn("user", updated)

    def test_install_refuses_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            user_home = Path(directory)
            outside = user_home / "outside.md"
            outside.write_text("keep\n", encoding="utf-8")
            target = user_home / ".hermes" / "SOUL.md"
            target.parent.mkdir(parents=True)
            target.symlink_to(outside)
            run = run_cli("client", "install", "hermes", "--home", str(ROOT), "--user-home", directory)
            preserved = outside.read_text(encoding="utf-8")
        self.assertEqual(run.returncode, 2)
        self.assertIn("symlink", run.stderr)
        self.assertEqual(preserved, "keep\n")

    def test_install_refuses_symlink_skill_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            user_home = Path(directory)
            codex = user_home / ".codex"
            codex.mkdir()
            (codex / "skills").symlink_to(outside, target_is_directory=True)
            run = run_cli("client", "install", "codex", "--home", str(ROOT), "--user-home", directory)
        self.assertEqual(run.returncode, 2)
        self.assertIn("symlink skill root", run.stderr)

    def test_cursor_reports_only_the_human_only_step(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = run_cli("client", "install", "cursor", "--home", str(ROOT), "--user-home", directory)
        self.assertEqual(run.returncode, 0)
        self.assertIn("ACTION-REQUIRED", run.stdout)
        self.assertIn("Cursor Settings", run.stdout)
        self.assertIn(f"{ROOT}/bin/portwright preflight", run.stdout)
        self.assertNotIn("{{PORTWRIGHT_HOME}}", run.stdout)

    def test_doctor_returns_failure_when_client_is_not_installed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = run_cli("client", "doctor", "codex", "--home", str(ROOT), "--user-home", directory)
        self.assertEqual(run.returncode, 1)
        self.assertIn("NOT-INSTALLED", run.stdout)


if __name__ == "__main__":
    unittest.main()
