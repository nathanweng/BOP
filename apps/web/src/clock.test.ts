import { describe, expect, it } from 'vitest';
import { acceptClockSample, formatTime, incidentPosition, recordingPosition } from './clock';
import type { ClockSample } from './types';

const initial: ClockSample = {
  receivedAt: 1_000,
  playback: {
    run_id: 'first-run', state: 'playing', position_seconds: 3,
    duration_seconds: 20, server_time: '2026-09-12T12:00:00Z', revision: 2,
  },
};

describe('server-authoritative incident clock', () => {
  it('extrapolates from the monotonic receipt time and stops at the incident duration', () => {
    expect(incidentPosition(initial, 3_250)).toBe(5.25);
    expect(incidentPosition(initial, 100_000)).toBe(20);
    expect(incidentPosition(initial, 500)).toBe(3);
  });

  it('never advances paused or ended snapshots', () => {
    for (const state of ['paused', 'ended'] as const) {
      expect(incidentPosition({ ...initial, playback: { ...initial.playback, state } }, 10_000)).toBe(3);
    }
  });

  it('ignores delayed snapshots that would overwrite a restart', () => {
    const restarted = { ...initial, playback: { ...initial.playback, revision: 3, run_id: 'new-run', position_seconds: 0 } };
    expect(acceptClockSample(restarted, initial)).toBe(restarted);
    expect(acceptClockSample(initial, restarted)).toBe(restarted);
  });

  it('ignores out-of-order polls at the same revision', () => {
    const old = { ...initial, playback: { ...initial.playback, server_time: '2026-09-12T11:59:59Z' } };
    expect(acceptClockSample(initial, old)).toBe(initial);
  });
});

describe('manual recording alignment', () => {
  const recording = { start_offset_seconds: 2.5, duration_seconds: 10 };

  it('does not expose a feed before its configured start', () => {
    expect(recordingPosition(recording, 0)).toEqual({ state: 'waiting', seconds: 0 });
    expect(recordingPosition(recording, 2.499)).toEqual({ state: 'waiting', seconds: 0 });
    expect(recordingPosition(recording, 2.5)).toEqual({ state: 'active', seconds: 0 });
  });

  it('maps incident time to recording-local time and marks exact end boundaries', () => {
    expect(recordingPosition(recording, 7.75)).toEqual({ state: 'active', seconds: 5.25 });
    expect(recordingPosition(recording, 12.5)).toEqual({ state: 'ended', seconds: 10 });
    expect(recordingPosition(recording, 100)).toEqual({ state: 'ended', seconds: 10 });
  });

  it('formats long manual offsets without truncating hours', () => {
    expect(formatTime(0)).toBe('00:00.0');
    expect(formatTime(61.27)).toBe('01:01.2');
    expect(formatTime(3600.5)).toBe('1:00:00.5');
  });
});
