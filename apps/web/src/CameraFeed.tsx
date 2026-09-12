import { useCallback, useEffect, useRef, useState } from 'react';
import { formatTime, recordingPosition } from './clock';
import type { RecordingTranscript, Recording, TranscriptSegment, TranscriptTurn } from './types';

interface Props {
  recording: Recording;
  incidentTime: number;
  playing: boolean;
  selected: boolean;
  audioEnabled: boolean;
  runId: string;
  transcript?: RecordingTranscript;
  transcriptConfigured: boolean;
  transcriptCutoffSeconds: number;
  onSelect: () => void;
  onProblem: (message: string) => void;
  onReady: (id: string, ready: boolean) => void;
}

export function CameraFeed({ recording, incidentTime, playing, selected, audioEnabled, runId, transcript, transcriptConfigured, transcriptCutoffSeconds, onSelect, onProblem, onReady }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const playPending = useRef(false);
  const syncRef = useRef<() => void>(() => undefined);
  const [mediaError, setMediaError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const position = recordingPosition(recording, incidentTime);

  const reportProblem = useCallback((message: string) => {
    setMediaError(message);
    setReady(false);
    onReady(recording.id, false);
    onProblem(message);
  }, [onProblem, onReady, recording.id]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.muted = !(audioEnabled && selected);
  }, [audioEnabled, selected]);

  const synchronize = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    const local = recordingPosition(recording, incidentTime);
    if (video.readyState < HTMLMediaElement.HAVE_METADATA) return;

    if (local.state !== 'active' || !playing || mediaError) {
      video.pause();
      // Keep unreleased feeds at zero. The overlay hides their first frame.
      const target = local.state === 'ended' ? Math.max(0, recording.duration_seconds - 0.03) : local.seconds;
      if (!video.seeking && Math.abs(video.currentTime - target) > 0.05) video.currentTime = target;
      return;
    }

    if (video.seeking) return;
    if (Math.abs(video.currentTime - local.seconds) > 0.25) {
      video.currentTime = local.seconds;
      return;
    }
    if (video.paused && !playPending.current) {
      playPending.current = true;
      void video.play().catch((failure: unknown) => {
        // A shared pause or a new seek can legitimately interrupt play().
        if (failure instanceof DOMException && failure.name === 'AbortError') return;
        const reason = failure instanceof Error ? failure.message : 'The browser could not start this recording.';
        reportProblem(`${recording.camera_label}: playback could not start. ${reason}`);
      }).finally(() => { playPending.current = false; });
    }
  }, [incidentTime, playing, recording, mediaError, reportProblem]);

  useEffect(() => {
    syncRef.current = synchronize;
    synchronize();
  }, [synchronize, runId]);

  useEffect(() => {
    const video = videoRef.current;
    return () => { video?.pause(); };
  }, []);

  const handleReady = () => {
    setReady(true);
    onReady(recording.id, true);
    syncRef.current();
  };

  const handleWaiting = () => {
    const video = videoRef.current;
    if (playing && position.state === 'active' && video && !video.seeking && video.readyState < HTMLMediaElement.HAVE_FUTURE_DATA) {
      onProblem(`${recording.camera_label} is buffering. Replay paused to keep all cameras synchronized. Wait for media to load, then press Play.`);
    }
  };

  const retryMedia = () => {
    setMediaError(null);
    setReady(false);
    onReady(recording.id, false);
    videoRef.current?.load();
  };

  const status = mediaError ? 'Media error' : position.state === 'waiting' ? 'Not started' :
    position.state === 'ended' ? 'Ended' : !ready ? 'Loading media' : playing ? 'Playing' : 'Ready';

  return (
    <article className={`camera-feed${selected ? ' selected' : ''}`} data-testid={`camera-feed-${recording.id}`}>
      <div className="feed-stage">
        <div className="feed-heading">
          <button type="button" className="select-feed" onClick={onSelect} aria-label={`Select ${recording.camera_label}`} aria-pressed={selected}>
            {recording.camera_label}{selected ? ' · Selected' : ''}
          </button>
          <span className="status" data-testid={`feed-status-${recording.id}`}>{status}</span>
        </div>
        <div className="video-frame">
          <video
            ref={videoRef}
            src={recording.media_url}
            preload="auto"
            playsInline
            muted={!audioEnabled || !selected}
            controls={false}
            disablePictureInPicture
            aria-label={`${recording.camera_label} synchronized recording`}
            className={position.state === 'waiting' ? 'unreleased' : ''}
            onLoadedMetadata={() => syncRef.current()}
            onCanPlay={handleReady}
            onSeeked={() => syncRef.current()}
            onWaiting={handleWaiting}
            onStalled={handleWaiting}
            onError={() => {
              const code = videoRef.current?.error?.code;
              reportProblem(`${recording.camera_label}: the browser could not read this recording${code ? ` (media error ${code})` : ''}. Check the API connection and try reloading the media.`);
            }}
          />
          {position.state === 'waiting' && <div className="video-overlay">Starts at incident {formatTime(recording.start_offset_seconds)}</div>}
          {position.state === 'ended' && <span className="ended-label">Recording ended</span>}
        </div>
        <div className="feed-metadata">
          <span>Recording {formatTime(position.seconds)} / {formatTime(recording.duration_seconds)}</span>
          <span data-testid={`feed-analyzed-${recording.id}`}>Latest analyzed: {transcript?.latest_analyzed_time_seconds != null ? formatTime(transcript.latest_analyzed_time_seconds) : '—'}</span>
          <span>Location: unknown</span>
        </div>
        {mediaError && <div className="feed-error"><p role="alert">{mediaError}</p><button type="button" onClick={retryMedia} disabled={playing}>Reload media</button></div>}
      </div>
      <TranscriptPanel recordingLabel={recording.camera_label} transcript={transcript} transcriptConfigured={transcriptConfigured} recordingId={recording.id} cutoffSeconds={transcriptCutoffSeconds} />
    </article>
  );
}

interface TranscriptPanelProps {
  recordingId: string;
  recordingLabel: string;
  transcript: RecordingTranscript | undefined;
  transcriptConfigured: boolean;
  cutoffSeconds: number;
}

function SpeakerTurnLines({ turns, className }: { turns: TranscriptTurn[]; className?: string }) {
  return (
    <div className={className ?? 'speaker-turns'}>
      {turns.map((turn, index) => (
        <p key={`${turn.label}-${index}-${turn.local_start_seconds}`} className="speaker-turn-line">
          <span className="speaker-label">{turn.label}</span> {turn.text}
        </p>
      ))}
    </div>
  );
}

function TranscriptPanel({ recordingId, recordingLabel, transcript, transcriptConfigured, cutoffSeconds }: TranscriptPanelProps) {
  const logRef = useRef<HTMLDivElement>(null);
  const followLatest = useRef(true);
  const allSegments = transcript?.segments ?? [];
  // Only surface segments whose playback window has already been reached, so
  // scrubbing the shared clock backward hides transcripts that belong to a
  // later part of the timeline.
  const segments = allSegments.filter((segment) => segment.incident_start_seconds <= cutoffSeconds + 1e-6);
  const inFlight = segments.filter((segment) => segment.status === 'queued' || segment.status === 'processing').length;
  const completed = segments.filter((segment) => segment.status === 'completed');
  const failed = segments.filter((segment) => segment.status === 'failed');
  const empty = segments.filter((segment) => segment.status === 'empty').length;
  const latest = segments.at(-1);
  const latestKey = `${segments.length}:${latest?.id ?? ''}:${latest?.status ?? ''}:${latest?.text ?? ''}`;

  useEffect(() => {
    const log = logRef.current;
    if (!log || !followLatest.current) return;
    log.scrollTop = log.scrollHeight;
  }, [latestKey]);

  const placeholder = segments.length === 0
    ? (!transcriptConfigured
      ? 'Set XAI_API_KEY on the API to enable live Grok transcription for this feed.'
      : inFlight > 0
        ? 'Transcribing the latest released speech…'
        : 'Speech will appear here as the shared clock releases each segment.')
    : null;

  return (
    <section className="feed-transcript" aria-label={`Live transcript for ${recordingLabel}`}>
      <header className="transcript-heading">
        <span>Live transcript</span>
        <div className="transcript-heading-actions">
          <TranscriptStatusBadge inFlight={inFlight} completedCount={completed.length} emptyCount={empty} failedCount={failed.length} configured={transcriptConfigured} />
        </div>
      </header>
      <div
        ref={logRef}
        id={`transcript-log-${recordingId}`}
        className="transcript-log"
        data-testid={`feed-transcript-${recordingId}`}
        onScroll={(event) => {
          const log = event.currentTarget;
          followLatest.current = log.scrollHeight - log.scrollTop - log.clientHeight < 64;
        }}
      >
        {placeholder && <p className="transcript-placeholder">{placeholder}</p>}
        <ol className="transcript-log-list" data-testid={`feed-transcript-log-${recordingId}`}>
          {segments.map((segment) => <TranscriptLogEntry key={segment.id} segment={segment} />)}
        </ol>
      </div>
    </section>
  );
}

function TranscriptLogEntry({ segment }: { segment: TranscriptSegment }) {
  const range = `${formatTime(segment.incident_start_seconds)} – ${formatTime(segment.incident_end_seconds)}`;
  if (segment.status === 'completed') {
    const turns = segment.turns ?? [];
    return (
      <li className="transcript-log-entry">
        <time className="cue-time">{range}</time>
        {turns.length > 0 ? (
          <SpeakerTurnLines turns={turns} />
        ) : (
          <p className="transcript-plain">{segment.text}</p>
        )}
      </li>
    );
  }
  if (segment.status === 'empty') {
    return (
      <li className="transcript-log-entry muted">
        <time className="cue-time">{range}</time>
        <p className="muted">No speech detected.</p>
      </li>
    );
  }
  if (segment.status === 'failed') {
    return (
      <li className="transcript-log-entry failed">
        <time className="cue-time">{range}</time>
        <p role="alert">Transcription failed. {segment.error ?? ''}</p>
      </li>
    );
  }
  return (
    <li className="transcript-log-entry pending">
      <time className="cue-time">{range}</time>
      <p className="muted">{segment.status === 'processing' ? 'Transcribing…' : 'Waiting for transcription…'}</p>
    </li>
  );
}

function TranscriptStatusBadge({ inFlight, completedCount, emptyCount, failedCount, configured }: { inFlight: number; completedCount: number; emptyCount: number; failedCount: number; configured: boolean }) {
  if (!configured) return <span className="transcript-status">Not configured</span>;
  if (inFlight > 0) return <span className="transcript-status pending">Transcribing {inFlight} segment{inFlight === 1 ? '' : 's'}…</span>;
  if (failedCount > 0) return <span className="transcript-status failed">{failedCount} failed</span>;
  if (completedCount === 0 && emptyCount === 0) return <span className="transcript-status">Idle</span>;
  return <span className="transcript-status">Up to date</span>;
}
