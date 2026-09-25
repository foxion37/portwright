from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

from .browser import select_menu
from .clients import CLIENTS, ClientManager
from .contracts import ContractCatalog, PACKAGE_ROOT, render_report, resource_path
from .jev import JevClient
from .memory import MemoryLifecycle
from .preflight import render_decision, run_preflight
from .review import Ledger, collect, prioritize, render, write_report


def _root(value: str | None) -> Path:
    return Path(value or os.environ.get("PORTWRIGHT_HOME") or PACKAGE_ROOT).expanduser().resolve()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="portwright", description="Tool-use knowledge layer CLI")
    sub = parser.add_subparsers(dest="command")

    for name in ("check", "lint"):
        command = sub.add_parser(name, help="Validate Procedure, Lesson, and draft contracts")
        command.add_argument("--home")
        command.add_argument("--strict", action="store_true", help="Reject stale notes")

    preflight = sub.add_parser("preflight", help="Resolve Profile, Procedure, Lesson, freshness, and advisory tier before tool use")
    preflight.add_argument("service")
    preflight.add_argument("--intent", choices=("call", "instruct", "recover"), default="call")
    preflight.add_argument("--profile", help="Profile id; default resolves from --cwd")
    preflight.add_argument("--cwd", help="Working directory to route (default: current)")
    preflight.add_argument("--evidence-version", help="Version string read from the current official docs")
    preflight.add_argument("--evidence-fetched-at", help="YYYY-MM-DD the evidence was fetched")
    preflight.add_argument("--evidence-file", help="Text file with the current official docs excerpt (JEV noul support)")
    preflight.add_argument("--json", action="store_true")
    preflight.add_argument("--home")
    preflight.add_argument("--hub", choices=("public", "company"))
    preflight.add_argument("--include-trial", action="store_true")

    hub = sub.add_parser("hub", help="Synchronize one shared Hub content snapshot")
    hub_sub = hub.add_subparsers(dest="hub_command", required=True)
    sync = hub_sub.add_parser("sync")
    sync.add_argument("--hub", choices=("public", "company"))
    sync.add_argument("--include-trial", action="store_true")
    sync.add_argument("--offline", action="store_true")
    sync.add_argument("--home")

    browser = sub.add_parser("browser", help="Select a browser menu candidate for a goal (extraction stays in the browser)")
    browser_sub = browser.add_subparsers(dest="browser_command", required=True)
    select = browser_sub.add_parser("select")
    select.add_argument("--candidates", required=True, help="JSON file: list of {ref, role, name, url?, snippet?}")
    select.add_argument("--goal", required=True)
    select.add_argument("--json", action="store_true")

    update = sub.add_parser("update", help="Fast-forward the shared pack and re-validate stale Procedures")
    update.add_argument("--dry-run", action="store_true")
    update.add_argument("--json", action="store_true")
    update.add_argument("--home")

    review = sub.add_parser("review", help="Propose improvements from repeated failures and repeated questions")
    review.add_argument("--days", type=int, default=90)
    review.add_argument("--no-jev", action="store_true", help="Skip the optional reordering judgment")
    review.add_argument("--no-write", action="store_true", help="Print only; do not save a report")
    review.add_argument("--json", action="store_true")
    review.add_argument("--home")

    memory = sub.add_parser("memory", help="Create, review, or promote safe memory drafts")
    memory_sub = memory.add_subparsers(dest="memory_command", required=True)
    draft = memory_sub.add_parser("draft")
    draft.add_argument("kind", choices=("procedure", "lesson"))
    draft.add_argument("service")
    draft.add_argument("slug", nargs="?")
    draft.add_argument("--home")
    review = memory_sub.add_parser("review")
    review.add_argument("path")
    review.add_argument("--home")
    promote = memory_sub.add_parser("promote")
    promote.add_argument("path")
    promote.add_argument("--replace", action="store_true")
    promote.add_argument("--home")

    client = sub.add_parser("client", help="Install, remove, or inspect Client adapters")
    client_sub = client.add_subparsers(dest="client_command", required=True)
    for name in ("install", "remove"):
        action = client_sub.add_parser(name)
        action.add_argument("client", choices=tuple(CLIENTS))
        action.add_argument("--home")
        action.add_argument("--user-home")
    doctor = client_sub.add_parser("doctor")
    doctor.add_argument("client", nargs="?", choices=tuple(CLIENTS))
    doctor.add_argument("--home")
    doctor.add_argument("--user-home")

    legacy_doctor = sub.add_parser("doctor", help="Compatibility alias for client doctor plus repository checks")
    legacy_doctor.add_argument("--home")
    legacy_doctor.add_argument("--user-home")

    models = sub.add_parser("models", help="Show the task-class to model-tier routing table agents should follow")
    models.add_argument("--json", action="store_true")
    models.add_argument("--home")

    skills = sub.add_parser("skills", help="Serve the version-matched packaged skill guides (Orca-style stub loading)")
    skills_sub = skills.add_subparsers(dest="skills_command", required=True)
    skills_sub.add_parser("list")
    get = skills_sub.add_parser("get")
    get.add_argument("name")
    mcp = sub.add_parser("mcp", help="Serve preflight, get_note, and status as a stdio MCP server")
    mcp.add_argument("--home")
    sub.add_parser("help", help="Show this help")
    return parser


def _check(args: argparse.Namespace) -> int:
    root = _root(args.home)
    report = ContractCatalog(root).validate_workspace(strict=args.strict, include_drafts=True)
    print(render_report(report))
    return 0 if report.ok else 1


def _routing_path(root: Path) -> Path:
    return resource_path(root, "install/model-routing.json")


def _models(args: argparse.Namespace) -> int:
    table = json.loads(_routing_path(_root(args.home)).read_text(encoding="utf-8"))
    if args.json:
        print(json.dumps(table, ensure_ascii=False, indent=2))
        return 0
    print(f"decision model: {table['decision_model']['id']} ({table['decision_model']['use_for']})")
    for entry in table["classes"]:
        tier = entry["tier"] or "-"
        effort = entry["effort"] or "-"
        print(f"{entry['class']:<20} decision={entry['decision']:<13} tier={tier:<9} effort={effort:<10} {entry['note']}")
    for rule in table["rules"]:
        print(f"- {rule}")
    return 0


def _skills(args: argparse.Namespace) -> int:
    skill_root = PACKAGE_ROOT / "skills"
    names = sorted(path.parent.name for path in skill_root.glob("*/SKILL.md"))
    if args.skills_command == "list":
        print("\n".join(names))
        return 0
    if args.name not in names:
        raise ValueError(f"unknown skill {args.name!r}; choose one of: {', '.join(names)}")
    print((skill_root / args.name / "SKILL.md").read_text(encoding="utf-8"), end="")
    return 0


def _preflight(args: argparse.Namespace) -> int:
    evidence_text = Path(args.evidence_file).read_text(encoding="utf-8") if args.evidence_file else None
    decision = run_preflight(
        _root(args.home),
        args.service,
        args.intent,
        cwd=Path(args.cwd).expanduser() if args.cwd else None,
        profile_id=args.profile,
        evidence_version=args.evidence_version,
        evidence_fetched_at=args.evidence_fetched_at,
        evidence_text=evidence_text,
        hub=args.hub,
        include_trial=args.include_trial,
    )
    print(json.dumps(decision.to_dict(), ensure_ascii=False, indent=2) if args.json else render_decision(decision))
    return 0


def _review(args: argparse.Namespace) -> int:
    root = _root(args.home)
    findings = collect(root, days=args.days)
    jev = None if args.no_jev else JevClient.from_env()
    findings, order = prioritize(findings, jev)
    rows = len(Ledger(root).entries(args.days))
    text = render(findings, days=args.days, ledger_rows=rows, order=order)
    if args.json:
        print(json.dumps({"days": args.days, "ledger_rows": rows, "order": order,
                          "findings": [finding.to_dict() for finding in findings]}, ensure_ascii=False, indent=2))
    else:
        print(text)
    if not args.no_write:
        path = write_report(root, text)
        print(f"saved: {path.relative_to(root)}")
    return 0


def _browser(args: argparse.Namespace) -> int:
    candidates = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    if not isinstance(candidates, list):
        raise ValueError("candidates file must contain a JSON list")
    selection = select_menu(candidates, args.goal, jev=JevClient.from_env(), fixture_id="browser-select")
    if args.json:
        print(json.dumps(selection.to_dict(), ensure_ascii=False, indent=2))
    elif selection.accepted:
        print(f"SELECT: {selection.candidate_ref} -> {selection.value_location} (confidence {selection.confidence:.2f})")
    else:
        print(f"REFUSE: {selection.reject_reason}")
    return 0 if selection.accepted else 1


def _update(args: argparse.Namespace) -> int:
    from .update import render_update, run_update

    report = run_update(_root(args.home), dry_run=args.dry_run)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) if args.json else render_update(report))
    return 0 if not report.refused_conflicts and not report.docs_failed else 1


def _memory(args: argparse.Namespace) -> int:
    lifecycle = MemoryLifecycle(_root(args.home))
    if args.memory_command == "draft":
        if args.kind == "lesson":
            if not args.slug:
                raise ValueError("lesson draft requires a slug")
            result = lifecycle.create_lesson_draft(args.service, args.slug)
        else:
            if args.slug:
                raise ValueError("procedure draft does not accept a slug")
            result = lifecycle.create_procedure_draft(args.service)
        if not result.ok:
            print(f"DRAFT BLOCKED: {result.path}")
            for issue in result.issues:
                print(f"  - {issue.render()}")
            return 1
        state = "DRAFT CREATED" if result.created else "DRAFT EXISTS"
        print(f"{state}: {result.path.relative_to(lifecycle.root)}")
        executable = shlex.quote(str(PACKAGE_ROOT / "bin" / "portwright"))
        memory_root = shlex.quote(str(lifecycle.root))
        print(
            "Next: edit the draft, then run "
            f"{executable} memory promote {result.path.relative_to(lifecycle.root)} "
            f"--home {memory_root}"
        )
        return 0
    path = Path(args.path)
    if not path.is_absolute():
        path = lifecycle.root / path
    if args.memory_command == "review":
        result = lifecycle.review(path)
    else:
        result = lifecycle.promote(path, replace=args.replace)
    if result.ok:
        state = "PROMOTED" if result.promoted else "READY TO PROMOTE"
        print(f"{state}: {result.path.relative_to(lifecycle.root)}")
        return 0
    print(f"PROMOTION BLOCKED: {result.path}")
    for issue in result.issues:
        print(f"  - {issue.render()}")
    return 1


def _client(args: argparse.Namespace) -> int:
    root = _root(args.home)
    user_home = Path(args.user_home).expanduser().resolve() if args.user_home else Path.home()
    manager = ClientManager(root, user_home)
    if args.client_command == "install":
        healths = [manager.install(args.client)]
    elif args.client_command == "remove":
        healths = [manager.remove(args.client)]
    elif args.client:
        healths = [manager.inspect(args.client)]
    else:
        healths = manager.inspect_all()
    for health in healths:
        print(f"[{health.state.upper()}] {health.client_id}: {health.detail}")
    if args.client_command in {"doctor", "install"}:
        return 1 if any(health.state in {"partial", "not-installed"} for health in healths) else 0
    return 0


def _doctor(args: argparse.Namespace) -> int:
    root = _root(args.home)
    report = ContractCatalog(root).validate_workspace(include_drafts=True)
    print(render_report(report))
    namespace = argparse.Namespace(
        home=str(root),
        user_home=args.user_home,
        client_command="doctor",
        client=None,
    )
    client_code = _client(namespace)
    return 0 if report.ok and client_code == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command in (None, "help"):
        parser.print_help()
        return 0
    try:
        if args.command in ("check", "lint"):
            return _check(args)
        if args.command == "preflight":
            return _preflight(args)
        if args.command == "hub":
            from .hub_sync import sync
            print(json.dumps(sync(_root(args.home), hub=args.hub, include_trial=args.include_trial, offline=args.offline),
                             ensure_ascii=False))
            return 0
        if args.command == "memory":
            return _memory(args)
        if args.command == "browser":
            return _browser(args)
        if args.command == "update":
            return _update(args)
        if args.command == "review":
            return _review(args)
        if args.command == "mcp":
            from .mcp import serve

            serve(_root(args.home))
            return 0
        if args.command == "models":
            return _models(args)
        if args.command == "skills":
            return _skills(args)
        if args.command == "client":
            return _client(args)
        if args.command == "doctor":
            return _doctor(args)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"portwright: {error}", file=sys.stderr)
        return 2
    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
