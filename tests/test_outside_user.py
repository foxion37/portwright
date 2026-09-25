"""Exercise the public export as a new user outside the author's checkout."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OutsideUserTest(unittest.TestCase):
    def test_fresh_clone_installs_clients_and_derives_missing_procedure(self) -> None:
        exporter = ROOT / "scripts" / "export_public.py"
        if not exporter.is_file():
            self.skipTest("public export does not ship the exporter")

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            home = base / "empty-home"
            home.mkdir()
            elsewhere = base / "elsewhere"
            elsewhere.mkdir()
            exported = base / "export"
            clone = base / "unrelated-checkout"
            env = {
                "HOME": str(home),
                "PATH": os.environ.get("PATH", os.defpath),
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            outputs: list[str] = []

            def run(*args: str, cwd: Path = elsewhere) -> str:
                result = subprocess.run(args, cwd=cwd, env=env, text=True,
                                        capture_output=True, check=False)
                combined = result.stdout + result.stderr
                outputs.append(combined)
                self.assertEqual(result.returncode, 0, f"{args}: {combined}")
                return result.stdout

            run(sys.executable, str(exporter), "--out", str(exported))
            run("git", "init", "-q", str(exported))
            run("git", "add", "-A", cwd=exported)
            run("git", "-c", "user.name=Outside User", "-c", "user.email=outside@example.invalid",
                "commit", "-qm", "public export", cwd=exported)
            run("git", "clone", "-q", str(exported), str(clone))

            cli = str(clone / "bin" / "portwright")
            for client in ("claude-code", "codex"):
                self.assertIn(f"[OK] {client}:", run(cli, "client", "install", client))
                self.assertIn(f"[OK] {client}:", run(cli, "client", "doctor", client))

            decision = json.loads(run(cli, "preflight", "outside-user-unknown-service", "--json"))
            self.assertEqual(decision["freshness"]["action"], "derive-required")
            self.assertTrue(run(cli, "check", cwd=clone).strip().splitlines()[-1].endswith(", 0 failed"))

            transcript = "".join(outputs)
            for author_path in (str(ROOT), str(ROOT.parents[1]), str(Path.home()),
                                "developer/tools/portwright"):
                with self.subTest(author_path=author_path):
                    self.assertNotIn(author_path, transcript)


if __name__ == "__main__":
    unittest.main()
