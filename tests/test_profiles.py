from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests._v2 import preflight_json, run_cli, write_profile, write_service


class ProfileSwitchTests(unittest.TestCase):
    """Two profiles with different GitHub accounts and databases, alternated from one workstation."""

    def _home(self, directory: str) -> tuple[Path, Path, Path]:
        home = Path(directory)
        alpha = home / "work" / "alpha"
        beta = home / "work" / "beta"
        alpha.mkdir(parents=True)
        beta.mkdir(parents=True)
        write_profile(home, "alpha", alpha, github="org-alpha", db="d1:alpha", services=("cloudflare",), tier="confirm", env_kind="infisical", env_project="alpha-env")
        write_profile(home, "beta", beta, github="me-beta", db="postgres:beta", services=("cloudflare",), tier="auto", env_kind="1password", env_project="beta-vault")
        write_service(home, "cloudflare")
        (home / "install").mkdir()
        (home / "install" / "tiers.json").write_text('{"rules": []}', encoding="utf-8")
        return home, alpha, beta

    def test_switching_cwd_returns_the_matching_account_env_and_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, alpha, beta = self._home(directory)
            for cwd, expected in ((alpha, ("alpha", "org-alpha", "infisical", "alpha-env", "d1:alpha")), (beta, ("beta", "me-beta", "1password", "beta-vault", "postgres:beta")), (alpha, ("alpha", "org-alpha", "infisical", "alpha-env", "d1:alpha"))):
                decision = preflight_json("cloudflare", "--cwd", str(cwd / "src"), "--home", str(home))
                profile = decision["profile"]
                self.assertEqual(profile["id"], expected[0])
                self.assertEqual(profile["github_account"], expected[1])
                self.assertEqual(profile["env_source"]["kind"], expected[2])
                self.assertEqual(profile["env_source"]["project"], expected[3])
                self.assertEqual(profile["databases"], [expected[4]])
                self.assertEqual(decision["procedure"], "services/cloudflare.md")
                self.assertEqual(decision["state"], "ready")

    def test_explicit_profile_overrides_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, alpha, _ = self._home(directory)
            decision = preflight_json("cloudflare", "--cwd", str(alpha), "--profile", "beta", "--home", str(home))
            self.assertEqual(decision["profile"]["id"], "beta")
            self.assertEqual(decision["rationale"]["profile"], "selected with --profile")

    def test_ambiguous_cwd_is_routed_by_synthetic_jev_choice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, _, _ = self._home(directory)
            decision = preflight_json("cloudflare", "--cwd", directory, "--home", str(home))
            self.assertEqual(decision["profile"]["id"], "beta")
            self.assertIn("JEV choice confidence 0.90", decision["rationale"]["profile"])

    def test_low_confidence_choice_leaves_profile_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            write_profile(home, "gamma", home / "g", github="g", db=None, services=("vercel",))
            write_profile(home, "delta", home / "d", github="d", db=None, services=("vercel",))
            write_service(home, "vercel")
            decision = preflight_json("vercel", "--cwd", directory, "--home", str(home))
            self.assertIsNone(decision["profile"])
            self.assertIn("ambiguous among delta, gamma", decision["rationale"]["profile"])

    def test_missing_fixture_and_credential_never_invent_a_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as empty:
            home, _, _ = self._home(directory)
            decision = preflight_json("cloudflare", "--cwd", directory, "--home", str(home), fixtures=Path(empty))
            self.assertIsNone(decision["profile"])
            self.assertIn("fixture missing", decision["rationale"]["profile"])
            decision = preflight_json("cloudflare", "--cwd", directory, "--home", str(home), fixtures=None)
            self.assertIsNone(decision["profile"])
            self.assertIn("credential missing", decision["rationale"]["profile"])

    def test_procedure_bound_to_another_profile_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, alpha, _ = self._home(directory)
            write_service(home, "cloudflare", extra="profile_ids:\n  - beta")
            decision = preflight_json("cloudflare", "--cwd", str(alpha), "--home", str(home))
            self.assertEqual(decision["state"], "cache-invalid")
            self.assertTrue(any("bound to beta" in entry for entry in decision["invalid_entries"]))

    def test_private_procedure_is_found_and_check_validates_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, alpha, _ = self._home(directory)
            write_service(home, "secretsvc", private=True, extra="distributable: false")
            decision = preflight_json("secretsvc", "--cwd", str(alpha), "--home", str(home))
            self.assertEqual(decision["procedure"], "services/_private/secretsvc.md")
            (home / "profiles" / "broken.md").write_text("---\nid: broken\nproject_path: relative/path\n---\n", encoding="utf-8")
            run = run_cli("check", "--home", str(home))
            self.assertEqual(run.returncode, 1)
            self.assertIn("profiles/broken.md", run.stdout)
            self.assertIn("project_path", run.stdout)

    def test_unresolved_profile_cannot_use_a_bound_procedure_or_lesson(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, _, _ = self._home(directory)
            write_service(home, "cloudflare", extra="profile_ids:\n  - beta")
            (home / "failures").mkdir()
            (home / "failures" / "2026-07-13-cloudflare-beta-only.md").write_text("\n".join([
                "---", 'date: "2026-07-13"', "service: cloudflare", 'service_version: "v1"', "status: active", "profile_id: beta", "---",
                "## 진짜 원인", "beta 전용", "",
            ]), encoding="utf-8")
            decision = preflight_json("cloudflare", "--cwd", directory, "--profile", "missing", "--home", str(home))
        self.assertIsNone(decision["profile"])
        self.assertEqual(decision["state"], "cache-invalid")
        self.assertTrue(any("unresolved profile" in entry for entry in decision["invalid_entries"]))
        self.assertEqual(decision["active_lessons"], [])

    def test_private_root_symlink_is_rejected_by_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            home = Path(directory)
            (home / "services").mkdir()
            (home / "services" / "_private").symlink_to(outside, target_is_directory=True)
            run = run_cli("check", "--home", str(home))
        self.assertEqual(run.returncode, 1)
        self.assertIn("services/_private", run.stdout)

    def test_non_distributable_draft_promotes_into_the_private_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            write_service(home, "acme")
            draft_dir = home / "failures" / "_drafts"
            draft_dir.mkdir(parents=True)
            draft = draft_dir / "2026-07-13-acme-private-fix.md"
            draft.write_text("\n".join([
                "---", 'date: "2026-07-13"', "service: acme", 'service_version: "v1"', "status: active", "distributable: false", "---",
                "## 진짜 원인", "내 계정 전용 설정", "",
            ]), encoding="utf-8")
            run = run_cli("memory", "promote", str(draft), "--home", str(home))
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertTrue((home / "failures" / "_private" / draft.name).is_file())
            self.assertFalse((home / "failures" / draft.name).exists())
            self.assertNotIn("_private", (home / "services" / "acme.md").read_text(encoding="utf-8"))

    def test_private_promotion_refuses_directory_aliases_without_public_writes(self) -> None:
        for alias_kind in ("services", "failures"):
            with self.subTest(alias_kind=alias_kind), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                procedure = write_service(home, "acme")
                original = procedure.read_bytes()
                draft_dir = home / "failures" / "_drafts"
                draft_dir.mkdir(parents=True)
                draft = draft_dir / "2026-07-13-acme-private-fix.md"
                draft.write_text("\n".join([
                    "---", 'date: "2026-07-13"', "service: acme",
                    'service_version: "v1"', "status: active", "distributable: false", "---",
                    "## 진짜 원인", "개인 계정 설정", "",
                ]), encoding="utf-8")
                original_draft = draft.read_bytes()
                (home / alias_kind / "_private").symlink_to(home / alias_kind, target_is_directory=True)
                run = run_cli("memory", "promote", str(draft), "--home", str(home))
                self.assertNotEqual(run.returncode, 0)
                self.assertEqual(procedure.read_bytes(), original)
                self.assertEqual(draft.read_bytes(), original_draft)
                self.assertFalse((home / "failures" / draft.name).exists())
                self.assertFalse((home / "failures" / "_private" / draft.name).exists())

    def test_preflight_sends_at_most_one_jev_request(self) -> None:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
        from portwright.jev import JevClient
        from portwright.preflight import Preflight
        from tests._v2 import FIXTURES

        class Counting(JevClient):
            def __init__(self) -> None:
                super().__init__(fixtures=FIXTURES)
                self.calls: list[tuple[str, list[str]]] = []

            def ask(self, fixture_id, state, questions):
                self.calls.append((fixture_id, sorted(questions)))
                return super().ask(fixture_id, state, questions)

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            write_profile(home, "alpha", home / "a", github="a", db=None, services=("vercel",))
            write_profile(home, "beta", home / "b", github="b", db=None, services=("vercel",))
            write_service(home, "vercel", version_tag="cli-48.0")
            (home / "install").mkdir()
            (home / "install" / "tiers.json").write_text('{"rules": []}', encoding="utf-8")
            client = Counting()
            decision = Preflight(home, jev=client).resolve("vercel", cwd=home, evidence_version="cli-49", evidence_text="changed docs")
        self.assertEqual(client.calls, [("preflight-vercel-call", ["contradicts", "profile", "risk"])])
        self.assertEqual(decision.freshness["state"], "stale")
        self.assertEqual(decision.tier, "confirm")


if __name__ == "__main__":
    unittest.main()
