from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal


ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
KEY_ENV = "TYPESAFE_API_KEY"
FIXTURES_ENV = "PORTWRIGHT_JEV_FIXTURES"


@dataclass(frozen=True)
class Judgment:
    status: Literal["ok", "unknown"]
    source: Literal["fixture", "live", "none"]
    answers: dict[str, dict[str, Any]] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def answer(self, key: str) -> dict[str, Any] | None:
        return self.answers.get(key) if self.status == "ok" else None


def _unknown(error: str) -> Judgment:
    return Judgment(status="unknown", source="none", error=error)


class JevClient:
    """TypeSafe Jev caller. Fixture mode replays recorded responses and never goes live.

    ``before_send`` is the hub's pre-flight seam: it receives the exact serialized
    request-body bytes before the Authorization header is built, and the transport
    sends those identical bytes. Recording a request is incompatible with it, so a
    hub caller cannot leave a recording path behind.
    """

    def __init__(
        self,
        api_key: str | None = None,
        fixtures: Path | None = None,
        record: bool = False,
        before_send: Callable[[bytes], None] | None = None,
    ):
        if before_send is not None and record:
            raise ValueError("request recording is not allowed with a before_send hook")
        self._api_key = api_key
        self.fixtures = fixtures
        self.record = record and fixtures is not None
        self.before_send = before_send

    @classmethod
    def from_env(cls, before_send: Callable[[bytes], None] | None = None) -> "JevClient":
        fixtures = os.environ.get(FIXTURES_ENV)
        return cls(
            api_key=None if fixtures else os.environ.get(KEY_ENV),
            fixtures=Path(fixtures).expanduser() if fixtures else None,
            record=bool(os.environ.get("PORTWRIGHT_JEV_RECORD")),
            before_send=before_send,
        )

    @property
    def mode(self) -> str:
        if self.fixtures is not None and not self.record:
            return "fixture"
        return "live" if self._api_key else "none"

    def ask(self, fixture_id: str, state: Any, questions: dict[str, dict[str, Any]]) -> Judgment:
        request = {"state": state, "model": MODEL, "questions": questions}
        if self.fixtures is not None and not self.record:
            return self._replay(fixture_id, request)
        if not self._api_key:
            return _unknown("credential missing")
        judgment = self._live(request)
        if self.record and judgment.status == "ok":
            self.fixtures.mkdir(parents=True, exist_ok=True)
            payload = {"request": request, "response": {"answers": judgment.answers, "usage": judgment.usage}}
            (self.fixtures / f"{fixture_id}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        return judgment

    def _replay(self, fixture_id: str, request: dict[str, Any]) -> Judgment:
        path = self.fixtures / f"{fixture_id}.json"
        if not path.is_file():
            return _unknown(f"fixture missing: {fixture_id}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            response = payload["response"]
            return Judgment(
                status="ok",
                source="fixture",
                answers=dict(response["answers"]),
                usage=dict(response.get("usage", {})),
                latency_ms=0,
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            return _unknown(f"fixture unreadable: {fixture_id} ({type(error).__name__})")

    def _live(self, request: dict[str, Any]) -> Judgment:
        body = json.dumps(request).encode("utf-8")
        if self.before_send is not None:
            self.before_send(body)
        http = urllib.request.Request(
            ENDPOINT,
            data=body,
            method="POST",
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(http, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return _unknown(f"http {error.code}")
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            return _unknown(f"request failed ({type(error).__name__})")
        latency = int((time.monotonic() - started) * 1000)
        try:
            return Judgment(
                status="ok",
                source="live",
                answers=dict(payload["answers"]),
                usage=dict(payload.get("usage", {})),
                latency_ms=latency,
            )
        except (KeyError, TypeError):
            return _unknown("response malformed")


@dataclass
class Pending:
    """A closed question a decider wants answered; the batch sends all of them in one request."""

    key: str
    state: Any
    question: dict[str, Any]


class JevBatch:
    """Collect questions from several deciders, send one request, hand back one Judgment."""

    def __init__(self, client: JevClient, fixture_id: str):
        self.client = client
        self.fixture_id = fixture_id
        self.state: dict[str, Any] = {}
        self.questions: dict[str, dict[str, Any]] = {}

    def add(self, pending: Pending | None) -> None:
        if pending is not None:
            self.state[pending.key] = pending.state
            self.questions[pending.key] = pending.question

    def commit(self) -> Judgment:
        if not self.questions:
            return Judgment(status="ok", source="none")
        return self.client.ask(self.fixture_id, self.state, self.questions)
