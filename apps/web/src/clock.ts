import type { ClockSample, Recording } from './types';

export function demoSpeed(speed: number | undefined): 1 | 2 | 4 {
  return speed === 2 || speed === 4 ? speed : 1;
}

export function incidentPosition(sample: ClockSample, now: number): number {
  const { playback } = sample;
  const elapsed = playback.state === 'playing' ? Math.max(0, now - sample.receivedAt) / 1000 * demoSpeed(playback.speed) : 0;
  return Math.max(0, Math.min(playback.duration_seconds, playback.position_seconds + elapsed));
}

export function acceptClockSample(current: ClockSample, incoming: ClockSample): ClockSample {
  if (incoming.playback.revision < current.playback.revision) return current;
  if (incoming.playback.revision === current.playback.revision &&
      Date.parse(incoming.playback.server_time) < Date.parse(current.playback.server_time)) return current;
  return incoming;
}

export function recordingPosition(recording: Pick<Recording, 'start_offset_seconds' | 'duration_seconds'>, incidentSeconds: number) {
  const local = incidentSeconds - recording.start_offset_seconds;
  if (local < 0) return { state: 'waiting' as const, seconds: 0 };
  if (local >= recording.duration_seconds) return { state: 'ended' as const, seconds: recording.duration_seconds };
  return { state: 'active' as const, seconds: local };
}

export function formatTime(seconds: number): string {
  const tenths = Math.max(0, Math.floor(seconds * 10 + 0.00001));
  const whole = Math.floor(tenths / 10);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const remainder = whole % 60;
  return `${hours > 0 ? `${hours}:` : ''}${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}.${tenths % 10}`;
}
