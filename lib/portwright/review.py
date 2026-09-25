"""Improvement review: what the cache itself says should be fixed next.

Two sources, both local. The note corpus (`services/`, `failures/`, including the
`_private` and `_hub` roots) answers "which procedure keeps failing"; the usage
"which question do I keep asking". Findings are produced by counting, never by a
model. JEV is optional and may only reorder them.

The ledger records judgment metadata only — service id, profile id, freshness state,
tier, intent, date. Never arguments, results, paths, or credentials. It lives under a
gitignored `_local/` root, is never exported, and deleting it breaks nothing.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .contracts import ContractCatalog
from .jev import JevClient

LOCAL_ROOT = "_local"
LEDGER = "usage.jsonl"
REVIEWS = "reviews"
STALE_DAYS = 90
MAX_LEDGER_LINES = 20000

# kind -> (headline, why it matters, what to do)
KINDS = {
    "procedure-defect": "절차 결함",
    "cache-gap": "캐시 구멍",
    "no-evidence": "증거 없음",
    "stale": "낡음",
    "unlinked-lesson": "미반영 교훈",
}


@dataclass(frozen=True)
class Finding:
    kind: str
    service: str
    summary: str
    action: str
    facts: dict[str, Any] = field(default_factory=dict)
    rank: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Ledger:
    """Append-only preflight metadata. Local, private, disposable."""

    def __init__(self, root: Path):
        self.path = root.resolve() / LOCAL_ROOT / LEDGER

    def record(self, **entry: Any) -> None:
        """Best effort: a ledger failure must never break a preflight."""
        allowed = ("service", "profile", "freshness", "tier", "intent", "procedure")
        row = {"date": date.today().isoformat(), **{k: entry.get(k) for k in allowed}}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            return

    def entries(self, days: int | None = None) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        cutoff = (date.today() - timedelta(days=days)).isoformat() if days else None
        rows: list[dict[str, Any]] = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()[-MAX_LEDGER_LINES:]
        except OSError:
            return []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue  # a truncated write must not poison the review
            if isinstance(row, dict) and (cutoff is None or str(row.get("date", "")) >= cutoff):
                rows.append(row)
        return rows


def _corpus(root: Path) -> tuple[dict[str, dict], dict[str, list[tuple[str, dict]]], dict[str, str], dict[str, str]]:
    """Valid Procedures and active Lessons, plus each Procedure's body.

    `_private` and `_hub` notes count: they are the ones actually used here.
    Only `_drafts` are excluded, because an unpromoted draft is not yet a claim
    about anything. Resolution is private > tracked > hub, so a private override
    is counted once, not twice.
    """
    catalog = ContractCatalog(root)
    procedures: dict[str, dict] = {}
    bodies: dict[str, str] = {}
    paths: dict[str, str] = {}
    lessons: dict[str, list[tuple[str, dict]]] = {}
    for note in catalog.notes():
        if not note.ok:
            continue
        if note.kind == "service":
            procedures[note.data["id"]] = note.data
            paths[note.data["id"]] = note.relative_path
            bodies[note.data["id"]] = "\n".join(note.body)
        elif note.kind == "failure" and note.data.get("status") == "active":
            lessons.setdefault(note.data["service"], []).append((note.path.name, note.data))
    return procedures, lessons, bodies, paths


def collect(root: Path, days: int = 90, today: date | None = None) -> list[Finding]:
    root = root.resolve()
    stamp = today or date.today()
    procedures, lessons, bodies, paths = _corpus(root)
    rows = Ledger(root).entries(days)

    calls: dict[str, int] = {}
    unknown_freshness: dict[str, int] = {}
    misses: dict[str, int] = {}
    for row in rows:
        service = str(row.get("service") or "")
        if not service:
            continue
        calls[service] = calls.get(service, 0) + 1
        if row.get("freshness") == "unknown":
            unknown_freshness[service] = unknown_freshness.get(service, 0) + 1
        if not row.get("procedure"):
            misses[service] = misses.get(service, 0) + 1

    cutoff = (stamp - timedelta(days=days)).isoformat()
    findings: list[Finding] = []

    for service, entries in sorted(lessons.items()):
        recent = [name for name, data in entries if str(data.get("date", "")) >= cutoff]
        unlinked = [name for name, _ in entries if name not in bodies.get(service, "")]
        # A service that failed repeatedly long ago and has been quiet since was fixed;
        # only a pattern that reaches into the window is still a defect.
        if len(recent) >= 2 or (len(entries) >= 3 and len(recent) >= 1):
            findings.append(Finding(
                "procedure-defect", service,
                f"교훈 {len(entries)}건(최근 {days}일 {len(recent)}건). 반복 실패는 운이 아니라 절차가 틀렸다는 신호다.",
                f"{paths[service]} 재도출" if service in paths else f"절차가 없다. memory draft procedure {service}",
                {"lessons": len(entries), "recent": len(recent), "unlinked": unlinked},
                rank=len(entries) * 10 + len(recent) * 5,
            ))
        elif unlinked:
            findings.append(Finding(
                "unlinked-lesson", service,
                f"활성 교훈 {len(unlinked)}건이 절차 본문에서 참조되지 않는다.",
                (f"{paths[service]} 의 '하지 말 것'과 '관련 실패 기록'에 반영"
                 if service in paths else f"절차가 없다. memory draft procedure {service}"),
                {"unlinked": unlinked},
                rank=len(unlinked) * 4,
            ))

    for service, count in sorted(misses.items(), key=lambda item: -item[1]):
        if count >= 2 and service not in procedures:
            findings.append(Finding(
                "cache-gap", service,
                f"절차 없이 preflight {count}회. 매번 맨손으로 다시 알아내고 있다.",
                f"memory draft procedure {service}",
                {"calls": count},
                rank=count * 8,
            ))

    for service, count in sorted(unknown_freshness.items(), key=lambda item: -item[1]):
        data = procedures.get(service)
        if count >= 3 and data and not (data.get("freshness_evidence") or {}).get("url"):
            findings.append(Finding(
                "no-evidence", service,
                f"preflight {count}회가 모두 freshness=unknown. 판정이 매번 사람에게 떠넘겨진다.",
                f"services/{service}.md 에 freshness_evidence.url 추가",
                {"unknown": count},
                rank=count * 3,
            ))

    for service, data in sorted(procedures.items()):
        verified = str(data.get("last_verified", ""))
        age = (stamp - date.fromisoformat(verified)).days if verified[:4].isdigit() else 0
        if age >= STALE_DAYS and calls.get(service, 0) > 0:
            findings.append(Finding(
                "stale", service,
                f"last_verified {age}일 경과, 그 사이 preflight {calls[service]}회.",
                f"현재 문서로 {paths[service]} 재검증 후 last_verified 갱신",
                {"age_days": age, "calls": calls[service]},
                rank=age // 10 + calls[service],
            ))

    findings.sort(key=lambda finding: (-finding.rank, finding.kind, finding.service))
    return findings


def prioritize(findings: list[Finding], jev: JevClient | None) -> tuple[list[Finding], str]:
    """One optional JEV request may reorder findings. It never adds, drops, or edits one."""
    if not findings or jev is None or jev.mode == "none":
        return findings, "deterministic order (no JEV)"
    questions = {
        "urgency": {
            "type": "choice",
            "instructions": "Using `findings`: which one costs the operator the most if it stays unfixed for another month?",
            "criteria": {f"{index}": f"{finding.kind} {finding.service}: {finding.summary}" for index, finding in enumerate(findings)},
        }
    }
    judgment = jev.ask("review-urgency", {"findings": [finding.to_dict() for finding in findings]}, questions)
    answer = judgment.answer("urgency")
    if not answer:
        return findings, f"deterministic order (JEV {judgment.error or 'unavailable'})"
    try:
        top = int(str(answer.get("choice")))
    except (TypeError, ValueError):
        return findings, "deterministic order (JEV answer unreadable)"
    if not 0 <= top < len(findings):
        return findings, "deterministic order (JEV chose out of range)"
    reordered = [findings[top]] + [f for index, f in enumerate(findings) if index != top]
    return reordered, f"JEV moved '{findings[top].service}' first (confidence {answer.get('confidence', '?')})"


def render(findings: list[Finding], *, days: int, ledger_rows: int, order: str) -> str:
    lines = [
        f"portwright review — 최근 {days}일, 장부 {ledger_rows}건, 제안 {len(findings)}건",
        f"순서: {order}",
        "",
    ]
    if not findings:
        lines.append("개선 제안 없음. 반복 실패도, 반복 캐시 미스도 관측되지 않았다.")
        return "\n".join(lines)
    for index, finding in enumerate(findings, 1):
        lines.append(f"{index}. [{KINDS.get(finding.kind, finding.kind)}] {finding.service}")
        lines.append(f"   {finding.summary}")
        lines.append(f"   → {finding.action}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_report(root: Path, text: str, today: date | None = None) -> Path:
    stamp = (today or date.today()).isoformat()
    path = root.resolve() / LOCAL_ROOT / REVIEWS / f"{stamp}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# portwright review {stamp}\n\n```\n{text.rstrip()}\n```\n", encoding="utf-8")
    return path
