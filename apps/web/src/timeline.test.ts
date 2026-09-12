import { describe, expect, it } from 'vitest';
import { processedThrough } from './timeline';
import type { TranscriptStatus } from './types';

const recording = (id: string, start = 0, duration = 30) => ({
  id,
  start_offset_seconds: start,
  duration_seconds: duration,
});

const segment = (start: number, end: number, status: TranscriptStatus = 'completed') => ({
  incident_start_seconds: start,
  incident_end_seconds: end,
  status,
});

describe('processed timeline coverage', () => {
  it('advances through contiguous completed and empty windows', () => {
    expect(processedThrough(
      [recording('camera-a')],
      [{ recording_id: 'camera-a', segments: [segment(0, 10), segment(10, 20, 'empty')] }],
      30,
    )).toBe(20);
  });

  it('stops at gaps and does not count queued, processing, or failed windows', () => {
    for (const status of ['queued', 'processing', 'failed'] as const) {
      expect(processedThrough(
        [recording('camera-a')],
        [{ recording_id: 'camera-a', segments: [segment(0, 10, status), segment(10, 20)] }],
        30,
      )).toBe(0);
    }
  });

  it('waits for every camera that is active at the cutoff', () => {
    expect(processedThrough(
      [recording('camera-a', 0, 10), recording('camera-b', 3, 10)],
      [
        { recording_id: 'camera-a', segments: [segment(0, 10)] },
        { recording_id: 'camera-b', segments: [segment(3, 8)] },
      ],
      13,
    )).toBe(8);
  });

  it('does not let a fully processed shorter recording cap longer coverage', () => {
    expect(processedThrough(
      [recording('camera-a', 0, 5), recording('camera-b', 0, 10)],
      [
        { recording_id: 'camera-a', segments: [segment(0, 5)] },
        { recording_id: 'camera-b', segments: [segment(0, 7)] },
      ],
      10,
    )).toBe(7);
  });

  it('treats time before a delayed camera begins as having no work for that camera', () => {
    expect(processedThrough(
      [recording('camera-a', 0, 10), recording('camera-b', 4, 6)],
      [{ recording_id: 'camera-a', segments: [segment(0, 8)] }],
      10,
    )).toBe(4);
  });
});
