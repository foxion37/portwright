from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1]


class OutsideCloneTests(unittest.TestCase):
    def test_clean_home_installs_clients_and_derives_unknown_service(self) -> None:
        with tempfile.TemporaryDirectory(prefix="outside-") as directory:
            base = Path(directory).resolve()
            clone = base / "checkout"
            home = base / "empty-home"
            clone.mkdir()
            home.mkdir()
            listed = subprocess.run(["git", "ls-files", "-z"], cwd=SOURCE, capture_output=True)
            if listed.returncode != 0:
                self.skipTest("source tree is not a git checkout (e.g. a release archive)")
            tracked = listed.stdout
            for raw in tracked.split(b"\0"):
                if not raw:
                    continue
                relative = Path(os.fsdecode(raw))
                if "_private" in relative.parts:
                    continue
                destination = clone / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(SOURCE / relative, destination)

            env = os.environ.copy()
            env["HOME"] = str(home)
            env.pop("PORTWRIGHT_HOME", None)
            env.pop("PYTHONPATH", None)
            cli = clone / "bin" / "portwright"

            def run(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [str(cli), *args], cwd=clone, env=env, text=True, capture_output=True,
                )

            for client in ("claude-code", "codex"):
                installed = run("client", "install", client)
                self.assertEqual(installed.returncode, 0, installed.stderr)
                self.assertIn("[OK]", installed.stdout)
                skill_dir = home / (".claude" if client == "claude-code" else ".codex") / "skills"
                for skill in ("portwright-tool-use", "portwright-tool-memory"):
                    self.assertEqual((skill_dir / skill).resolve(), clone / "skills" / skill)

            block = (home / ".codex" / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn(f"{clone}/bin/portwright preflight", block)
            settings = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
            hook = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
            self.assertIn(str(clone / "install" / "portwright-reminder.sh"), hook)
            reminder = subprocess.run(["bash", str(clone / "install" / "portwright-reminder.sh")],
                                      cwd=home, env=env, text=True, capture_output=True)
            self.assertEqual(reminder.returncode, 0, reminder.stderr)
            self.assertIn(f"{clone}/bin/portwright preflight", reminder.stdout)
            self.assertNotIn("reminder unavailable", reminder.stdout)

            preflight = run("preflight", "unknown-outside-service", "--json")
            self.assertEqual(preflight.returncode, 0, preflight.stderr)
            self.assertEqual(json.loads(preflight.stdout)["state"], "derive-required")
            self.assertIn(f"{clone}/bin/portwright", preflight.stdout)
            for result in (preflight.stdout, preflight.stderr, block, reminder.stdout):
                self.assertNotIn(str(SOURCE), result)
                self.assertNotIn(str(Path.home()), result)
                self.assertNotIn("/".join(("developer", "tools", "portwright")), result)

    def test_explicit_content_home_uses_packaged_install_resources(self) -> None:
        with tempfile.TemporaryDirectory(prefix="outside-") as directory:
            home = Path(directory).resolve() / "content"
            user_home = Path(directory).resolve() / "user"
            home.mkdir()
            user_home.mkdir()
            env = os.environ.copy()
            env["HOME"] = str(user_home)
            env["PORTWRIGHT_HOME"] = str(home)
            result = subprocess.run(
                [str(SOURCE / "bin" / "portwright"), "client", "install", "codex"],
                cwd=directory, env=env, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            installed = (user_home / ".codex" / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn(f"{SOURCE}/bin/portwright preflight <service-id> --home {home}", installed)
            self.assertEqual((user_home / ".codex" / "skills" / "portwright-tool-use").resolve(),
                             SOURCE / "skills" / "portwright-tool-use")

    def test_claude_hook_preserves_explicit_content_home(self) -> None:
        with tempfile.TemporaryDirectory(prefix="outside-") as directory:
            base = Path(directory).resolve()
            content = base / "content"
            user = base / "user"
            content.mkdir()
            user.mkdir()
            env = os.environ.copy()
            env["HOME"] = str(user)
            env.pop("PORTWRIGHT_HOME", None)
            installed = subprocess.run(
                [str(SOURCE / "bin" / "portwright"), "client", "install", "claude-code",
                 "--home", str(content)],
                env=env, text=True, capture_output=True,
            )
            self.assertEqual(installed.returncode, 0, installed.stderr)
            settings = json.loads((user / ".claude" / "settings.json").read_text(encoding="utf-8"))
            hook = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
            invoked = subprocess.run(hook, shell=True, env=env, text=True, capture_output=True)
            self.assertEqual(invoked.returncode, 0, invoked.stderr)
            self.assertIn(f"preflight <service-id> --home {content}", invoked.stdout)


if __name__ == "__main__":
    unittest.main()
