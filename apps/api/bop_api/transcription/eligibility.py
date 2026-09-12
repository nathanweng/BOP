"""Pure math for choosing which recording-local windows are released.

The cutoff is the current incident time; a window is released only when its
entire span has already been played on the shared clock. This keeps the
domain rule that no model receives media beyond the cutoff.
"""

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Window:
    local_start: float
    local_end: float
    incident_start: float
    incident_end: float


def _round(value: float, ndigits: int = 6) -> float:
    return round(value, ndigits)


def eligible_windows(
    *,
    incident_position_seconds: float,
    start_offset_seconds: float,
    duration_seconds: float,
    segment_seconds: float,
) -> Iterable[Window]:
    """Yield all cutoff-eligible windows for one recording.

    A window is emitted when either its full span is at or before the local
    cutoff, or when the recording has ended and this is the final partial
    remainder. Partial trailing windows are only released once the recording
    is finished.
    """
    if segment_seconds <= 0 or duration_seconds <= 0:
        return
    local_cutoff = incident_position_seconds - start_offset_seconds
    if local_cutoff <= 0:
        return
    recording_ended = local_cutoff >= duration_seconds
    index = 0
    while True:
        start = index * segment_seconds
        if start >= duration_seconds:
            return
        end = min((index + 1) * segment_seconds, duration_seconds)
        full_window_released = end <= local_cutoff
        trailing_partial_released = recording_ended and end >= duration_seconds
        if not (full_window_released or trailing_partial_released):
            return
        yield Window(
            local_start=_round(start),
            local_end=_round(end),
            incident_start=_round(start + start_offset_seconds),
            incident_end=_round(end + start_offset_seconds),
        )
        index += 1


def incident_window(
    *,
    local_start: float,
    local_end: float,
    start_offset_seconds: float,
) -> tuple[float, float]:
    return (
        _round(local_start + start_offset_seconds),
        _round(local_end + start_offset_seconds),
    )
