from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

from .contracts import ContractCatalog, PACKAGE_ROOT
from .jev import JevClient, Judgment, Pending


Tier = Literal["auto", "confirm", "forbid"]
TIER_ORDER = {"auto": 0, "confirm": 1, "forbid": 2}
TIER_CONFIDENCE = 0.6


@dataclass(frozen=True)
class Profile:
    id: str
    project_path: str
    github_account: str
    env_source: dict[str, Any]
    databases: tuple[str, ...]
    services: tuple[str, ...]
    default_tier: Tier
    host: str
    path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProfileResolution:
    profile: Profile | None
    candidates: tuple[str, ...]
    method: Literal["explicit", "path", "judgment", "none"]
    rationale: str
    judgment: Judgment | None = None
    pending: Pending | None = None


@dataclass(frozen=True)
class Freshness:
    state: Literal["fresh", "stale", "unknown"]
    compared: dict[str, Any]
    action: Literal["none", "derive-required", "verify-required"]
    rationale: str
    pending: Pending | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("pending", None)
        return data


@dataclass(frozen=True)
class TierDecision:
    tier: Tier
    rationale: str
    rule: str | None
    judgment: Judgment | None = None
    pending: Pending | None = None


class ProfileRouter:
    def __init__(self, root: Path, jev: JevClient | None = None):
        self.root = root.resolve()
        self.catalog = ContractCatalog(self.root)
        self.jev = jev or JevClient.from_env()

    def load_all(self) -> tuple[list[Profile], list[str]]:
        directory = self.root / "profiles"
        profiles: list[Profile] = []
        invalid: list[str] = []
        if directory.is_symlink() or not directory.is_dir():
            return profiles, invalid
        for path in sorted(directory.glob("*.md")):
            if path.name == "_TEMPLATE.md":
                continue
            result = self.catalog.validate_note(path)
            relative = path.relative_to(self.root).as_posix()
            if not result.ok or result.data.get("status") == "stale":
                invalid.append(relative)
                continue
            data = result.data
            profiles.append(
                Profile(
                    id=data["id"],
                    project_path=str(Path(data["project_path"]).expanduser().resolve()),
                    github_account=data["github_account"],
                    env_source=dict(data.get("env_source") or {}),
                    databases=tuple(data.get("databases") or []),
                    services=tuple(data.get("services") or []),
                    default_tier=data["default_tier"],
                    host=data["host"],
                    path=relative,
                )
            )
        return profiles, invalid

    def resolve(self, *, cwd: Path, explicit: str | None = None, service_id: str | None = None, judgment: Judgment | None = None) -> ProfileResolution:
        profiles, _ = self.load_all()
        by_id = {profile.id: profile for profile in profiles}
        if explicit:
            profile = by_id.get(explicit)
            if profile is None:
                return ProfileResolution(None, tuple(by_id), "none", f"profile {explicit!r} not found or invalid")
            return ProfileResolution(profile, (explicit,), "explicit", "selected with --profile")

        cwd_text = str(cwd.resolve())
        matches = [
            profile for profile in profiles
            if cwd_text == profile.project_path or cwd_text.startswith(profile.project_path.rstrip("/") + "/")
        ]
        if len(matches) == 1:
            return ProfileResolution(matches[0], (matches[0].id,), "path", "cwd is inside project_path")
        if len(matches) > 1:
            longest = max(len(profile.project_path) for profile in matches)
            deepest = [profile for profile in matches if len(profile.project_path) == longest]
            if len(deepest) == 1:
                return ProfileResolution(deepest[0], tuple(p.id for p in matches), "path", "longest project_path prefix")
            candidates = deepest
        else:
            candidates = [p for p in profiles if service_id and service_id in p.services] or profiles
        if not candidates:
            return ProfileResolution(None, (), "none", "no profiles defined")
        if len(candidates) == 1 and service_id and service_id in candidates[0].services:
            return ProfileResolution(candidates[0], (candidates[0].id,), "path", "only profile listing this service")

        ids = tuple(profile.id for profile in candidates)
        pending = Pending(
            "profile",
            {"cwd": cwd_text, "service": service_id, "candidates": [p.to_dict() for p in candidates]},
            {
                "type": "choice",
                "instructions": "Using `profile.cwd`, `profile.service`, and `profile.candidates`: which profile does this working directory and service belong to?",
                "criteria": {p.id: f"{p.project_path} / {p.github_account}" for p in candidates},
            },
        )
        if judgment is None:
            return ProfileResolution(None, ids, "none", f"ambiguous among {', '.join(ids)}: judgment pending", pending=pending)
        answer = judgment.answer("profile")
        if answer and answer.get("choice") in by_id and float(answer.get("confidence", 0)) >= TIER_CONFIDENCE:
            chosen = by_id[answer["choice"]]
            return ProfileResolution(chosen, ids, "judgment", f"JEV choice confidence {answer['confidence']:.2f}", judgment)
        reason = judgment.error or ("JEV choice below confidence threshold" if answer else "no answer for profile")
        return ProfileResolution(None, ids, "none", f"ambiguous among {', '.join(ids)}: {reason}", judgment)


def assess_freshness(
    procedure: dict[str, Any] | None,
    *,
    evidence_version: str | None,
    evidence_fetched_at: str | None,
    evidence_text: str | None,
    procedure_text: str | None,
    judgment: Judgment | None = None,
) -> Freshness:
    if procedure is None:
        return Freshness("unknown", {}, "derive-required", "no cached Procedure")
    version_tag = str(procedure.get("version_tag", ""))
    last_verified = str(procedure.get("last_verified", ""))
    compared: dict[str, Any] = {
        "version_tag": version_tag,
        "last_verified": last_verified,
        "evidence_version": evidence_version,
        "evidence_fetched_at": evidence_fetched_at,
    }
    if evidence_version is not None:
        if evidence_version == version_tag:
            return Freshness("fresh", compared, "none", "version match only (rule 1); procedure steps not revalidated; JEV not consulted")
        state, action, rationale = "stale", "derive-required", "evidence version differs from version_tag (rule 1)"
    elif evidence_fetched_at is not None:
        try:
            fetched = date.fromisoformat(evidence_fetched_at)
            verified = date.fromisoformat(last_verified)
        except ValueError:
            return Freshness("unknown", compared, "verify-required", "unparseable date in evidence or last_verified (rule 3)")
        if fetched <= verified:
            return Freshness("fresh", compared, "none", "evidence fetched on or before last_verified (rule 2); JEV not consulted")
        state, action, rationale = "unknown", "verify-required", "evidence newer than last_verified; time alone cannot confirm (rule 2)"
    else:
        state, action, rationale = "unknown", "verify-required", "no evidence supplied (rule 3)"

    pending = None
    if evidence_text and procedure_text:
        pending = Pending(
            "contradicts",
            {"cached_procedure": procedure_text, "current_document": evidence_text},
            {
                "type": "noul",
                "instructions": "Does `contradicts.current_document` contradict any step in `contradicts.cached_procedure`?",
                "criteria": {"true": "a step, path, or command no longer matches", "false": "steps still hold"},
            },
        )
        if judgment is not None:
            answer = judgment.answer("contradicts")
            if answer is not None:
                compared["noul_contradicts"] = answer.get("noul")
                rationale += f"; JEV noul contradiction {float(answer.get('noul', 0)):.2f} (rule 4, supporting only)"
            else:
                rationale += f"; JEV unavailable ({judgment.error or 'no answer'}) (rule 4)"
            pending = None
    return Freshness(state, compared, action, rationale, pending)


def load_tier_rules(root: Path) -> list[dict[str, Any]]:
    for base in (root, PACKAGE_ROOT):
        path = base / "install" / "tiers.json"
        if path.is_file():
            return list(json.loads(path.read_text(encoding="utf-8")).get("rules", []))
    return []


def decide_tier(
    *,
    service_id: str,
    intent: str,
    profile: Profile | None,
    procedure: dict[str, Any] | None,
    rules: list[dict[str, Any]],
    judgment: Judgment | None = None,
) -> TierDecision:
    matched = [
        rule for rule in rules
        if rule.get("service") in ("*", service_id) and rule.get("intent") in ("*", intent)
    ]
    forbids = [rule for rule in matched if rule.get("tier") == "forbid"]
    if forbids:
        rule = forbids[0]
        return TierDecision("forbid", rule.get("reason", "explicit forbid rule"), f"{rule['service']}:{rule['intent']}")
    exact = [rule for rule in matched if rule.get("service") == service_id]
    if exact:
        rule = max(exact, key=lambda item: TIER_ORDER.get(item.get("tier", "confirm"), 1))
        return TierDecision(rule["tier"], rule.get("reason", "matched rule"), f"{rule['service']}:{rule['intent']}")
    if matched:
        rule = max(matched, key=lambda item: TIER_ORDER.get(item.get("tier", "confirm"), 1))
        return TierDecision(rule["tier"], rule.get("reason", "matched wildcard rule"), f"{rule['service']}:{rule['intent']}")
    if profile is not None and profile.default_tier == "forbid":
        return TierDecision("forbid", f"profile {profile.id} default_tier", f"profile:{profile.id}")

    pending = Pending(
        "risk",
        {"service": service_id, "intent": intent, "agent_can": (procedure or {}).get("agent_can", []), "profile": profile.id if profile else None},
        {
            "type": "score",
            "instructions": "Using `risk.service`, `risk.intent`, and `risk.agent_can`: how risky is letting an agent run this service call without asking the user first?",
            "criteria": ["read-only or trivially reversible", "writes that can be undone", "irreversible, external, or costly"],
        },
    )
    if judgment is None:
        return TierDecision("confirm", "judgment pending", None, pending=pending)
    answer = judgment.answer("risk")
    if answer is not None and float(answer.get("confidence", 0)) >= TIER_CONFIDENCE:
        level = round(float(answer.get("score", 1)))
        tier: Tier = ("auto", "confirm", "forbid")[max(0, min(2, level))]
        if profile is not None and TIER_ORDER[profile.default_tier] > TIER_ORDER[tier]:
            tier = profile.default_tier
        return TierDecision(tier, f"JEV risk score {float(answer.get('score', 1)):.2f}, confidence {float(answer['confidence']):.2f}", "jev", judgment)
    reason = judgment.error or ("low confidence" if answer else "no answer for risk")
    if profile is not None and TIER_ORDER[profile.default_tier] > TIER_ORDER["confirm"]:
        return TierDecision(profile.default_tier, f"profile {profile.id} default_tier; JEV {reason}", f"profile:{profile.id}", judgment)
    return TierDecision("confirm", f"no rule matched; JEV {reason}", None, judgment)
