import { useRef, useState } from 'react';
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
  const history = useQuery({ queryKey, queryFn: ({ signal }) => api.getEvents(incidentId, signal) });
  const generate = useMutation({
    mutationFn: () => api.generateEvents(incidentId),
    onSuccess: (data) => { if (data.run_id === runId) queryClient.setQueryData(queryKey, data); },
  });
  const data = history.data?.run_id === runId ? history.data : undefined;
  const recording = recordings.find((entry) => entry.id === selected?.recording_id);
  const review = (event: HistoryEvent, correction = false) => {
    const source = correction && event.status_recording_id && event.status_local_seconds != null
      ? { ...event, recording_id: event.status_recording_id, local_seconds: event.status_local_seconds }
      : event;
    if (video.current && selected?.recording_id === source.recording_id) video.current.currentTime = source.local_seconds;
    setSelected(source);
  };
  return <details className="panel" open>
    <summary>Event history</summary>
    <p className="muted">A permanent log from completed transcripts. Yellow entries are superseded by later information; red entries are contradicted. Timestamps open source video.</p>
    <button disabled={!data?.configured || generate.isPending} onClick={() => generate.mutate()}>
      {generate.isPending ? 'Generating history…' : 'Generate / update history'}
    </button>
    {data && !data.configured && <p className="muted">Set OPENROUTER_API_KEY in .env and restart the API to enable event history.</p>}
    {history.isPending && <p role="status">Loading history…</p>}
    {history.isError && <p role="alert">{messageFor(history.error)} <button onClick={() => void history.refetch()}>Retry</button></p>}
    {generate.isError && <p role="alert">{messageFor(generate.error)}</p>}
    {data && data.events.length === 0 && <p>No significant events available yet.</p>}
    {!!data?.events.length && <table className="event-history"><thead><tr><th>Timestamp</th><th>Event title</th></tr></thead>
      <tbody>{data.events.map((event) => <tr key={event.id} className={`event-${event.status}`}>
        <td><button onClick={() => review(event)} aria-label={`Review ${event.title} at ${formatTime(event.timestamp_seconds)}`}>
          {formatTime(event.timestamp_seconds)}
        </button></td><td>{event.title}{event.status !== 'current' && event.status_timestamp_seconds != null && <>
          {' '}<button className="event-update-link" onClick={() => review(event, true)}>
            {event.status === 'disproven' ? 'Contradicted' : 'Clarified'} at {formatTime(event.status_timestamp_seconds)}
          </button>
        </>}</td>
      </tr>)}</tbody>
    </table>}
    {selected && recording && <section className="event-source" aria-label="Event source video">
      <div className="section-heading"><h3>{recording.camera_label} · {selected.title}</h3><button onClick={() => setSelected(null)}>Close source</button></div>
      <video key={`${selected.segment_id}-${selected.title}`} ref={video} controls preload="metadata"
        src={recording.media_url} onLoadedMetadata={() => { if (video.current) video.current.currentTime = selected.local_seconds; }} />
    </section>}
  </details>;
}
