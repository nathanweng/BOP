import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, messageFor } from './api';
import { CameraFeed } from './CameraFeed';
import { SituationReport } from './SituationReport';
import { EventHistory } from './EventHistory';
import { BoardStatus } from './BoardStatus';
import { demoSpeed, formatTime } from './clock';
import { useSelection } from './selection';
import { processedThrough } from './timeline';
import type { Incident, Recording, RecordingTranscript } from './types';
import { useReplay } from './useReplay';
import { Icon } from './Icon';
import { SignalField } from './SignalField';
import { transition } from './motion';

function incidentFromUrl() {
  return new URLSearchParams(window.location.search).get('incident');
}

export default function App() {
  const queryClient = useQueryClient();
  const [incidentId, setIncidentId] = useState(incidentFromUrl);
  const [navOpen, setNavOpen] = useState(false);
  const drawer = useRef<HTMLDialogElement>(null);
  const [search, setSearch] = useState('');
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
    transition(() => { setIncidentId(id); setNavOpen(false); });
    window.scrollTo({ top: 0, behavior: 'instant' });
  };

  const created = async (next: Incident) => {
    queryClient.setQueryData(['incident', next.id], next);
    openIncident(next.id);
    await queryClient.invalidateQueries({ queryKey: ['incidents'] });
  };

  useEffect(() => {
    if (navOpen) drawer.current?.showModal();
    else drawer.current?.close();
  }, [navOpen]);

  const [pendingDelete, setPendingDelete] = useState<{ id: string; title: string } | null>(null);
  const showLibrary = () => {
    const url = new URL(window.location.href);
    url.searchParams.delete('incident');
    window.history.pushState({}, '', url);
    transition(() => setIncidentId(null));
  };
  const removeIncident = useMutation({
    mutationFn: (id: string) => api.deleteIncident(id),
    onSuccess: async (_, id) => {
      setPendingDelete(null);
      queryClient.removeQueries({ queryKey: ['incident', id] });
      await queryClient.invalidateQueries({ queryKey: ['incidents'] });
      if (incidentId === id) showLibrary();
    },
  });
  const askRemove = (entry: { id: string; title: string }) => {
    removeIncident.reset();
    setNavOpen(false);
    setPendingDelete(entry);
  };

  const matching = incidents.data?.filter((entry) => entry.title.toLowerCase().includes(search.toLowerCase())) ?? [];
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to workspace</a>
      <header className="app-header">
        <a className="brand" href="/" aria-label="BOP home"><span className="brand-mark"><Icon name="layers" /></span>BOP<span className="brand-slash">/</span><span className="brand-name">BIZZY OPS</span></a>
        <nav className="top-nav" aria-label="Workspace navigation"><a href="/" aria-current={!incidentId ? 'page' : undefined}>Incidents</a>{incidentId && <><a href="#stage-heading">Camera views</a><a href="#event-history">Events &amp; facts</a></>}</nav>
        <span className="simulation-status"><span className="status-dot" />Simulated replay</span>
        <button className="new-incident-button" onClick={() => setNavOpen(true)} aria-controls="incident-nav" aria-expanded={navOpen}><span aria-hidden="true">+</span> New incident</button>
      </header>
      {pendingDelete && <ConfirmDialog
        title="Remove this incident?"
        body={`This permanently deletes “${pendingDelete.title}”, including recordings, transcripts, and analysis. This cannot be undone.`}
        confirmLabel={removeIncident.isPending ? 'Removing…' : 'Remove incident'}
        confirmDisabled={removeIncident.isPending}
        error={removeIncident.isError ? messageFor(removeIncident.error) : undefined}
        onConfirm={() => removeIncident.mutate(pendingDelete.id)}
        onCancel={() => { if (!removeIncident.isPending) setPendingDelete(null); }}
      />}
      <dialog className="incident-drawer" ref={drawer} id="incident-nav" aria-labelledby="drawer-title" onCancel={() => setNavOpen(false)} onClick={(event) => { if (event.target === event.currentTarget) setNavOpen(false); }}>
        <div className="drawer-inner"><div className="drawer-heading"><span className="eyebrow">INCIDENT LIBRARY</span><button onClick={() => setNavOpen(false)} aria-label="Close incident library">✕</button></div>
          <h2 id="drawer-title">New incident<span>.</span></h2><CreateIncident onCreated={created} />
          <nav className="drawer-saved" aria-label="Saved incidents"><h3>Or return to an incident</h3>
            <label className="incident-search"><Icon name="search" /><span className="sr-only">Search incidents</span><input type="search" placeholder="Search incidents" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
            <ul className="incident-list">{matching.map((entry) => <li key={entry.id}>
              <button type="button" onClick={() => openIncident(entry.id)} aria-current={entry.id === incidentId ? 'page' : undefined}>{entry.title}<small>{new Date(entry.created_at).toLocaleDateString()}</small><Icon name="arrow" /></button>
              <button type="button" className="row-remove" aria-label={`Remove ${entry.title}`} onClick={() => askRemove(entry)}>✕</button>
            </li>)}</ul>
            {incidents.isPending && <p role="status">Loading incidents…</p>}
            {incidents.isError && <p role="alert">{messageFor(incidents.error)} <button onClick={() => void incidents.refetch()}>Retry incidents</button></p>}
            {!incidents.isPending && !incidents.isError && !matching.length && <p className="muted">{search ? 'No matching incidents.' : 'No incidents yet.'}</p>}
          </nav>
        </div>
      </dialog>
      <main id="main-content">
        {!incidentId && <div className="incident-library">
          <header className="library-heading"><div><p className="eyebrow">RECORDINGS / TRANSCRIPTS / EVIDENCE</p><h1>Incidents<span>.</span></h1><p>Your recordings. Every perspective.</p></div><SignalField /></header>
          <div className="library-toolbar"><span>All incidents <small>{incidents.data?.length ?? '—'}</small></span><label className="incident-search"><Icon name="search" /><span className="sr-only">Find an incident</span><input type="search" placeholder="Find an incident" value={search} onChange={(event) => setSearch(event.target.value)} /></label><button className="primary" onClick={() => setNavOpen(true)}>Create incident <Icon name="arrow" /></button></div>
          {incidents.isPending && <p role="status">Loading incidents…</p>}
          {incidents.isError && <p role="alert">{messageFor(incidents.error)} <button onClick={() => void incidents.refetch()}>Retry incidents</button></p>}
          <div className="library-list"><div className="library-columns"><span>Incident</span><span>Created</span><span>Open workspace</span><span className="sr-only">Remove</span></div>{matching.map((entry, index) => <div className="library-row" key={entry.id} style={{ ['--row' as string]: Math.min(index, 8) }}>
            <button type="button" className="library-row-open" onClick={() => openIncident(entry.id)}><span className="library-row-title"><span className="library-index">{String(index + 1).padStart(2, '0')}</span><span><strong>{entry.title}</strong><small>{entry.context || 'No incident context supplied'}</small></span></span><time dateTime={entry.created_at}>{new Date(entry.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}</time><span className="library-open"><Icon name="arrow" /></span></button>
            <button type="button" className="row-remove" aria-label={`Remove ${entry.title}`} onClick={() => askRemove(entry)}>✕</button>
          </div>)}</div>
          {!incidents.isPending && !incidents.isError && !matching.length && <div className="library-empty"><h2>{search ? 'No matching incidents.' : 'Ready for your first recording.'}</h2><p>{search ? 'Try another title.' : 'Create an incident to bring your recordings into one workspace.'}</p></div>}
          <footer className="library-footer"><span>BOP / INCIDENT REVIEW</span><span>Bizzy Ops · prerecorded, source-linked context.</span></footer>
        </div>}
        {incidentId && incident.isPending && <p className="workspace-loading" role="status">Opening incident…</p>}
        {incidentId && incident.isError && <div className="panel"><p role="alert">{messageFor(incident.error)}</p><button onClick={() => void incident.refetch()}>Retry incident</button></div>}
        {incident.data && <Workspace key={incident.data.id} incident={incident.data} onRemove={() => { if (incident.data) askRemove(incident.data); }} />}
      </main>
    </div>
  );
}

function ConfirmDialog({ title, body, confirmLabel, confirmDisabled, error, onConfirm, onCancel }: {
  title: string;
  body: string;
  confirmLabel: string;
  confirmDisabled?: boolean;
  error?: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape' && !confirmDisabled) onCancel(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onCancel, confirmDisabled]);
  return (
    <div className="modal-backdrop" role="presentation" onClick={() => { if (!confirmDisabled) onCancel(); }}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="confirm-title" onClick={(event) => event.stopPropagation()}>
        <h2 id="confirm-title">{title}</h2>
        <p>{body}</p>
        {error && <p role="alert" className="error">{error}</p>}
        <div className="modal-actions">
          <button type="button" onClick={onCancel} disabled={confirmDisabled}>Cancel</button>
          <button type="button" className="danger" onClick={onConfirm} disabled={confirmDisabled} data-testid="confirm-clear">{confirmLabel}</button>
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
    <label>Incident title<input id="new-incident-title" placeholder="e.g. Incident 2026-0142" required maxLength={200} value={title} onChange={(event) => setTitle(event.target.value)} disabled={create.isPending} /></label>
    <label>Incident context<textarea placeholder="Add initial context…" rows={3} maxLength={10000} value={context} onChange={(event) => setContext(event.target.value)} disabled={create.isPending} /><small>Optional information supplied by you.</small></label>
    <button type="submit" disabled={!title.trim() || create.isPending}>{create.isPending ? 'Creating incident…' : 'Create incident'}</button>
    {create.isError && <p role="alert" className="error">{messageFor(create.error)}</p>}
  </form>;
}

function Workspace({ incident, onRemove }: { incident: Incident; onRemove: () => void }) {
  const queryClient = useQueryClient();
  const replay = useReplay(incident.id, incident.playback);
  const selectedId = useSelection((state) => state.selectedByIncident[incident.id]) ?? incident.recordings[0]?.id;
  const select = useSelection((state) => state.select);
  const [audioEnabled, setAudioEnabled] = useState(false);
  const [readyById, setReadyById] = useState<Record<string, boolean>>({});
  const [setupBusy, setSetupBusy] = useState(false);
  const [setupOpen, setSetupOpen] = useState(incident.recordings.length === 0);
  const [scrubbing, setScrubbing] = useState(false);
  const [scrubValue, setScrubValue] = useState(0);
  const [confirmClear, setConfirmClear] = useState(false);
  const [eventTarget, setEventTarget] = useState<{ id: string }>();
  const [stageExpanded, setStageExpanded] = useState(false);
  useEffect(() => {
    if (!stageExpanded) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const exit = (event: KeyboardEvent) => { if (event.key === 'Escape') transition(() => setStageExpanded(false)); };
    window.addEventListener('keydown', exit);
    return () => { document.body.style.overflow = previous; window.removeEventListener('keydown', exit); };
  }, [stageExpanded]);
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
  const speed = demoSpeed(replay.playback.speed);
  const ending = replay.position >= duration && duration > 0;
  const stateLabel = replay.holdReason ? 'Paused locally' : scrubbing ? 'Scrubbing' : ending ? 'Ended' : replay.playback.state === 'playing' ? (speed === 1 ? 'Playing' : `Playing · ${speed}×`) : 'Paused';
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
    <div className="incident-heading"><div><a className="incident-back" href="/">← All incidents</a><h1>{incident.title}</h1>{incident.context && <p className="context">{incident.context}</p>}</div><div className="incident-heading-actions"><span className={`status connection-status${replay.connected ? ' connected' : ''}`}><span className="status-dot" />{replay.connected ? 'Replay connected' : 'Replay disconnected'}</span><button type="button" className="danger" onClick={onRemove} data-testid="remove-incident">Remove incident</button></div></div>
    <div className="incident-meta" aria-label="Incident overview">
      <span><Icon name="camera" />{incident.recordings.length} {incident.recordings.length === 1 ? 'camera' : 'cameras'}</span>
      <span><Icon name="clock" />{formatTime(duration)} total</span>
      {selected && <span>Selected: <strong>{selected.camera_label}</strong></span>}
    </div>

    {confirmClear && <ConfirmDialog
      title="Clear all history?"
      body="This deletes the current run's transcripts and event history and resets the incident clock to zero. Uploaded recordings stay in place. This cannot be undone."
      confirmLabel="Clear all history"
      onConfirm={confirmClearHistory}
      onCancel={() => setConfirmClear(false)}
    />}

    <section className={`panel main-stage${hasCameras ? ' has-cameras' : ''}${stageExpanded ? ' stage-expanded' : ''}`} aria-labelledby="stage-heading">
      <BoardStatus incidentId={incident.id} runId={replay.playback.run_id} />
      <div className="section-heading"><h2 id="stage-heading"><span className="section-number">01</span> Camera views</h2>
        <a href={`?incident=${encodeURIComponent(incident.id)}&view=mindmap`} target={`mindmap-${incident.id}`}
          onClick={(event) => {
            const popup = window.open(event.currentTarget.href, `mindmap-${incident.id}`, 'popup,width=1500,height=950,resizable=yes,scrollbars=yes');
            if (popup) { event.preventDefault(); popup.focus(); }
          }}>Open mind map ↗</a><button className="expand-stage" aria-expanded={stageExpanded} onClick={() => transition(() => setStageExpanded((expanded) => !expanded))}>{stageExpanded ? 'Exit expanded view' : 'Expand view'} <Icon name="target" /></button><span className="stage-source-count">{incident.recordings.length} {incident.recordings.length === 1 ? 'source' : 'sources'}</span></div>
      <div className="stage-grid">
        <div className="camera-stack">
          {incident.recordings.length === 0 && <div className="empty-state"><h3>No recordings uploaded</h3><p>Upload an MP4 recording to display a camera feed.</p></div>}
          {incident.recordings.map((recording) => <CameraFeed
            key={recording.id}
            recording={recording}
            incidentTime={displayPosition}
            playing={replay.playing && !scrubbing}
            speed={speed}
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

    <details className="panel setup-panel" open={setupOpen} onToggle={(event) => setSetupOpen(event.currentTarget.open)}>
      <summary>Recording setup · {incident.recordings.length} {incident.recordings.length === 1 ? 'camera' : 'cameras'}</summary>
      <p>Offsets are seconds after the incident begins. For example, a camera offset of 2 starts when the shared clock reaches 00:02.0.</p>
      {setupLocked && <p className="notice">Use “Clear all history” to unlock uploads and alignment. Clearing history creates a new run and removes the current run's transcripts and event history.</p>}
      <UploadRecording incident={incident} disabled={setupLocked || setupBusy} onBusy={setSetupBusy} onSaved={saved} />
      <div className="recording-settings">{incident.recordings.map((recording) => <AlignmentForm key={recording.id} incidentId={incident.id} recording={recording} disabled={setupLocked || setupBusy} onBusy={setSetupBusy} onSaved={saved} />)}</div>
    </details>


    <div className="analysis-layout">
    <SituationReport incidentId={incident.id} runId={replay.playback.run_id} incidentTime={displayPosition}
      onSelectEvent={(id) => setEventTarget({ id })} />
    <EventHistory key={replay.playback.run_id} incidentId={incident.id} runId={replay.playback.run_id} recordings={incident.recordings}
      eventTarget={eventTarget} seekDisabled={scrubberDisabled} onSeek={(seconds, recordingId) => {
        select(incident.id, recordingId);
        commitScrub(seconds);
        document.getElementById('stage-heading')?.scrollIntoView({ block: 'start', behavior: 'instant' });
      }} />

    </div>
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
        <button type="button" className="primary play-button" onClick={() => void replay.control('play')} disabled={!mediaReady || !replay.connected || replay.pending || setupBusy || replay.playing || ending || scrubbing}>Play</button>
        <button type="button" onClick={() => void replay.control('pause')} disabled={replay.playback.state !== 'playing' || !replay.connected || replay.pending || setupBusy || scrubbing}>Pause</button>
        <div className="speed-choice" role="group" aria-label="Replay speed">
          {([1, 2, 4] as const).map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={speed === option}
              data-testid={`replay-speed-${option}`}
              onClick={() => void replay.control({ action: 'set_speed', speed: option })}
              disabled={!replay.connected || replay.pending || setupBusy || scrubbing || speed === option}
            >{option}×</button>
          ))}
        </div>
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
