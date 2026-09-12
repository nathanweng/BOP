from datetime import UTC, datetime

from .models import PlaybackRun, Recording
from .schemas import PlaybackView


def utc(value: datetime) -> datetime:
    # PostgreSQL preserves timezone; SQLite returns naive UTC datetimes.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def playback_view(run: PlaybackRun, recordings: list[Recording], now: datetime) -> PlaybackView:
    duration = max((r.start_offset_seconds + r.duration_seconds for r in recordings), default=0)
    position = run.position_seconds
    speed = run.speed if run.speed in (1, 2, 4) else 1
    if run.state == "playing":
        position += max(0, (utc(now) - utc(run.anchor_at)).total_seconds()) * speed
    position = min(duration, position)
    state = "ended" if duration > 0 and position >= duration else run.state
    return PlaybackView(
        run_id=run.id,
        state=state,
        position_seconds=position,
        duration_seconds=duration,
        speed=speed,
        server_time=utc(now),
        revision=run.revision,
    )

