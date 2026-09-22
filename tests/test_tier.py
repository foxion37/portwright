from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests._v2 import preflight_json, run_cli, write_profile, write_service


class AdvisoryTierTests(unittest.TestCase):
    """Tier is advice inside the preflight output. Nothing is blocked; the client enforces."""

    def _home(self, directory: str, rules: list[dict]) -> Path:
        home = Path(directory)
        (home / "install").mkdir()
        (home / "install" / "tiers.json").write_text(json.dumps({"rules": rules}), encoding="utf-8")
        return home

    def test_explicit_forbid_rule_wins_and_does_not_block_the_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(directory, [
                {"service": "wipe", "intent": "call", "tier": "auto", "reason": "wrongly permissive"},
                {"service": "wipe", "intent": "*", "tier": "forbid", "reason": "destructive"},
            ])
            write_service(home, "wipe")
            run = run_cli("preflight", "wipe", "--home", str(home))
            decision = preflight_json("wipe", "--home", str(home))
        self.assertEqual(run.returncode, 0)
        self.assertIn("Tier: forbid (destructive)", run.stdout)
        self.assertEqual(decision["tier"], "forbid")
        self.assertEqual(decision["state"], "ready")
        self.assertIn("forbid", decision["next_action"])

    def test_matched_rule_gives_auto_with_its_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(directory, [{"service": "acme", "intent": "call", "tier": "auto", "reason": "read-only api"}])
            write_service(home, "acme")
            decision = preflight_json("acme", "--home", str(home))
        self.assertEqual(decision["tier"], "auto")
        self.assertEqual(decision["rationale"]["tier"], "read-only api")

    def test_unmatched_service_uses_synthetic_jev_score(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(directory, [])
            write_service(home, "acme")
            write_service(home, "wipe")
            low = preflight_json("acme", "--home", str(home))
            high = preflight_json("wipe", "--home", str(home))
        self.assertEqual(low["tier"], "auto")
        self.assertIn("JEV risk score 0.10", low["rationale"]["tier"])
        self.assertEqual(high["tier"], "forbid")
        self.assertIn("JEV risk score 1.90", high["rationale"]["tier"])

    def test_unmatched_and_no_judgment_defaults_to_confirm_never_auto(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as empty:
            home = self._home(directory, [])
            project = home / "p"
            project.mkdir()
            write_profile(home, "p", project, github="me", db=None, tier="auto")
            write_service(home, "acme")
            decision = preflight_json("acme", "--cwd", str(project), "--home", str(home), fixtures=Path(empty))
        self.assertEqual(decision["profile"]["id"], "p")
        self.assertEqual(decision["tier"], "confirm")
        self.assertIn("fixture missing", decision["rationale"]["tier"])

    def test_profile_default_tier_raises_but_never_lowers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(directory, [])
            project = home / "p"
            project.mkdir()
            write_profile(home, "p", project, github="me", db=None, tier="forbid")
            write_service(home, "acme")
            decision = preflight_json("acme", "--cwd", str(project), "--home", str(home))
        self.assertEqual(decision["tier"], "forbid")
        self.assertIn("profile p default_tier", decision["rationale"]["tier"])

    def test_instruct_intent_is_confirm_by_packaged_rules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            write_service(home, "acme")
            decision = preflight_json("acme", "--intent", "instruct", "--home", str(home))
        self.assertEqual(decision["tier"], "confirm")
        self.assertEqual(decision["state"], "verify-required")

    def test_github_call_without_operation_scope_requires_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            write_service(home, "github")
            decision = preflight_json("github", "--home", str(home), fixtures=None)
        self.assertEqual(decision["tier"], "confirm")


if __name__ == "__main__":
    unittest.main()


class ModelRoutingTests(unittest.TestCase):
    """Agents read the routing table; it must stay vendor-neutral and route closed questions away from LLMs."""

    def test_routing_table_is_neutral_and_sends_closed_questions_to_code_or_jev(self) -> None:
        run = run_cli("models", "--json")
        self.assertEqual(run.returncode, 0, run.stderr)
        table = json.loads(run.stdout)
        text = json.dumps(table).lower()
        for vendor in ("anthropic", "openai", "claude", "gpt", "gemini", "devin", "fable", "astra"):
            self.assertNotIn(vendor, text)
        by_class = {entry["class"]: entry for entry in table["classes"]}
        for closed in ("route-profile", "tier-risk", "freshness", "browser-menu"):
            self.assertEqual(by_class[closed]["decision"], "code-then-jev")
            self.assertIsNone(by_class[closed]["tier"])
        self.assertEqual(by_class["independent-review"]["tier"], "frontier")

    def test_skills_get_serves_the_packaged_guide(self) -> None:
        listing = run_cli("skills", "list")
        self.assertIn("portwright-tool-use", listing.stdout.split())
        guide = run_cli("skills", "get", "portwright-tool-use")
        self.assertEqual(guide.returncode, 0)
        self.assertIn("portwright models", guide.stdout)
        missing = run_cli("skills", "get", "nope")
        self.assertEqual(missing.returncode, 2)
