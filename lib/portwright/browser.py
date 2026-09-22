from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .jev import JevClient, Judgment


MIN_CONFIDENCE = 0.6
MIN_MARGIN = 0.15


@dataclass(frozen=True)
class MenuSelection:
    accepted: bool
    candidate_ref: str | None
    value_location: str | None
    confidence: float
    reject_reason: str | None
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalise(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    clean: list[dict[str, Any]] = []
    for item in candidates:
        ref = str(item.get("ref", "")).strip()
        name = str(item.get("name", "")).strip()
        if not ref or not name or ref in seen:
            continue
        seen.add(ref)
        clean.append({
            "ref": ref,
            "role": str(item.get("role", "")).strip(),
            "name": name,
            "url": item.get("url"),
            "snippet": item.get("snippet"),
        })
    return clean


def _location(candidate: dict[str, Any]) -> str:
    return candidate.get("url") or f"{candidate['role'] or 'element'}[ref={candidate['ref']}]"


def select_menu(
    candidates: list[dict[str, Any]],
    goal: str,
    *,
    jev: JevClient,
    fixture_id: str,
    min_confidence: float = MIN_CONFIDENCE,
    min_margin: float = MIN_MARGIN,
) -> MenuSelection:
    """Pick the one candidate that reaches `goal`. Extraction is the browser's job; this only selects."""
    clean = _normalise(candidates)
    if not clean:
        return MenuSelection(False, None, None, 0.0, "no candidates", "none")
    exact = [c for c in clean if c["name"].casefold() == goal.strip().casefold()]
    if len(exact) == 1:
        return MenuSelection(True, exact[0]["ref"], _location(exact[0]), 1.0, None, "exact-name")

    judgment: Judgment = jev.ask(
        fixture_id,
        {"goal": goal, "candidates": clean},
        {
            "target": {
                "type": "choice",
                "instructions": "Which candidate should be opened to reach `goal`? Pick `none` if no candidate leads there.",
                "criteria": {**{c["ref"]: f"{c['role']} {c['name']}".strip() for c in clean}, "none": "no candidate matches the goal"},
            }
        },
    )
    answer = judgment.answer("target")
    if answer is None:
        return MenuSelection(False, None, None, 0.0, f"judgment unavailable: {judgment.error}", "none")
    probabilities = {k: float(v) for k, v in (answer.get("probabilities") or {}).items()}
    ranked = sorted(probabilities.items(), key=lambda kv: kv[1], reverse=True)
    choice = str(answer.get("choice", ""))
    confidence = float(answer.get("confidence", 0.0))
    if choice == "none" or choice not in {c["ref"] for c in clean}:
        return MenuSelection(False, None, None, confidence, "model chose none", judgment.source)
    if confidence < min_confidence:
        return MenuSelection(False, choice, None, confidence, f"confidence {confidence:.2f} below {min_confidence}", judgment.source)
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < min_margin:
        return MenuSelection(False, choice, None, confidence, f"top-2 margin {ranked[0][1] - ranked[1][1]:.2f} below {min_margin}", judgment.source)
    chosen = next(c for c in clean if c["ref"] == choice)
    return MenuSelection(True, choice, _location(chosen), confidence, None, judgment.source)
