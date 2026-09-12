"""Maintain an immutable-timestamp event log from released transcripts."""
import json
import logging
from uuid import uuid4, uuid5, NAMESPACE_URL
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, ValidationError


logger = logging.getLogger(__name__)


class EventProviderError(ValueError):
    """Safe, user-visible provider failure. Other exceptions stay generic in the API."""

# Hand-written so OpenRouter/OpenAI strict JSON schema accepts it.
# Pydantic's model_json_schema() emits minLength/maxItems/$defs that those endpoints reject.
EVENT_CHANGES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["updates", "new_events"],
    "properties": {
        "updates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["event_id", "supporting_segment_id", "status", "reason"],
                "properties": {
                    "event_id": {"type": "string"},
                    "supporting_segment_id": {"type": "string"},
                    "status": {"type": "string", "enum": ["current", "outdated", "disproven"]},
                    "reason": {"type": "string"},
                },
            },
        },
        "new_events": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["segment_id", "id", "title"],
                "properties": {
                    "segment_id": {"type": "string"},
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                },
            },
        },
    },
}


class EventUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    event_id: str
    supporting_segment_id: str
    status: Literal["current", "outdated", "disproven"]
    reason: str = Field(min_length=1, max_length=500)


class NewEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    segment_id: str
    id: str
    title: str = Field(min_length=1, max_length=200)


class EventChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")
    updates: list[EventUpdate] = Field(max_length=500)
    new_events: list[NewEvent] = Field(max_length=500)


VALID_STATUSES = {"current", "outdated", "disproven"}


def normalize_events(events):
    """Read histories created before stable IDs and statuses were introduced."""
    return [
        {**event, "id": event.get("id") or str(uuid5(NAMESPACE_URL, event["segment_id"] + event["title"])),
         "status": event.get("status") if event.get("status") in VALID_STATUSES else "current"}
        for event in events
    ]


def generate_events(settings, segments, existing_events=()):
    sources = {segment.id: segment for segment in segments}
    existing = normalize_events(existing_events)
    content = json.dumps({
        "existing_events": existing,
        "transcript_sources": [
            {"segment_id": segment.id, "recording_id": segment.recording_id,
             "timestamp_seconds": segment.incident_start_seconds, "text": segment.text}
            for segment in segments
        ],
    })
    if len(content) > 600_000:
        raise EventProviderError("Too much transcript for one history request.")
    request = Request(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/local/bop",
            "X-Title": "BOP (Bizzy Ops)",
        },
        data=json.dumps({
            "model": settings.openrouter_model,
            "temperature": 0.2,
            "max_tokens": 12000,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "event_changes", "strict": True, "schema": EVENT_CHANGES_SCHEMA,
            }},
            "provider": {"require_parameters": True},
            "messages": [
                {"role": "system", "content": (
                    'Update the live fact log from new transcripts only. existing_events is saved; transcript_sources '
                    'are newly completed segments (may arrive late or out of order). Return JSON only: '
                    '{"new_events":[{"id":"new-1","segment_id":"supplied source ID","title":"Short title"}],'
                    '"updates":[{"event_id":"saved ID or new-1","supporting_segment_id":"supplied source ID",'
                    '"status":"current|outdated|disproven","reason":"One short sentence"}]} . '
                    'Titles: under 12 words. Reasons: one sentence. current entries are the live facts right now. '
                    'Each new_event MUST use the segment_id of the transcript that states that fact. Do not attach '
                    'several facts to the first or same segment if later sources support them. Split separate facts. '
                    'If a detail is unclear, use Possible/Appears/Speaker reports or skip it; do not invent. '
                    'RED (disproven) for direct contradictions of objective, checkable details or explicit factual '
                    'retractions. Do not require an admission of lying or independent corroboration before marking '
                    'a direct factual contradiction red. Compare new sources with saved source_text as well as titles, '
                    'including titles phrased as reports. Example: the same clearly identified driver first gives '
                    'the name John Smith, then gives Michael Jones as his name: mark the original name claim red '
                    'and cite the later source, explaining the incompatible names without deciding which is real '
                    'or asserting deliberate deception. Different speakers, aliases, unclear attribution, and '
                    'transcription ambiguity alone do not establish an objective contradiction; use yellow. '
                    'SUBJECTIVE opinions, impressions, credibility, intent, demeanor, and interpretations ALWAYS '
                    'remain YELLOW (outdated), never red or current; humans must decide them even if another '
                    'speaker disagrees or retracts an opinion. For each new subjective event, include an update '
                    'targeting its new-N ID with status outdated and its own segment as support, explaining that '
                    'human judgment is needed. Apply this rule to saved subjective entries too. '
                    'YELLOW also covers incomplete, uncertain, qualified, or superseded information. '
                    'Example: "He has a knife" then "It is a phone, not a knife" → red the knife claim. '
                    '"He seems suspicious" then "He seems harmless" → yellow, human judgment required. '
                    '"Nobody inside" then "Upstairs not checked" → yellow. "Help requested" then "Help arrived" → '
                    'new current fact for arrival, yellow on the waiting-for-help fact, not red. '
                    'Cite the supporting segment on every update. Never invent contradictions to add color. '
                    'Keep existing titles. Do not merge facts onto one timestamp. Update the original entry instead of '
                    'duplicating a correction. New events use ids like new-1. Omit unchanged saved entries. '
                    'Empty arrays if nothing new. Cite only supplied segment IDs. Camera labels are not speakers.'
                )},
                {"role": "user", "content": content},
            ],
        }).encode(),
    )
    body = _provider_response(request)
    choices = body.get("choices") or []
    if not choices:
        raise EventProviderError("OpenRouter returned no completion.")
    choice = choices[0]
    if choice.get("finish_reason") == "length":
        raise EventProviderError("Provider output was truncated; no progress saved.")
    payload = _message_text(choice.get("message") or {})
    try:
        changes = EventChanges.model_validate_json(payload)
    except ValidationError:
        raise EventProviderError("OpenRouter returned JSON that did not match the event schema.") from None
    known = {event["id"]: event for event in existing}
    # Models occasionally cite a source segment instead of the event UUID. Accept
    # that shorthand only when it identifies exactly one saved event.
    by_segment = {}
    for event in existing:
        by_segment.setdefault(event["segment_id"], []).append(event)
    for segment_id, matches in by_segment.items():
        if len(matches) == 1:
            known.setdefault(segment_id, matches[0])
    for candidate in changes.new_events:
        if candidate.segment_id not in sources:
            raise ValueError("Provider returned an unknown source.")
        identity = (candidate.segment_id, candidate.title.casefold())
        if candidate.id and candidate.id in known:
            raise ValueError("Provider returned a duplicate event ID.")
        duplicate = next((e for e in existing if (e["segment_id"], e["title"].casefold()) == identity), None)
        if duplicate:
            if candidate.id:
                known[candidate.id] = duplicate
            continue
        source = sources[candidate.segment_id]
        event = {"id": str(uuid4()), "segment_id": source.id, "recording_id": source.recording_id,
                         "timestamp_seconds": source.incident_start_seconds,
                         "local_seconds": source.local_start_seconds, "title": candidate.title,
                         "status": "current", "source_text": source.text}
        existing.append(event)
        if candidate.id:
            known[candidate.id] = event
    for change in changes.updates:
        source = sources.get(change.supporting_segment_id)
        if change.event_id not in known or source is None:
            logger.warning(
                "Ignoring unsupported event update (known_event=%s, known_source=%s)",
                change.event_id in known,
                source is not None,
            )
            continue
        event = known[change.event_id]
        evidence = {"status": change.status, "reason": change.reason, "segment_id": source.id,
                    "recording_id": source.recording_id, "timestamp_seconds": source.incident_start_seconds,
                    "local_seconds": source.local_start_seconds, "source_text": source.text}
        audit = list(event.get("assessment_history", []))
        if not audit or audit[-1] != evidence:
            audit.append(evidence)
        event.update(status=change.status, status_reason=change.reason, assessment_history=audit,
                     status_timestamp_seconds=source.incident_start_seconds,
                     status_local_seconds=source.local_start_seconds, status_recording_id=source.recording_id)
    return sorted(existing, key=lambda event: (event["timestamp_seconds"], event["recording_id"], event["id"]))


def _provider_response(request):
    try:
        with urlopen(request, timeout=180) as response:
            return json.load(response)
    except HTTPError as error:
        detail = _provider_error_message(error)
        logger.warning("OpenRouter request failed (%s): %s", error.code, detail)
        raise EventProviderError(_user_error(error.code, detail)) from None
    except URLError as error:
        logger.warning("OpenRouter request failed: %s", error.reason)
        raise EventProviderError("Could not reach OpenRouter. Check network access from the API.") from None


def _provider_error_message(error):
    raw = error.read() if error.fp else b""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return error.reason or "request failed"
    message = payload.get("error")
    if isinstance(message, dict):
        return str(message.get("message") or message.get("code") or error.reason)
    if isinstance(message, str):
        return message
    return error.reason or "request failed"


def _user_error(status, detail):
    detail = (detail or "").casefold()
    if status in (401, 403):
        return "OpenRouter rejected the API key. Check OPENROUTER_API_KEY and restart the API."
    if status == 402:
        return "OpenRouter credits are exhausted."
    if status == 429:
        return "OpenRouter rate-limited the request; analysis will retry."
    if "no endpoints" in detail or "structured" in detail:
        return "The configured OpenRouter model does not support structured JSON output."
    return "Event analysis failed. Check the OpenRouter model, key or credits. Saved events are retained."


def _message_text(message):
    content = message.get("content")
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                parts.append(part.get("text") or "")
        content = "\n".join(parts)
    if not isinstance(content, str) or not content.strip():
        content = message.get("reasoning_content") or ""
    if not isinstance(content, str) or not content.strip():
        raise EventProviderError("OpenRouter returned an empty completion.")
    content = content.strip()
    if content.startswith("```"):
        content = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    start, end = content.find("{"), content.rfind("}")
    if start != -1 and end > start:
        content = content[start:end + 1]
    return content
