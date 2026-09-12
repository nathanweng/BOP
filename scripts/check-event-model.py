"""Opt-in live provider check using synthetic text only; never writes incident data."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
from bop_api.config import Settings
from bop_api.events import generate_events


def source(identity, second, text):
    return SimpleNamespace(id=identity, recording_id="synthetic-camera",
                           incident_start_seconds=second, local_start_seconds=second, text=text)


settings = Settings.from_env()
if not settings.openrouter_api_key:
    raise SystemExit("OPENROUTER_API_KEY is required.")
initial = [
    {"id": "weapon-report", "segment_id": "initial-1", "recording_id": "synthetic-camera",
     "timestamp_seconds": 0, "local_seconds": 0, "title": "Speaker reports a knife in the man's hand",
     "status": "current", "source_text": "He has a knife in his hand."},
    {"id": "empty-building", "segment_id": "initial-2", "recording_id": "synthetic-camera",
     "timestamp_seconds": 10, "local_seconds": 10, "title": "Speaker reports nobody inside the building",
     "status": "current", "source_text": "There is nobody inside the building."},
]
try:
    result = generate_events(settings, [
        source("correction", 20, "Correction to my earlier report: it is a phone in his hand, not a knife. I was mistaken."),
        source("qualifier", 30, "About my claim that nobody is inside: we have only checked downstairs. Upstairs has not been checked yet."),
        source("development", 40, "An ambulance has just arrived at the east entrance. A responder is asking the driver for a first aid kit."),
    ], initial)
except Exception as error:
    if isinstance(error, ValidationError):
        print(json.dumps(error.errors(include_input=False, include_url=False)))
    raise SystemExit(f"Live provider check failed ({type(error).__name__}); no source or credentials logged.") from None
by_id = {event["id"]: event for event in result}
assert by_id["weapon-report"]["status"] == "disproven", "Expected red for retracted knife report"
assert by_id["empty-building"]["status"] == "outdated", "Expected yellow for incomplete building check"
assert by_id["weapon-report"]["timestamp_seconds"] == 0
assert by_id["empty-building"]["timestamp_seconds"] == 10
assert len(result) > 2, "Expected new developments to be added"
print(json.dumps({"model": settings.openrouter_model, "red": True, "yellow": True,
                  "original_timestamps_preserved": True, "new_events": len(result) - 2}))
