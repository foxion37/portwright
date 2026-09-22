from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests._v2 import preflight_json, write_service


class FreshnessDecisionTableTests(unittest.TestCase):
    """The four freshness rules from the 2.0.0 seed. unknown is never fresh."""

    def _home(self, directory: str) -> Path:
        home = Path(directory)
        write_service(home, "vercel", version_tag="cli-48.0", last_verified="2026-07-13")
        return home

    def test_rule1_matching_evidence_version_is_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            decision = preflight_json("vercel", "--evidence-version", "cli-48.0", "--home", str(self._home(directory)), fixtures=None)
        self.assertEqual(decision["freshness"]["state"], "fresh")
        self.assertEqual(decision["freshness"]["action"], "none")
        self.assertEqual(decision["state"], "ready")

    def test_rule1_different_evidence_version_is_stale_and_requires_derivation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(directory)
            evidence = home / "current.txt"
            evidence.write_text("Deploy with `tool deploy --production`; `--prod` was removed in 49.", encoding="utf-8")
            decision = preflight_json("vercel", "--evidence-version", "cli-49.1", "--evidence-file", str(evidence), "--home", str(home))
        self.assertEqual(decision["freshness"]["state"], "stale")
        self.assertEqual(decision["freshness"]["action"], "derive-required")
        self.assertEqual(decision["state"], "stale-warning")
        self.assertIn("다시 도출", decision["next_action"])
        self.assertIn("rule 1", decision["freshness"]["rationale"])
        self.assertIn("JEV noul contradiction 0.91", decision["freshness"]["rationale"])
        self.assertEqual(decision["freshness"]["compared"]["noul_contradicts"], 0.91)

    def test_rule2_fetched_before_last_verified_is_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            decision = preflight_json("vercel", "--evidence-fetched-at", "2026-07-01", "--home", str(self._home(directory)))
        self.assertEqual(decision["freshness"]["state"], "fresh")

    def test_rule2_fetched_after_last_verified_is_unknown_not_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            decision = preflight_json("vercel", "--evidence-fetched-at", "2026-09-01", "--home", str(self._home(directory)))
        self.assertEqual(decision["freshness"]["state"], "unknown")
        self.assertEqual(decision["freshness"]["action"], "verify-required")
        self.assertIn("evidence-version", decision["next_action"])

    def test_rule3_no_evidence_is_unknown_and_verify_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            decision = preflight_json("vercel", "--home", str(self._home(directory)))
        self.assertEqual(decision["freshness"]["state"], "unknown")
        self.assertEqual(decision["freshness"]["action"], "verify-required")

    def test_rule3_missing_credential_and_fixture_stay_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as empty:
            home = self._home(directory)
            evidence = home / "current.txt"
            evidence.write_text("changed docs", encoding="utf-8")
            for fixtures in (Path(empty), None):
                decision = preflight_json("vercel", "--evidence-fetched-at", "2026-09-01", "--evidence-file", str(evidence), "--home", str(home), fixtures=fixtures)
                self.assertEqual(decision["freshness"]["state"], "unknown")
                self.assertEqual(decision["freshness"]["action"], "verify-required")
                self.assertIn("JEV unavailable", decision["freshness"]["rationale"])
                self.assertNotIn("TYPESAFE", decision["freshness"]["rationale"])

    def test_rule4_noul_cannot_flip_a_deterministic_fresh_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(directory)
            evidence = home / "current.txt"
            evidence.write_text("contradicting text", encoding="utf-8")
            decision = preflight_json("vercel", "--evidence-version", "cli-48.0", "--evidence-file", str(evidence), "--home", str(home))
        self.assertEqual(decision["freshness"]["state"], "fresh")
        self.assertNotIn("noul", decision["freshness"]["rationale"])

    def test_cache_miss_is_derive_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            decision = preflight_json("nothing", "--home", directory)
        self.assertEqual(decision["freshness"]["state"], "unknown")
        self.assertEqual(decision["freshness"]["action"], "derive-required")
        self.assertEqual(decision["state"], "derive-required")


if __name__ == "__main__":
    unittest.main()
