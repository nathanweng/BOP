import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, messageFor } from './api';
import { formatTime } from './clock';
import type { HistoryEvent, Recording } from './types';

export function EventHistory({ incidentId, runId, recordings }: {
  incidentId: string; runId: string; recordings: Recording[];
}) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<HistoryEvent | null>(null);
  const video = useRef<HTMLVideoElement>(null);
  const queryKey = ['events', incidentId, runId];
  const history = useQuery({ queryKey, queryFn: ({ signal }) => api.getEvents(incidentId, signal),
    refetchInterval: 2000, retry: false });
  const generate = useMutation({
    mutationFn: () => api.generateEvents(incidentId),
    onSuccess: (data) => { if (data.run_id === runId) queryClient.setQueryData(queryKey, data); },
  });
  const data = history.data?.run_id === runId ? history.data : undefined;
  const queuedKey = data?.state === 'queued' ? `${runId}:${data.pending_segments}` : '';
  const kicked = useRef('');
  useEffect(() => {
    if (!queuedKey || kicked.current === queuedKey) return;
    kicked.current = queuedKey;
    generate.mutate();
  }, [queuedKey]);
  const events = data?.events ?? [];
  const current = events.filter((event) => event.status === 'current');
  const challenged = events.filter((event) => event.status !== 'current');
  const recording = recordings.find((entry) => entry.id === selected?.recording_id);
  const review = (event: HistoryEvent, correction = false) => {
    const source = correction && event.status_recording_id && event.status_local_seconds != null
      ? { ...event, recording_id: event.status_recording_id, local_seconds: event.status_local_seconds }
      : event;
    if (video.current && selected?.recording_id === source.recording_id) video.current.currentTime = source.local_seconds;
    setSelected(source);
  };
  const factRow = (event: HistoryEvent) => {
    const factTime = formatTime(event.timestamp_seconds);
    const evidenceTime = event.status_timestamp_seconds != null ? formatTime(event.status_timestamp_seconds) : null;
    return <tr key={event.id} className={`event-${event.status}`}>
      <td><button onClick={() => review(event)} aria-label={`Review source at ${factTime}`}>{factTime}</button></td>
      <td>
        <button className="event-title" onClick={() => review(event)}>{event.title}</button>
        {event.status !== 'current' && <p className="event-reason">
          {event.status === 'disproven' ? 'Invalidated' : 'Suspicious'}
          {evidenceTime && <>
            {' · '}
            <button className="event-update-link" onClick={() => review(event, true)}
              aria-label={`Review evidence at ${evidenceTime}`}>
              evidence at {evidenceTime}
            </button>
          </>}
          {event.status_reason ? ` — ${event.status_reason}` : ''}
        </p>}
      </td>
    </tr>;
  };
  return <details className="panel event-history-panel" id="event-history" open>
    <summary>Event history</summary>
    <p className="muted">Current facts update as transcripts finish. Each fact keeps its own time. Yellow is suspicious and red is invalidated, with the evidence timestamp.</p>
    {data?.configured && data.state == null && (
      <p role="status">The API is running an older process. Restart npm run dev:api so event analysis can start.</p>
    )}
    {data?.configured && data.state != null && <p role="status" className="muted">
      {generate.isPending || data.state === 'processing' ? 'Analyzing new transcripts…' : data.state === 'queued' ? 'New transcripts waiting for analysis…' :
        data.state === 'retrying' ? 'Analysis will retry automatically…' : data.state === 'failed' ? 'Analysis needs attention.' :
          data.processed_segments ? 'Up to date with completed transcripts.' : 'Waiting for completed transcripts…'}
      {' '}{data.processed_segments} processed · {data.pending_segments} pending
    </p>}
    {data?.error && <div role="alert"><p>{data.error}</p>
      <button disabled={generate.isPending} onClick={() => generate.mutate()}>Retry analysis</button>
    </div>}
    {data && !data.configured && <p className="muted">Set OPENROUTER_API_KEY in .env and restart the API to enable event history.</p>}
    {history.isPending && <p role="status">Loading history…</p>}
    {history.isError && <p role="alert">{messageFor(history.error)} <button onClick={() => void history.refetch()}>Retry</button></p>}
    {generate.isError && <p role="alert">{messageFor(generate.error)}</p>}
    {data && data.events.length === 0 && <p>No events recorded yet.</p>}
    {!!current.length && <>
      <h3 className="event-section">Current facts</h3>
      <table className="event-history"><thead><tr><th>Timestamp</th><th>Fact</th></tr></thead>
        <tbody>{current.map(factRow)}</tbody>
      </table>
    </>}
    {!!challenged.length && <>
      <h3 className="event-section">Suspicious or invalidated</h3>
      <table className="event-history"><thead><tr><th>Timestamp</th><th>Fact</th></tr></thead>
        <tbody>{challenged.map(factRow)}</tbody>
      </table>
    </>}
    {selected && recording && <section className="event-source" aria-label="Event source video">
      <div className="section-heading"><h3>{recording.camera_label} · {selected.title}</h3><button onClick={() => setSelected(null)}>Close source</button></div>
      <video key={`${selected.id}-${selected.recording_id}-${selected.local_seconds}`} ref={video} controls preload="metadata"
        src={recording.media_url} onLoadedMetadata={() => { if (video.current) video.current.currentTime = selected.local_seconds; }} />
    </section>}
  </details>;
}
