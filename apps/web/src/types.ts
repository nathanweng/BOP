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
  processing_status: 'not_implemented';
  latest_analyzed_time_seconds: null;
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

export type PlaybackAction = 'play' | 'pause' | 'restart';
