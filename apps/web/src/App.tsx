import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, messageFor } from './api';
import { CameraFeed } from './CameraFeed';
import { EventHistory } from './EventHistory';
import { formatTime } from './clock';
import { useSelection } from './selection';
import { processedThrough } from './timeline';
import type { Incident, Recording, RecordingTranscript } from './types';
import { useReplay } from './useReplay';

function incidentFromUrl() {
  return new URLSearchParams(window.location.search).get('incident');
}

export default function App() {
  const queryClient = useQueryClient();
  const [incidentId, setIncidentId] = useState(incidentFromUrl);
  const [navOpen, setNavOpen] = useState(true);
  const incidents = useQuery({ queryKey: ['incidents'], queryFn: ({ signal }) => api.listIncidents(signal) });
  const incident = useQuery({
    queryKey: ['incident', incidentId],
    queryFn: ({ signal }) => api.getIncident(incidentId!, signal),
    enabled: Boolean(incidentId),
  });

  useEffect(() => {
    const restore = () => setIncidentId(incidentFromUrl());
    window.addEventListener('popstate', restore);
    return () => window.removeEventListener('popstate', restore);
  }, []);

  const openIncident = (id: string) => {
    const url = new URL(window.location.href);
    url.searchParams.set('incident', id);
    window.history.pushState({}, '', url);
    setIncidentId(id);
  };

  const created = async (next: Incident) => {
    queryClient.setQueryData(['incident', next.id], next);
    openIncident(next.id);
    await queryClient.invalidateQueries({ queryKey: ['incidents'] });
  };

  return (
    <div className="app-shell">
      <header className="app-header"><div><p className="eyebrow">Bodycam</p><h1>Incident workspace</h1></div><span className="status">Prerecorded media · Simulated replay</span></header>
      <div className={`app-body${navOpen ? '' : ' nav-collapsed'}`}>
        <aside className="sidebar" id="incident-nav" aria-label="Incident navigation">
          <button
            type="button"
            className="sidebar-toggle"
            aria-expanded={navOpen}
            aria-controls="incident-nav"
            onClick={() => setNavOpen((open) => !open)}
          >
            {navOpen ? 'Hide incident list' : 'Show incident list'}
          </button>
          {navOpen && <>
            <section aria-labelledby="create-heading">
              <h2 id="create-heading">Create an incident</h2>
              <CreateIncident onCreated={created} />
            </section>
            <nav aria-label="Saved incidents">
              <h2>Saved incidents</h2>
              {incidents.isPending && <p role="status">Loading incidents…</p>}
              {incidents.isError && <div><p role="alert">{messageFor(incidents.error)}</p><button onClick={() => void incidents.refetch()}>Retry incidents</button></div>}
              {incidents.data?.length === 0 && <p className="muted">No incidents yet.</p>}
              <ul className="incident-list">{incidents.data?.map((entry) => <li key={entry.id}>
                <button type="button" onClick={() => openIncident(entry.id)} aria-current={entry.id === incidentId ? 'page' : undefined}>
                  {entry.title}<small>{new Date(entry.created_at).toLocaleString()}</small>
                </button>
              </li>)}</ul>
            </nav>
          </>}
        </aside>
        <main id="main-content">
          {!incidentId && <section className="panel empty-welcome"><h2>Start with the source recordings</h2><p>Create an incident, upload one or more MP4 recordings, then align their start times before beginning synchronized replay.</p><p>Situation reports and reconstruction remain empty until real processing is available.</p></section>}
          {incidentId && incident.isPending && <p role="status">Loading incident…</p>}
          {incidentId && incident.isError && <div className="panel"><p role="alert">{messageFor(incident.error)}</p><button onClick={() => void incident.refetch()}>Retry incident</button></div>}
          {incident.data && <Workspace key={incident.data.id} incident={incident.data} />}
        </main>
      </div>
    </div>
  );
}

function ConfirmDialog({ title, body, confirmLabel, onConfirm, onCancel }: {
  title: string;
  body: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onCancel(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onCancel]);
  return (
    <div className="modal-backdrop" role="presentation" onClick={onCancel}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="confirm-title" onClick={(event) => event.stopPropagation()}>
        <h2 id="confirm-title">{title}</h2>
        <p>{body}</p>
        <div className="modal-actions">
          <button type="button" onClick={onCancel}>Cancel</button>
          <button type="button" className="danger" onClick={onConfirm} data-testid="confirm-clear">{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}

function CreateIncident({ onCreated }: { onCreated: (incident: Incident) => Promise<void> }) {
  const [title, setTitle] = useState('');
  const [context, setContext] = useState('');
  const create = useMutation({
    mutationFn: () => api.createIncident(title.trim(), context.trim()),
    onSuccess: async (incident) => { await onCreated(incident); setTitle(''); setContext(''); },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (title.trim()) create.mutate();
  };

  return <form onSubmit={submit} className="stacked-form" aria-label="Create incident">
    <label>Incident title<input required maxLength={200} value={title} onChange={(event) => setTitle(event.target.value)} disabled={create.isPending} /></label>
    <label>Incident context<textarea rows={3} maxLength={10000} value={context} onChange={(event) => setContext(event.target.value)} disabled={create.isPending} /><small>Optional information supplied by you.</small></label>
    <button type="submit" disabled={!title.trim() || create.isPending}>{create.isPending ? 'Creating incident…' : 'Create incident'}</button>
    {create.isError && <p role="alert" className="error">{messageFor(create.error)}</p>}
  </form>;
}

function Workspace({ incident }: { incident: Incident }) {
  const queryClient = useQueryClient();
  const replay = useReplay(incident.id, incident.playback);
  const selectedId = useSelection((state) => state.selectedByIncident[incident.id]) ?? incident.recordings[0]?.id;
  const select = useSelection((state) => state.select);
  const [audioEnabled, setAudioEnabled] = useState(false);
  const [readyById, setReadyById] = useState<Record<string, boolean>>({});
  const [setupBusy, setSetupBusy] = useState(false);
  const [scrubbing, setScrubbing] = useState(false);
  const [scrubValue, setScrubValue] = useState(0);
  const [confirmClear, setConfirmClear] = useState(false);
  const hasCameras = incident.recordings.length >= 1;
  const mediaReady = hasCameras && incident.recordings.every((recording) => readyById[recording.id]);
  const selected = incident.recordings.find((recording) => recording.id === selectedId);
  const transcripts = useQuery({
    queryKey: ['transcripts', incident.id],
    queryFn: ({ signal }) => api.getTranscripts(incident.id, signal),
    refetchInterval: 1000,
    retry: false,
    networkMode: 'always',
    enabled: incident.recordings.length > 0,
  });
  const transcriptsByRecording: Record<string, RecordingTranscript> = {};
  if (transcripts.data) {
    for (const entry of transcripts.data.recordings) transcriptsByRecording[entry.recording_id] = entry;
  }
  const transcriptionConfigured = transcripts.data?.transcription_configured ?? false;
  const hasTranscriptSegments = Boolean(transcripts.data?.recordings.some((entry) => entry.segments.length > 0));
  // Uploads and alignment must stay locked after any playback has happened so
  // that transcripts and event history remain consistent with the recordings
  // they were derived from. Seeking back to zero does not unlock.
  const setupLocked = replay.playback.state !== 'paused' || replay.position > 0 || hasTranscriptSegments;

  const saved = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['incident', incident.id] }),
      queryClient.invalidateQueries({ queryKey: ['playback', incident.id] }),
    ]);
  }, [queryClient, incident.id]);

  const onReady = useCallback((id: string, ready: boolean) => {
    setReadyById((previous) => previous[id] === ready ? previous : { ...previous, [id]: ready });
  }, []);

  const duration = replay.playback.duration_seconds;
  const ending = replay.position >= duration && duration > 0;
  const stateLabel = replay.holdReason ? 'Paused locally' : scrubbing ? 'Scrubbing' : ending ? 'Ended' : replay.playback.state === 'playing' ? 'Playing' : 'Paused';
  const displayPosition = scrubbing ? scrubValue : replay.position;
  const currentRunTranscripts = transcripts.data?.run_id === replay.playback.run_id
    ? transcripts.data.recordings
    : [];
  const processedCutoff = processedThrough(incident.recordings, currentRunTranscripts, duration);
  const processedPct = duration > 0 ? Math.min(100, (processedCutoff / duration) * 100) : 0;
  const playedPct = duration > 0 ? Math.min(100, (displayPosition / duration) * 100) : 0;
  const scrubberDisabled = !hasCameras || !replay.connected || replay.pending || setupBusy || duration <= 0;
  const resetDisabled = !replay.connected || replay.pending || setupBusy || !hasCameras || (replay.playback.state === 'paused' && replay.position === 0);
  const clearDisabled = !replay.connected || replay.pending || setupBusy || incident.recordings.length === 0;

  const clampToDuration = (value: number) => Math.min(duration, Math.max(0, value));

  const beginScrub = () => {
    if (scrubberDisabled) return;
    setScrubValue(clampToDuration(replay.position));
    setScrubbing(true);
  };

  const commitScrub = (value: number) => {
    setScrubbing(false);
    void replay.control({ action: 'seek', positionSeconds: clampToDuration(value) });
  };

  const confirmClearHistory = () => {
    setConfirmClear(false);
    void replay.control('restart');
  };

  return <div className="workspace">
    <div className="incident-heading"><h2>{incident.title}</h2>{incident.context && <p className="context">{incident.context}</p>}</div>
    <details className="panel setup-panel" open>
      <summary>Recording setup · {incident.recordings.length} {incident.recordings.length === 1 ? 'camera' : 'cameras'}</summary>
      <p>Offsets are seconds after the incident begins. For example, a camera offset of 2 starts when the shared clock reaches 00:02.0.</p>
      {setupLocked && <p className="notice">Use “Clear all history” to unlock uploads and alignment. Clearing history creates a new run and removes the current run's transcripts and event history.</p>}
      <UploadRecording incident={incident} disabled={setupLocked || setupBusy} onBusy={setSetupBusy} onSaved={saved} />
      <div className="recording-settings">{incident.recordings.map((recording) => <AlignmentForm key={recording.id} incidentId={incident.id} recording={recording} disabled={setupLocked || setupBusy} onBusy={setSetupBusy} onSaved={saved} />)}</div>
    </details>

    <section className="playback-bar" aria-label="Shared replay controls">
      <div className="clock-column">
        <span className="muted">Incident clock · {stateLabel}</span>
        <output data-testid="incident-clock" data-seconds={displayPosition.toFixed(3)} className="clock" aria-label="Incident playback time">{formatTime(displayPosition)} <small>/ {formatTime(duration)}</small></output>
        <div
          className={`scrubber-track${scrubberDisabled ? ' is-disabled' : ''}`}
          style={{ ['--played' as string]: `${playedPct}%`, ['--processed' as string]: `${processedPct}%` }}
          title={`Processed through ${formatTime(processedCutoff)}`}
        >
          <span className="scrubber-played" aria-hidden="true" />
          <input
            type="range"
            className="scrubber"
            aria-label="Scrub incident timeline"
            aria-valuemin={0}
            aria-valuemax={duration}
            aria-valuenow={displayPosition}
            data-testid="incident-scrubber"
            data-processed-seconds={processedCutoff.toFixed(3)}
            min={0}
            max={duration > 0 ? duration : 0}
            step={0.1}
            value={displayPosition}
            disabled={scrubberDisabled}
            onPointerDown={beginScrub}
            onKeyDown={(event) => { if (['ArrowLeft', 'ArrowRight', 'PageUp', 'PageDown', 'Home', 'End'].includes(event.key)) beginScrub(); }}
            onChange={(event) => setScrubValue(clampToDuration(Number(event.target.value)))}
            onPointerUp={(event) => { if (scrubbing) commitScrub(Number((event.target as HTMLInputElement).value)); }}
            onBlur={(event) => { if (scrubbing) commitScrub(Number(event.target.value)); }}
            onKeyUp={(event) => { if (scrubbing && ['ArrowLeft', 'ArrowRight', 'PageUp', 'PageDown', 'Home', 'End'].includes(event.key)) commitScrub(Number((event.target as HTMLInputElement).value)); }}
          />
        </div>
        <span className="scrubber-status" data-testid="processed-through">Processed through {formatTime(processedCutoff)}</span>
      </div>
      <div className="button-row">
        <button type="button" onClick={() => void replay.control('play')} disabled={!mediaReady || !replay.connected || replay.pending || setupBusy || replay.playing || ending || scrubbing}>Play</button>
        <button type="button" onClick={() => void replay.control('pause')} disabled={replay.playback.state !== 'playing' || !replay.connected || replay.pending || setupBusy || scrubbing}>Pause</button>
        <button type="button" onClick={() => void replay.control({ action: 'seek', positionSeconds: 0 })} disabled={resetDisabled} data-testid="reset-to-start">Reset to 0</button>
        <button type="button" className="danger" onClick={() => setConfirmClear(true)} disabled={clearDisabled} data-testid="clear-history">Clear all history</button>
        <label className="audio-choice"><input type="checkbox" checked={audioEnabled} onChange={(event) => setAudioEnabled(event.target.checked)} disabled={!selected} />Enable selected camera audio</label>
      </div>
      {!hasCameras && <p className="control-help">Upload a recording to start replay.</p>}
      {hasCameras && !mediaReady && <p className="control-help" role="status">Waiting for all recording media to be ready.</p>}
      {replay.pending && <p className="control-help" role="status">Updating shared replay…</p>}
      {replay.holdReason && <p className="notice" role="status">{replay.holdReason}</p>}
      {replay.error && <p className="error" role="alert">{replay.error}</p>}
      {!replay.connected && <button type="button" onClick={replay.reconnect}>Reconnect replay</button>}
    </section>

    {confirmClear && <ConfirmDialog
      title="Clear all history?"
      body="This deletes the current run's transcripts and event history and resets the incident clock to zero. Uploaded recordings stay in place. This cannot be undone."
      confirmLabel="Clear all history"
      onConfirm={confirmClearHistory}
      onCancel={() => setConfirmClear(false)}
    />}

    <section className="panel main-stage" aria-labelledby="stage-heading">
      <div className="section-heading"><h2 id="stage-heading">Incident map and body-camera view</h2>
        <a href={`?incident=${encodeURIComponent(incident.id)}&view=mindmap`} target={`mindmap-${incident.id}`}
          onClick={(event) => {
            const popup = window.open(event.currentTarget.href, `mindmap-${incident.id}`, 'popup,width=1500,height=950,resizable=yes,scrollbars=yes');
            if (popup) { event.preventDefault(); popup.focus(); }
          }}>Open mind map ↗</a><span className="status">Simulated replay</span></div>
      <div className="stage-grid">
        <div className="map-context" role="region" aria-label="Incident map">
          <h3>Incident map</h3><p>No supported location data for this incident.</p><p className="muted">Camera locations are unknown.</p>
          {selected && <p>Selected source: {selected.camera_label}</p>}
        </div>
        <div className="camera-stack">
          {incident.recordings.length === 0 && <div className="empty-state"><h3>No recordings uploaded</h3><p>Upload an MP4 recording to display a camera feed.</p></div>}
          {incident.recordings.map((recording) => <CameraFeed
            key={recording.id}
            recording={recording}
            incidentTime={displayPosition}
            playing={replay.playing && !scrubbing}
            selected={selectedId === recording.id}
            audioEnabled={audioEnabled}
            runId={replay.playback.run_id}
            transcript={transcriptsByRecording[recording.id]}
            transcriptConfigured={transcriptionConfigured}
            transcriptCutoffSeconds={replay.position}
            onSelect={() => select(incident.id, recording.id)}
            onProblem={replay.halt}
            onReady={onReady}
          />)}
        </div>
      </div>
      {!transcriptionConfigured && incident.recordings.length > 0 && <p className="muted">Live Grok transcription is not configured. Set XAI_API_KEY on the API to enable per-camera transcripts. Segment release still works so the pipeline stays visible.</p>}
    </section>

    <section className="panel" aria-labelledby="reconstruction-heading" data-testid="reconstruction-empty-state">
      <h2 id="reconstruction-heading">Statement-based reconstruction</h2>
      <p className="empty-state">No reconstruction is available. Source-linked observations are required before a scene can be shown.</p>
    </section>

    <section className="panel" aria-labelledby="sitrep-heading" data-testid="analysis-empty-state">
      <h2 id="sitrep-heading">Current situation report</h2>
      <p className="empty-state">No analyzed observations yet. The overview, latest changes, current status, and unresolved information will appear when source-linked results are available.</p>
    </section>
    <EventHistory key={replay.playback.run_id} incidentId={incident.id} runId={replay.playback.run_id} recordings={incident.recordings} />
    <section className="panel" aria-labelledby="evidence-heading"><h2 id="evidence-heading">Evidence</h2><p className="empty-state">No source-linked claims are available to review.</p></section>
  </div>;
}

interface SetupProps {
  disabled: boolean;
  onBusy: (busy: boolean) => void;
  onSaved: () => Promise<void>;
}

function UploadRecording({ incident, disabled, onBusy, onSaved }: SetupProps & { incident: Incident }) {
  const [label, setLabel] = useState('');
  const [offset, setOffset] = useState('0');
  const [file, setFile] = useState<File | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const upload = useMutation({
    mutationFn: (form: FormData) => api.uploadRecording(incident.id, form),
    onMutate: () => { onBusy(true); },
    onSuccess: async () => {
      await onSaved();
      setLabel(''); setOffset('0'); setFile(null);
      if (fileInput.current) fileInput.current.value = '';
    },
    onSettled: () => { onBusy(false); },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setValidationError(null);
    if (!file || !file.name.toLowerCase().endsWith('.mp4')) { setValidationError('Choose an MP4 recording.'); return; }
    const seconds = Number(offset);
    if (offset.trim() === '' || !Number.isFinite(seconds) || seconds < 0 || seconds > 86400) {
      setValidationError('Enter a start offset between 0 and 86400 seconds.'); return;
    }
    if (!label.trim()) { setValidationError('Enter a camera label.'); return; }
    const form = new FormData();
    form.append('file', file);
    form.append('camera_label', label.trim());
    form.append('start_offset_seconds', String(seconds));
    upload.mutate(form);
  };

  return <form onSubmit={submit} aria-label="Upload recording" className="upload-form">
    <fieldset disabled={disabled}>
      <legend>Add a camera recording</legend>
      <label>Camera label<input required maxLength={100} value={label} onChange={(event) => setLabel(event.target.value)} placeholder="Camera A" /></label>
      <label>MP4 recording<input ref={fileInput} type="file" accept=".mp4,video/mp4" required onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label>
      <label>Start offset (seconds)<input type="number" required min="0" max="86400" step="0.001" value={offset} onChange={(event) => setOffset(event.target.value)} /></label>
      <button type="submit">{upload.isPending ? 'Uploading and validating…' : 'Upload recording'}</button>
    </fieldset>
    {upload.isPending && <p role="status">Uploading and validating the actual recording. Keep this page open until it finishes.</p>}
    {(validationError || upload.isError) && <p role="alert" className="error">{validationError ?? messageFor(upload.error)}</p>}
  </form>;
}

function AlignmentForm({ incidentId, recording, disabled, onBusy, onSaved }: SetupProps & { incidentId: string; recording: Recording }) {
  const [label, setLabel] = useState(recording.camera_label);
  const [offset, setOffset] = useState(String(recording.start_offset_seconds));
  const [validationError, setValidationError] = useState<string | null>(null);
  const [savedNotice, setSavedNotice] = useState(false);
  const align = useMutation({
    mutationFn: () => api.alignRecording(incidentId, recording.id, label.trim(), Number(offset)),
    onMutate: () => { onBusy(true); setSavedNotice(false); },
    onSuccess: async () => { await onSaved(); setSavedNotice(true); },
    onSettled: () => { onBusy(false); },
  });

  useEffect(() => {
    setLabel(recording.camera_label);
    setOffset(String(recording.start_offset_seconds));
  }, [recording.camera_label, recording.start_offset_seconds]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setValidationError(null);
    const seconds = Number(offset);
    if (!label.trim()) { setValidationError('Enter a camera label.'); return; }
    if (offset.trim() === '' || !Number.isFinite(seconds) || seconds < 0 || seconds > 86400) {
      setValidationError('Enter a start offset between 0 and 86400 seconds.'); return;
    }
    align.mutate();
  };

  return <form onSubmit={submit} className="alignment-form" aria-label={`Alignment for ${recording.camera_label}`}>
    <fieldset disabled={disabled}>
      <legend>{recording.original_filename}</legend>
      <p className="file-details">Validated · {formatTime(recording.duration_seconds)} · {(recording.size_bytes / 1024 / 1024).toFixed(2)} MB</p>
      <label>Camera label for {recording.camera_label}<input required maxLength={100} value={label} onChange={(event) => { setLabel(event.target.value); setSavedNotice(false); }} /></label>
      <label>Start offset for {recording.camera_label} (seconds)<input type="number" required min="0" max="86400" step="0.001" value={offset} onChange={(event) => { setOffset(event.target.value); setSavedNotice(false); }} /></label>
      <button type="submit">{align.isPending ? 'Saving alignment…' : 'Save alignment'}</button>
    </fieldset>
    {savedNotice && <p className="muted" role="status">Alignment saved.</p>}
    {(validationError || align.isError) && <p role="alert" className="error">{validationError ?? messageFor(align.error)}</p>}
  </form>;
}
