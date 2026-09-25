from __future__ import annotations

import shlex
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from .contracts import ContractCatalog, PACKAGE_ROOT, SERVICE_ID_RE
from .doc_cache import load_document
from .jev import JevBatch, JevClient
from .profiles import ProfileRouter, assess_freshness, decide_tier, load_tier_rules
from .review import Ledger


Intent = Literal["call", "instruct", "recover"]


@dataclass(frozen=True)
class PreflightDecision:
    service_id: str
    intent: Intent
    state: str
    procedure: str | None
    active_lessons: tuple[str, ...]
    stale_lessons: tuple[str, ...]
    invalid_entries: tuple[str, ...]
    next_action: str
    profile: dict[str, Any] | None = None
    freshness: dict[str, Any] = field(default_factory=dict)
    tier: str = "confirm"
    rationale: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class Preflight:
    def __init__(self, root: Path, jev: JevClient | None = None):
        self.root = root.resolve()
        self.catalog = ContractCatalog(self.root)
        self.jev = jev or JevClient.from_env()
        self.router = ProfileRouter(self.root, self.jev)

    def resolve(
        self,
        service_id: str,
        intent: Intent = "call",
        *,
        cwd: Path | None = None,
        profile_id: str | None = None,
        evidence_version: str | None = None,
        evidence_fetched_at: str | None = None,
        evidence_text: str | None = None,
    ) -> PreflightDecision:
        if not SERVICE_ID_RE.fullmatch(service_id):
            raise ValueError("service id must be lowercase kebab-case")
        cwd = (cwd or Path.cwd()).resolve()
        resolution = self.router.resolve(cwd=cwd, explicit=profile_id, service_id=service_id)
        profile = resolution.profile  # may still be pending a judgment; finalised after the batch

        procedure: str | None = None
        procedure_data: dict[str, Any] | None = None
        procedure_text: str | None = None
        invalid = [note.relative_path for note in self.catalog.directory_issues(include_drafts=False)
                   if note.kind in {"service", "failure"}]
        result = self.catalog.lookup("service", service_id)
        if result is not None:
            procedure = result.relative_path
            procedure_data = result.data or None
            procedure_text = "\n".join(result.body) if result.body else None
            if not result.ok:
                invalid.append(procedure)
        cached_evidence: dict[str, Any] | None = None
        source = (procedure_data or {}).get("freshness_evidence")
        if (
            not invalid and isinstance(source, dict) and isinstance(source.get("url"), str)
            and evidence_version is None and evidence_fetched_at is None and evidence_text is None
        ):
            cached_evidence = load_document(self.root, service_id, source["url"])
            if cached_evidence:
                evidence_text = cached_evidence["text"]

        freshness_input = procedure_data if procedure and not invalid else None
        rules = load_tier_rules(self.root)
        batch = JevBatch(self.jev, f"preflight-{service_id}-{intent}")
        batch.add(resolution.pending)
        batch.add(assess_freshness(freshness_input, evidence_version=evidence_version, evidence_fetched_at=evidence_fetched_at,
                                   evidence_text=evidence_text, procedure_text=procedure_text).pending)
        batch.add(decide_tier(service_id=service_id, intent=intent, profile=profile, procedure=procedure_data, rules=rules).pending)
        judgment = batch.commit()

        if resolution.pending is not None:
            resolution = self.router.resolve(cwd=cwd, explicit=profile_id, service_id=service_id, judgment=judgment)
            profile = resolution.profile
        procedure_status = procedure_data.get("status") if procedure_data else None
        bound = tuple((procedure_data or {}).get("profile_ids") or [])
        if bound and (profile is None or profile.id not in bound):
            actual = profile.id if profile else "unresolved profile"
            invalid.append(f"{procedure} (bound to {', '.join(bound)}, not {actual})")

        active: list[str] = []
        stale: list[str] = []
        for result in self.catalog.notes("failure"):
            if result.data.get("service") != service_id:
                continue
            lesson_profile = result.data.get("profile_id")
            if lesson_profile and (profile is None or lesson_profile != profile.id):
                continue
            relative = result.relative_path
            if not result.ok:
                invalid.append(relative)
                continue
            if result.data.get("status") == "stale":
                stale.append(relative)
            else:
                active.append(relative)


        freshness = assess_freshness(
            freshness_input,
            evidence_version=evidence_version,
            evidence_fetched_at=evidence_fetched_at,
            evidence_text=evidence_text,
            procedure_text=procedure_text,
            judgment=judgment,
        )
        if cached_evidence:
            freshness.compared["source_url"] = cached_evidence["source_url"]
            freshness.compared["source_sha256"] = cached_evidence["sha256"]
            freshness.compared["source_fetched_at"] = cached_evidence["fetched_at"]
        tier = decide_tier(service_id=service_id, intent=intent, profile=profile, procedure=procedure_data, rules=rules, judgment=judgment)

        if invalid:
            state = "cache-invalid"
            next_action = "잘못된 cache 항목을 고친 뒤 portwright check를 다시 통과시키세요."
        elif procedure is None:
            state = "derive-required"
            executable = shlex.quote(str(PACKAGE_ROOT / "bin" / "portwright"))
            memory_root = shlex.quote(str(self.root))
            next_action = (
                "현재 공식 문서에서 Procedure를 도출하고 memory draft를 만드세요. "
                f"{executable} memory draft procedure {service_id} --home {memory_root}"
            )
        elif intent == "instruct":
            state = "verify-required"
            next_action = "User에게 사람 몫을 안내하기 직전에 현재 공식 문서로 경로를 다시 확인하세요."
        elif procedure_status == "stale" or freshness.state == "stale":
            state = "stale-warning"
            next_action = "현재 공식 문서에서 Procedure를 다시 도출한 뒤 Agent가 직접 실행하세요."
        else:
            state = "ready"
            next_action = "캐시된 Procedure와 active Lesson을 적용해 Agent가 직접 실행하세요."
            if freshness.action == "verify-required":
                next_action += " 신선도는 unknown 이므로 실행 전 현재 문서 버전으로 --evidence-version 을 확인하세요."
        if tier.tier == "forbid":
            next_action = "이 호출은 forbid 등급입니다. 사용자 승인 없이 실행하지 마세요. " + next_action
        elif tier.tier == "confirm":
            next_action = "실행 전 사용자 확인이 필요합니다(confirm). " + next_action

        return PreflightDecision(
            service_id=service_id,
            intent=intent,
            state=state,
            procedure=procedure,
            active_lessons=tuple(active),
            stale_lessons=tuple(stale),
            invalid_entries=tuple(invalid),
            next_action=next_action,
            profile=profile.to_dict() if profile else None,
            freshness=freshness.to_dict(),
            tier=tier.tier,
            rationale={"profile": resolution.rationale, "freshness": freshness.rationale, "tier": tier.rationale},
        )


def run_preflight(
    root: Path,
    service_id: str,
    intent: Intent = "call",
    *,
    cwd: Path | None = None,
    profile_id: str | None = None,
    evidence_version: str | None = None,
    evidence_fetched_at: str | None = None,
    evidence_text: str | None = None,
    jev: JevClient | None = None,
    engine: Preflight | None = None,
) -> PreflightDecision:
    """The one preflight operation: resolve the decision, then record the ledger row.

    Every surface (CLI, MCP) calls this, so the ledger never depends on which surface asked.
    `engine` lets a long-lived surface reuse one resolver; it must point at `root`.
    """
    resolver = engine or Preflight(root, jev)
    decision = resolver.resolve(
        service_id,
        intent,
        cwd=cwd,
        profile_id=profile_id,
        evidence_version=evidence_version,
        evidence_fetched_at=evidence_fetched_at,
        evidence_text=evidence_text,
    )
    Ledger(root).record(
        service=decision.service_id,
        profile=(decision.profile or {}).get("id"),
        freshness=decision.freshness.get("state"),
        tier=decision.tier,
        intent=decision.intent,
        procedure=decision.procedure,
    )
    return decision


def render_decision(decision: PreflightDecision) -> str:
    label = decision.state.upper().replace("-", " ")
    lines = [f"{label}: {decision.service_id}"]
    profile = decision.profile
    if profile:
        databases = ", ".join(profile.get("databases") or ()) or "-"
        env = profile.get("env_source") or {}
        lines.append(
            f"Profile: {profile['id']} (github {profile['github_account']}, env {env.get('kind', '-')}"
            f"{'/' + env['project'] if env.get('project') else ''}, db {databases})"
        )
    else:
        lines.append(f"Profile: (none) {decision.rationale.get('profile', '')}")
    lines.append(f"Procedure: {decision.procedure or '(cache miss)'}")
    freshness = decision.freshness
    if freshness:
        lines.append(f"Freshness: {freshness['state']} -> {freshness['action']} ({freshness['rationale']})")
    lines.append(f"Tier: {decision.tier} ({decision.rationale.get('tier', '')})")
    lines.append(f"Active Lessons: {len(decision.active_lessons)}")
    for lesson in decision.active_lessons:
        lines.append(f"  - {lesson}")
    if decision.stale_lessons:
        lines.append(f"Stale Lessons: {len(decision.stale_lessons)}")
        for lesson in decision.stale_lessons:
            lines.append(f"  - {lesson}")
    if decision.invalid_entries:
        lines.append(f"Invalid Entries: {len(decision.invalid_entries)}")
        for entry in decision.invalid_entries:
            lines.append(f"  - {entry}")
    lines.append(f"Next: {decision.next_action}")
    return "\n".join(lines)
