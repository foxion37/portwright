from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


START = "<!-- portwright:start"
END = "<!-- portwright:end -->"


@dataclass(frozen=True)
class ClientHealth:
    client_id: str
    state: str
    detail: str


@dataclass(frozen=True)
class ClientSpec:
    client_id: str
    mode: str
    target: str | None = None
    skills: bool = False
    preamble: str | None = None


CLIENTS = {
    "claude-code": ClientSpec("claude-code", "hook", ".claude/settings.json", skills=True),
    "codex": ClientSpec("codex", "managed", ".codex/AGENTS.md", skills=True),
    "cursor": ClientSpec("cursor", "manual"),
    "gemini-cli": ClientSpec("gemini-cli", "managed", ".gemini/GEMINI.md"),
    "hermes": ClientSpec("hermes", "managed", ".hermes/SOUL.md"),
    "oh-my-pi": ClientSpec("oh-my-pi", "managed", ".omp/agent/AGENTS.md"),
    "opencode": ClientSpec("opencode", "managed", ".config/opencode/AGENTS.md"),
    "vscode": ClientSpec(
        "vscode",
        "managed",
        ".copilot/instructions/portwright.instructions.md",
        preamble='---\napplyTo: "**"\n---',
    ),
}


class ClientManager:
    def __init__(self, root: Path, user_home: Path):
        self.root = root.resolve()
        self.user_home = user_home.resolve()

    def install(self, client_id: str) -> ClientHealth:
        spec = self._spec(client_id)
        if spec.mode == "manual":
            return ClientHealth(client_id, "action-required", self._cursor_instructions())
        target = self._target(spec)
        if spec.mode == "hook":
            self._read_json_object(target)
        elif target.is_file():
            target.read_text(encoding="utf-8")
        if spec.skills:
            self._preflight_skill_links(client_id)
        if spec.mode == "managed":
            self._install_managed_block(target, preamble=spec.preamble)
        elif spec.mode == "hook":
            self._install_claude_hook(target)
        if spec.skills:
            self._install_skill_links(client_id)
        return self.inspect(client_id)

    def remove(self, client_id: str) -> ClientHealth:
        spec = self._spec(client_id)
        if spec.mode == "manual":
            return ClientHealth(client_id, "action-required", "Cursor User Rules에서 portwright managed block을 지우세요.")
        if spec.mode == "managed":
            self._remove_managed_block(self._target(spec))
        elif spec.mode == "hook":
            self._remove_claude_hook(self._target(spec))
        if spec.skills:
            self._remove_skill_links(client_id)
        return self.inspect(client_id)

    def inspect(self, client_id: str) -> ClientHealth:
        spec = self._spec(client_id)
        if spec.mode == "manual":
            return ClientHealth(client_id, "manual", "Cursor User Rules는 파일로 검증할 수 없습니다.")
        checks: list[bool] = []
        details: list[str] = []
        if spec.skills:
            skill_root = self._skill_root(client_id)
            for skill in ("portwright-tool-use", "portwright-tool-memory"):
                present = (skill_root / skill / "SKILL.md").is_file()
                checks.append(present)
                details.append(f"{skill}={'ok' if present else 'missing'}")
        if spec.mode == "managed":
            target = self._target(spec)
            count = target.read_text(encoding="utf-8").count(START) if target.is_file() else 0
            checks.append(count == 1)
            details.append(f"managed-blocks={count}")
        elif spec.mode == "hook":
            target = self._target(spec)
            present = self._claude_hook_present(target)
            checks.append(present)
            details.append(f"SessionStart-hook={'ok' if present else 'missing'}")
        if checks and all(checks):
            state = "ok"
        elif any(checks):
            state = "partial"
        else:
            state = "not-installed"
        return ClientHealth(client_id, state, ", ".join(details))

    def inspect_all(self) -> list[ClientHealth]:
        return [self.inspect(client_id) for client_id in CLIENTS]

    def _spec(self, client_id: str) -> ClientSpec:
        try:
            return CLIENTS[client_id]
        except KeyError as error:
            raise ValueError(f"unknown client {client_id!r}; choose one of: {', '.join(CLIENTS)}") from error

    def _skill_root(self, client_id: str) -> Path:
        if client_id == "claude-code":
            skill_root = self.user_home / ".claude" / "skills"
        else:
            skill_root = self.user_home / ".codex" / "skills"
        if skill_root.is_symlink() or not skill_root.parent.resolve().is_relative_to(self.user_home):
            raise RuntimeError(f"refusing symlink skill root: {skill_root}")
        return skill_root

    def _target(self, spec: ClientSpec) -> Path:
        target = self.user_home / str(spec.target)
        resolved_parent = target.parent.resolve()
        if not resolved_parent.is_relative_to(self.user_home):
            raise RuntimeError(f"refusing Client path outside user home: {target}")
        if target.is_symlink():
            raise RuntimeError(f"refusing symlink Client target: {target}")
        return target

    def _install_skill_links(self, client_id: str) -> None:
        destination_root = self._skill_root(client_id)
        destination_root.mkdir(parents=True, exist_ok=True)
        for skill in ("portwright-tool-use", "portwright-tool-memory"):
            source = self.root / "skills" / skill
            destination = destination_root / skill
            if destination.is_symlink() and destination.resolve() == source.resolve():
                continue
            if destination.exists() or destination.is_symlink():
                raise RuntimeError(f"refusing to replace existing skill path: {destination}")
            destination.symlink_to(source, target_is_directory=True)

    def _preflight_skill_links(self, client_id: str) -> None:
        destination_root = self._skill_root(client_id)
        for skill in ("portwright-tool-use", "portwright-tool-memory"):
            source = self.root / "skills" / skill
            destination = destination_root / skill
            if destination.is_symlink() and destination.resolve() == source.resolve():
                continue
            if destination.exists() or destination.is_symlink():
                raise RuntimeError(f"refusing to replace existing skill path: {destination}")

    def _remove_skill_links(self, client_id: str) -> None:
        destination_root = self._skill_root(client_id)
        for skill in ("portwright-tool-use", "portwright-tool-memory"):
            destination = destination_root / skill
            source = self.root / "skills" / skill
            if destination.is_symlink() and destination.resolve() == source.resolve():
                destination.unlink()

    def _canonical_block(self) -> str:
        template = (self.root / "install" / "snippets" / "portwright.block.md").read_text(encoding="utf-8")
        return template.replace("{{PORTWRIGHT_HOME}}", shlex.quote(str(self.root))).strip()

    def _install_managed_block(self, target: Path, *, preamble: str | None = None) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if preamble and current.strip() and not current.startswith(f"{preamble.rstrip()}\n"):
            raise ValueError(f"required preamble missing in {target}; refusing to modify existing file")
        cleaned = self._without_managed_block(current).rstrip()
        if preamble and not cleaned:
            cleaned = preamble.rstrip()
        updated = f"{cleaned}\n\n{self._canonical_block()}\n" if cleaned else f"{self._canonical_block()}\n"
        if current == updated:
            return
        self._backup(target)
        self._atomic_write(target, updated)

    def _remove_managed_block(self, target: Path) -> None:
        if not target.is_file():
            return
        current = target.read_text(encoding="utf-8")
        updated = self._without_managed_block(current).rstrip()
        updated = f"{updated}\n" if updated else ""
        if current == updated:
            return
        self._backup(target)
        self._atomic_write(target, updated)

    @staticmethod
    def _without_managed_block(text: str) -> str:
        pattern = re.compile(r"(?ms)^<!-- portwright:start.*?^<!-- portwright:end -->\s*")
        return pattern.sub("", text)

    def _install_claude_hook(self, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        data = self._read_json_object(target)
        sessions = self._session_hooks(data, target, create=True)
        command = f"bash {shlex.quote(str(self.root / 'install' / 'portwright-reminder.sh'))}"
        if not self._hook_in(sessions, command):
            sessions.append({"hooks": [{"type": "command", "command": command, "timeout": 5}]})
        serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if current == serialized:
            return
        self._backup(target)
        self._atomic_write(target, serialized)

    def _remove_claude_hook(self, target: Path) -> None:
        if not target.is_file():
            return
        data = self._read_json_object(target)
        command = f"bash {shlex.quote(str(self.root / 'install' / 'portwright-reminder.sh'))}"
        sessions = self._session_hooks(data, target, create=False)
        filtered = [entry for entry in sessions if not self._hook_in([entry], command)]
        if filtered == sessions:
            return
        self._backup(target)
        data["hooks"]["SessionStart"] = filtered
        self._atomic_write(target, json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    def _claude_hook_present(self, target: Path) -> bool:
        if not target.is_file():
            return False
        try:
            data = self._read_json_object(target)
            sessions = self._session_hooks(data, target, create=False)
        except ValueError:
            return False
        command = f"bash {shlex.quote(str(self.root / 'install' / 'portwright-reminder.sh'))}"
        return self._hook_in(sessions, command)

    @staticmethod
    def _session_hooks(data: dict[str, object], target: Path, *, create: bool) -> list[object]:
        hooks = data.get("hooks")
        if hooks is None:
            if not create:
                return []
            hooks = {}
            data["hooks"] = hooks
        if not isinstance(hooks, dict):
            raise ValueError(f"expected 'hooks' to be a JSON object in {target}")
        sessions = hooks.get("SessionStart")
        if sessions is None:
            if not create:
                return []
            sessions = []
            hooks["SessionStart"] = sessions
        if not isinstance(sessions, list):
            raise ValueError(f"expected 'hooks.SessionStart' to be a JSON array in {target}")
        return sessions

    @staticmethod
    def _hook_in(entries: list[object], command: str) -> bool:
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for hook in entry.get("hooks", []):
                if isinstance(hook, dict) and hook.get("command") == command:
                    return True
        return False

    @staticmethod
    def _read_json_object(path: Path) -> dict[str, object]:
        if not path.exists() or not path.read_text(encoding="utf-8").strip():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSON; refusing to modify {path}: {error}") from error
        if not isinstance(data, dict):
            raise ValueError(f"expected a JSON object in {path}")
        return data

    @staticmethod
    def _backup(path: Path) -> None:
        if not path.exists():
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = path.with_name(f"{path.name}.bak-{stamp}")
        shutil.copy2(path, backup)

    def _cursor_instructions(self) -> str:
        return (
            "Cursor User Rules는 파일로 안전하게 수정할 수 없습니다. "
            "아래 내용을 Cursor Settings > Rules > User Rules에 한 번 붙여 넣으세요.\n\n"
            f"{self._canonical_block()}"
        )

    def _atomic_write(self, path: Path, text: str) -> None:
        self._assert_safe_client_path(path)
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(text)
            self._assert_safe_client_path(path)
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _assert_safe_client_path(self, path: Path) -> None:
        if path.is_symlink() or not path.parent.resolve().is_relative_to(self.user_home):
            raise RuntimeError(f"refusing unsafe Client target: {path}")
