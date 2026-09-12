import { useQuery } from '@tanstack/react-query';
import { api, messageFor } from './api';
import { MindMap } from './MindMap';

export function MindMapWindow() {
  const id = new URLSearchParams(window.location.search).get('incident');
  const incident = useQuery({ queryKey: ['incident', id], queryFn: ({ signal }) => api.getIncident(id!, signal),
    enabled: Boolean(id), refetchInterval: 3000 });
  const playback = useQuery({ queryKey: ['map-playback', id], queryFn: ({ signal }) => api.getPlayback(id!, signal),
    enabled: Boolean(id), refetchInterval: 1000 });
  const run = playback.data?.playback ?? incident.data?.playback;
  return <main className="mindmap-window">
    <header className="mindmap-window-header"><div><p className="eyebrow">Bodycam · Knowledge map</p>
      <h1>{incident.data?.title || 'Incident mind map'}</h1></div>
      <span className="status">Independent map window</span></header>
    {!id && <p role="alert">Open the mind map from an incident.</p>}
    {incident.isPending && id && <p role="status">Loading incident…</p>}
    {(incident.isError || playback.isError) && <p role="alert">{messageFor(incident.error || playback.error)}</p>}
    {incident.data && run && <MindMap incidentId={incident.data.id} incidentTitle={incident.data.title}
      runId={run.run_id} recordings={incident.data.recordings} incidentTime={run.position_seconds} standalone />}
  </main>;
}
