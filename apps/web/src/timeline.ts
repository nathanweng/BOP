import type { Recording, TranscriptSegment } from './types';

type TimelineRecording = Pick<Recording, 'id' | 'start_offset_seconds' | 'duration_seconds'>;
type TimelineSegment = Pick<TranscriptSegment, 'incident_start_seconds' | 'incident_end_seconds' | 'status'>;

interface TimelineTranscript {
  recording_id: string;
  segments: readonly TimelineSegment[];
}

const EPSILON_SECONDS = 1e-6;

/**
 * Return the incident high-water mark through which every active recording has
 * terminal transcription coverage. Completed and empty windows are processed;
 * queued, processing, and failed windows still need work and stop the marker.
 */
export function processedThrough(
  recordings: readonly TimelineRecording[],
  transcripts: readonly TimelineTranscript[],
  durationSeconds: number,
): number {
  if (durationSeconds <= 0 || recordings.length === 0) return 0;

  const transcriptsByRecording = new Map(
    transcripts.map((transcript) => [transcript.recording_id, transcript.segments]),
  );
  let incidentCutoff = durationSeconds;

  for (const recording of recordings) {
    const recordingStart = Math.min(durationSeconds, Math.max(0, recording.start_offset_seconds));
    const recordingEnd = Math.min(
      durationSeconds,
      Math.max(recordingStart, recording.start_offset_seconds + recording.duration_seconds),
    );
    if (recordingEnd <= recordingStart + EPSILON_SECONDS) continue;

    let recordingCutoff = recordingStart;
    const terminalSegments = [...(transcriptsByRecording.get(recording.id) ?? [])]
      .filter((segment) => segment.status === 'completed' || segment.status === 'empty')
      .sort((left, right) => left.incident_start_seconds - right.incident_start_seconds);

    for (const segment of terminalSegments) {
      if (segment.incident_end_seconds <= recordingCutoff + EPSILON_SECONDS) continue;
      if (segment.incident_start_seconds > recordingCutoff + EPSILON_SECONDS) break;
      recordingCutoff = Math.min(recordingEnd, Math.max(recordingCutoff, segment.incident_end_seconds));
      if (recordingCutoff >= recordingEnd - EPSILON_SECONDS) break;
    }

    // A fully processed recording cannot block a longer, later-ending feed.
    if (recordingCutoff < recordingEnd - EPSILON_SECONDS) {
      incidentCutoff = Math.min(incidentCutoff, recordingCutoff);
    }
  }

  return Math.min(durationSeconds, Math.max(0, incidentCutoff));
}
