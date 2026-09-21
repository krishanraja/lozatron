"""The analysis layer, and the wall around it.

Every incident in this system's history traces to one of two causes: a rule that
lived in prompt text instead of in code (the June 2026 freshness crisis), or a
model permitted to control its own output format (the September 2026 formatting
collapse). So the contract here is narrow and enforced in code:

  The model receives titles, outlets, summaries and an integer age. It is never
  sent a URL or a timestamp, so it cannot choose a link or a date -- not because
  it is instructed not to, but because it has no way to. It returns JSON matching
  a strict schema. Anything that looks like markup, a link or a second paragraph
  is rejected. It does not decide what ships, how many ship, or in what order.

`analyse` never raises. Returning None is an ordinary outcome meaning "ship the
deterministic brief", and the reason is always named rather than swallowed.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable

from . import http

ENDPOINT = "https://api.openai.com/v1/chat/completions"

# Tried in order. A model that no longer exists moves to the next rather than
# taking the run down -- the May 2026 retired-alias incident, where a dead model
# id put an entire provider into cooldown, is the reason this is a chain and not
# a constant. Override with LOZ_ANALYST_MODELS.
DEFAULT_MODELS = ("gpt-5", "gpt-4.1", "gpt-4o")

PROMPT_VERSION = 1
MAX_FIELD = 400
MAX_LEDE = 600

SYSTEM = (
    "You are the analyst for a daily creator-economy intelligence brief read by "
    "the president of a media company. For each story you are given, state what "
    "happened, why it matters to a creator-business executive, and the next "
    "observable signal worth watching. Be concrete and specific to the story; "
    "never restate the summary you were given. Judge novelty against the list of "
    "items already delivered. Write plain sentences. Each field is a single "
    "paragraph with no formatting, no lists, no links and no line breaks."
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["lede", "stories"],
    "properties": {
        "lede": {"type": "string"},
        "stories": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id", "what_happened", "why_it_matters", "what_to_watch",
                    "novelty", "impact", "confidence", "entities", "needs_decision",
                ],
                "properties": {
                    "id": {"type": "string"},
                    "what_happened": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                    "what_to_watch": {"type": "string"},
                    "novelty": {"type": "number"},
                    "impact": {"type": "number"},
                    "confidence": {"type": "number"},
                    "entities": {"type": "array", "items": {"type": "string"}},
                    "needs_decision": {"type": "boolean"},
                },
            },
        },
    },
}

# Markup, links and multi-paragraph prose are rejected outright. The renderer
# escapes everything anyway; this is the second, independent wall.
FORBIDDEN = re.compile(r"https?://|www\.|\]\(|<[a-zA-Z]|&#|\*\*|__|##|`|^\s*[-*]\s", re.MULTILINE)


def rejected(raw: object) -> bool:
    """Judge the model's text as it was written, not as it cleans up.

    Whitespace normalisation has to happen after this check, never before: a
    bullet on its own line collapses into innocuous-looking inline text, and the
    contract is one paragraph per field, so a line break is itself a rejection.
    """
    text = str(raw or "")
    return "\n" in text or "\r" in text or bool(FORBIDDEN.search(text))


class AnalystError(RuntimeError):
    """Carries a named reason so a failure can never be reported generically."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


@dataclasses.dataclass(slots=True, frozen=True)
class StoryAnalysis:
    cluster_key: str
    what_happened: str
    why_it_matters: str
    what_to_watch: str
    novelty: float
    impact: float
    confidence: float
    entities: tuple[str, ...]
    needs_decision: bool


@dataclasses.dataclass(slots=True, frozen=True)
class Analysis:
    lede: str
    per_cluster: dict[str, StoryAnalysis]
    model: str
    usage: dict[str, Any]
    prompt_version: int = PROMPT_VERSION
    dropped: tuple[str, ...] = ()

    @property
    def partial(self) -> bool:
        return bool(self.dropped)


class LlmLedger:
    """Reserve-then-settle spend ledger, mirroring the Apify cap pattern.

    Reserved before the call, not after, so a timeout that still billed cannot
    leave the cap looking untouched and invite an immediate retry.
    """

    RETENTION_DAYS = 45

    def __init__(self, path: Path, day: str | None = None):
        self.path = path
        self.day = day or dt.datetime.now(dt.timezone.utc).date().isoformat()
        self.rows: list[dict[str, Any]] = []

    def load(self) -> "LlmLedger":
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.rows = [row for row in data.get("runs", []) if isinstance(row, dict)]
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            self.rows = []
        return self

    def spent(self) -> float:
        return round(sum(float(r.get("usd", 0)) for r in self.rows if r.get("date") == self.day), 4)

    def permits(self, estimate_usd: float, cap_usd: float) -> bool:
        return self.spent() + estimate_usd <= cap_usd

    def reserve(self, estimate_usd: float, model: str) -> int:
        self.rows.append({"date": self.day, "model": model, "usd": estimate_usd, "status": "reserved"})
        self.save()
        return len(self.rows) - 1

    def settle(self, row_id: int, *, usd: float | None, status: str, usage: dict | None = None) -> None:
        if 0 <= row_id < len(self.rows):
            row = self.rows[row_id]
            row["status"] = status
            if usd is not None:
                row["usd"] = round(usd, 6)
            if usage:
                row["usage"] = usage
            self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        cutoff = (dt.date.fromisoformat(self.day) - dt.timedelta(days=self.RETENTION_DAYS)).isoformat()
        self.rows = [r for r in self.rows if str(r.get("date", "")) >= cutoff]
        self.path.write_text(
            json.dumps({"version": 1, "runs": self.rows}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def models() -> tuple[str, ...]:
    raw = os.environ.get("LOZ_ANALYST_MODELS", "").strip()
    return tuple(m.strip() for m in raw.split(",") if m.strip()) or DEFAULT_MODELS


def _clean(value: object, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _payload(clusters: Iterable[Any], recent: list[dict]) -> str:
    """What the model sees. Deliberately no URL and no timestamp."""
    return json.dumps(
        {
            "already_delivered": [
                {"title": _clean(item.get("title"), 160), "date": str(item.get("date", ""))}
                for item in recent[:60]
            ],
            "stories": [
                {
                    "id": cluster.key,
                    "title": _clean(cluster.leader.title, 300),
                    "outlets": cluster.outlets,
                    "age_hours": cluster.age_hours,
                    "summary": _clean(cluster.leader.summary, 800),
                }
                for cluster in clusters
            ],
        },
        ensure_ascii=False,
    )


def _classify(exc: BaseException) -> str:
    status = getattr(exc, "status", None)
    text = str(exc).lower()
    if status == 401 or "invalid_api_key" in text:
        return "auth_failed"
    if status == 402 or "insufficient_quota" in text or "billing" in text:
        return "quota_exhausted"
    if status == 404 or "model_not_found" in text or "does not exist" in text:
        return "model_not_found"
    if status == 429:
        return "rate_limited"
    if status and 500 <= status < 600:
        return "provider_error"
    return "request_failed"


def _request(model: str, payload: str, api_key: str, timeout: float) -> dict[str, Any]:
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": payload},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "brief_analysis", "strict": True, "schema": SCHEMA},
        },
    }).encode("utf-8")
    return http.request_json(
        ENDPOINT,
        method="POST",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=timeout,
        retries=2,
    )


def validate(raw: dict[str, Any], known: set[str]) -> tuple[str, dict[str, StoryAnalysis], list[str]]:
    """Second wall. Rejects individual stories rather than the whole response.

    A hallucinated cluster id, a link, any markup, or a field echoing the input
    drops that story and keeps the rest. Server-side schema enforcement is a
    convenience; this is the contract.
    """
    lede = "" if rejected(raw.get("lede")) else _clean(raw.get("lede"), MAX_LEDE)
    if len(lede) < 40:
        lede = ""

    kept: dict[str, StoryAnalysis] = {}
    dropped: list[str] = []
    for item in raw.get("stories") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id", ""))
        if key not in known:
            dropped.append(key or "<missing id>")
            continue
        names = ("what_happened", "why_it_matters", "what_to_watch")
        if any(rejected(item.get(name)) for name in names):
            dropped.append(key)
            continue
        fields = {name: _clean(item.get(name), MAX_FIELD) for name in names}
        if any(len(value) < 15 for value in fields.values()):
            dropped.append(key)
            continue
        try:
            scores = {
                name: max(0.0, min(1.0, float(item.get(name, 0))))
                for name in ("novelty", "impact", "confidence")
            }
        except (TypeError, ValueError):
            dropped.append(key)
            continue
        kept[key] = StoryAnalysis(
            cluster_key=key,
            **fields,
            **scores,
            entities=tuple(_clean(e, 60) for e in (item.get("entities") or [])[:6] if _clean(e, 60)),
            needs_decision=bool(item.get("needs_decision")),
        )
    return lede, kept, dropped


def analyse(
    clusters: list[Any],
    recent: list[dict],
    *,
    ledger: LlmLedger,
    cap_usd: float = 2.00,
    timeout: float = 90.0,
    estimate_usd: float = 0.25,
) -> tuple[Analysis | None, str]:
    """Analyse the selected clusters. Returns (analysis, reason).

    Never raises. `(None, reason)` means ship the deterministic brief and say
    why in the step summary.
    """
    if not clusters:
        return None, "no_stories"
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None, "no_api_key"
    if not ledger.permits(estimate_usd, cap_usd):
        return None, "budget_exhausted"

    payload = _payload(clusters, recent)
    known = {cluster.key for cluster in clusters}
    last = "request_failed"

    for model in models():
        row = ledger.reserve(estimate_usd, model)
        try:
            response = _request(model, payload, api_key, timeout)
        except Exception as exc:  # noqa: BLE001 - classification is the point
            reason = _classify(exc)
            ledger.settle(row, usd=0.0 if reason == "model_not_found" else estimate_usd, status=reason)
            last = reason
            if reason == "model_not_found":
                continue          # a retired alias is not an outage; try the next model
            return None, reason

        usage = response.get("usage") or {}
        ledger.settle(row, usd=estimate_usd, status="succeeded", usage=usage)
        try:
            content = response["choices"][0]["message"]["content"]
            raw = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            return None, "unparseable_response"

        lede, kept, dropped = validate(raw, known)
        if not kept:
            return None, "schema_rejected"
        return Analysis(
            lede=lede,
            per_cluster=kept,
            model=model,
            usage=usage,
            dropped=tuple(dropped),
        ), "partial_analysis" if dropped else "ok"

    return None, last
