import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, messageFor } from './api';
import { formatTime } from './clock';
import type { HistoryEvent, Recording } from './types';

export function EventHistory({ incidentId, runId, recordings, onSeek, seekDisabled, eventTarget }: {
  incidentId: string; runId: string; recordings: Recording[]; eventTarget?: { id: string };
  onSeek: (seconds: number, recordingId: string) => void; seekDisabled: boolean;
}) {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<'all' | 'current' | 'challenged'>('all');
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
  const generateEvents = generate.mutate;
  useEffect(() => {
    if (!queuedKey || kicked.current === queuedKey) return;
    kicked.current = queuedKey;
    generateEvents();
  }, [queuedKey, generateEvents]);
  const events = data?.events ?? [];
  const [revealedTarget, setRevealedTarget] = useState(eventTarget);
  if (eventTarget !== revealedTarget) {
    setRevealedTarget(eventTarget);
    setFilter('all');
  }
  const targetAvailable = !!data?.events.some((event) => event.id === eventTarget?.id);
  useEffect(() => {
    if (!eventTarget || !targetAvailable) return;
    const row = document.getElementById(`event-${eventTarget.id}`);
    row?.focus({ preventScroll: true });
    row?.scrollIntoView({ block: 'center', behavior: 'instant' });
  }, [eventTarget, targetAvailable]);
  const current = events.filter((event) => event.status === 'current');
  const challenged = events.filter((event) => event.status !== 'current');
  const visible = [...(filter === 'current' ? current : filter === 'challenged' ? challenged : events)]
    .sort((a, b) => a.timestamp_seconds - b.timestamp_seconds);
  const status = generate.isPending || data?.state === 'processing' ? 'Analyzing transcripts'
    : data?.state === 'queued' ? 'Analysis queued' : data?.state === 'retrying' ? 'Retrying analysis'
    : data?.state === 'failed' ? 'Analysis needs attention'
    : data?.processed_segments ? 'Up to date' : 'Awaiting transcripts';
  const factRow = (event: HistoryEvent) => {
    const factTime = formatTime(event.timestamp_seconds);
    const updateTime = event.status_timestamp_seconds;
    const source = recordings.find((entry) => entry.id === event.recording_id);
    return <li key={event.id} id={`event-${event.id}`} tabIndex={-1}
      className={`fact-row event-${event.status}${eventTarget?.id === event.id ? ' cited-event' : ''}`}>
      <button className="fact-time" disabled={seekDisabled} onClick={() => onSeek(event.timestamp_seconds, event.recording_id)}
        aria-label={`Jump to incident at ${factTime}`}><span>{factTime}</span><span aria-hidden="true">↗</span></button>
      <div className="fact-content">
        <div className="fact-meta"><span className={`fact-status ${event.status}`}>{event.status === 'current' ? 'Current' : event.status === 'disproven' ? 'Invalidated' : 'Needs review'}</span><span>{source?.camera_label || 'Recording'}</span></div>
        <p className="fact-title">{event.title}</p>
        {event.status !== 'current' && <div className="event-reason">
          {event.status_reason && <p>{event.status_reason}</p>}
          {updateTime != null && <button className="event-update-link" disabled={seekDisabled}
            onClick={() => onSeek(updateTime, event.status_recording_id || event.recording_id)}
            aria-label={`Jump to update at ${formatTime(updateTime)}`}>Updated at {formatTime(updateTime)} <span aria-hidden="true">↗</span></button>}
        </div>}
      </div>
    </li>;
  };
  return <section className="panel event-history-panel" id="event-history" aria-labelledby="event-history-heading">
    <div className="section-heading facts-heading"><div><h2 id="event-history-heading"><span className="section-number">03</span> Events & facts</h2>
      <p>A chronological log of the incident. Color marks facts that later changed.</p></div>
      <span className="analysis-status" role="status"><span className="status-dot" />{status}</span></div>
    <div className="facts-toolbar"><div className="fact-filters" role="group" aria-label="Filter events">
      {(['all', 'current', 'challenged'] as const).map((value) => <button key={value} aria-pressed={filter === value} onClick={() => setFilter(value)}>
        {value === 'all' ? 'All events' : value === 'current' ? 'Current' : 'Needs review'} <span>{value === 'all' ? events.length : value === 'current' ? current.length : challenged.length}</span>
      </button>)}</div><span className="facts-order">INCIDENT TIME ↑</span></div>
    {data?.configured && data.state == null && <p role="status">Event analysis is unavailable. Restart the API to enable it.</p>}
    {data?.error && <div role="alert"><p>{data.error}</p><button disabled={generate.isPending} onClick={() => generate.mutate()}>Retry analysis</button></div>}
    {data && !data.configured && <p className="muted">Event analysis is not configured.</p>}
    {history.isPending && <p role="status">Loading events…</p>}
    {history.isError && <p role="alert">{messageFor(history.error)} <button onClick={() => void history.refetch()}>Retry</button></p>}
    {generate.isError && <p role="alert">{messageFor(generate.error)}</p>}
    {!history.isPending && (visible.length ? <ol className="fact-list" aria-label="Event log">{visible.map(factRow)}</ol>
      : <p className="facts-empty">{events.length ? 'No matching events.' : 'No events yet.'}</p>)}
  </section>;
}
