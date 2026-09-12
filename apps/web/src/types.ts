export interface IncidentSummary {
  id: string;
  title: string;
  context: string | null;
  created_at: string;
}

export interface Recording {
  id: string;
  original_filename: string;
  camera_label: string;
  duration_seconds: number;
  start_offset_seconds: number;
  size_bytes: number;
  media_url: string;
  validation_status: 'ready';
  processing_status: 'transcribing';
  latest_analyzed_time_seconds: number | null;
}

export type TranscriptStatus = 'queued' | 'processing' | 'completed' | 'failed' | 'empty';

export interface TranscriptTurn {
  speaker: number;
  label: string;
  text: string;
  local_start_seconds: number;
  local_end_seconds: number;
}

export interface TranscriptSegment {
  id: string;
  recording_id: string;
  run_id: string;
  local_start_seconds: number;
  local_end_seconds: number;
  incident_start_seconds: number;
  incident_end_seconds: number;
  status: TranscriptStatus;
  text: string | null;
  error: string | null;
  turns: TranscriptTurn[];
}

export interface RecordingTranscript {
  recording_id: string;
  latest_analyzed_time_seconds: number | null;
  segments: TranscriptSegment[];
}

export interface Transcripts {
  run_id: string;
  incident_position_seconds: number;
  transcription_configured: boolean;
  recordings: RecordingTranscript[];
}

export interface HistoryEvent {
  id: string;
  segment_id: string;
  recording_id: string;
  timestamp_seconds: number;
  local_seconds: number;
  title: string;
  status: 'current' | 'outdated' | 'disproven';
  status_timestamp_seconds?: number;
  status_local_seconds?: number;
  status_recording_id?: string;
}

export interface EventHistoryData {
  run_id: string;
  configured: boolean;
  events: HistoryEvent[];
}

export interface Playback {
  run_id: string;
  state: 'paused' | 'playing' | 'ended';
  position_seconds: number;
  duration_seconds: number;
  server_time: string;
  revision: number;
}

export interface Incident extends IncidentSummary {
  recordings: Recording[];
  playback: Playback;
}

export interface ClockSample {
  playback: Playback;
  receivedAt: number;
}

export type PlaybackAction = 'play' | 'pause' | 'restart' | 'seek';

export type PlaybackCommand =
  | { action: 'play' | 'pause' | 'restart' }
  | { action: 'seek'; positionSeconds: number };
