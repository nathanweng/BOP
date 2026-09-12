import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { api, messageFor } from './api';
import { formatTime } from './clock';
import type { Recording } from './types';

interface Citation {
  id: string; segment_id: string; recording_id: string; observation: string; attribution: string;
  text: string; local_start: number; local_end: number; incident_start: number; incident_end: number;
}
interface Item {
  id: string; kind: string; label: string; description: string; uncertainty: string;
  source?: string; target?: string; status: string; citations: Citation[];
}
export interface KnowledgeGraph {
  run_id: string; ready: boolean; reason: string; completed: number; expected: number; failed: number;
  state: string; error?: string; version_id?: string; stale: boolean; nodes: Item[]; edges: Item[];
}
interface Props { incidentId: string; incidentTitle: string; runId: string; recordings: Recording[]; incidentTime: number; standalone?: boolean }
const groups = ['person', 'responder', 'event', 'claim', 'location', 'object', 'question', 'recording'];

// Recompute statuses from visible relationships, never from the full graph's future badges.
function projectGraph(graph: KnowledgeGraph, cutoff: number) {
  const visible = (item: Item) => item.citations.every((c) => c.incident_end <= cutoff);
  const nodes = graph.nodes.filter(visible).map((node) => ({ ...node, status: 'active' }));
  const ids = new Set(nodes.map((n) => n.id));
  const edges = graph.edges.filter((e) => visible(e) && ids.has(e.source!) && ids.has(e.target!));
  for (const node of nodes) {
    const kinds = edges.filter((e) => e.target === node.id).map((e) => e.kind);
    node.status = kinds.includes('contradicts') ? 'contradicted' : kinds.includes('disputes') ? 'disputed'
      : kinds.includes('updates') ? 'superseded' : 'active';
  }
  return { nodes, edges };
}

export function MindMap(props: Props) {
  return <KnowledgeMap key={props.runId} {...props} />;
}

function KnowledgeMap({ incidentId, incidentTitle, runId, recordings, incidentTime, standalone }: Props) {
  const [follow, setFollow] = useState(false);
  const [selectedId, setSelectedId] = useState<string>();
  const [citationId, setCitationId] = useState<string>();
  const [search, setSearch] = useState('');
  const [zoom, setZoom] = useState(1);
  const [expanded, setExpanded] = useState(false);
  const query = useQuery({ queryKey: ['knowledge', incidentId, runId],
    queryFn: ({ signal }) => api.getKnowledge(incidentId, signal), refetchInterval: 3000, retry: false });
  const build = useMutation({ mutationFn: () => api.buildKnowledge(incidentId),
    onSuccess: () => { void query.refetch(); } });
  const data = query.data?.run_id === runId ? query.data : undefined;
  const graph = data ? projectGraph(data, follow ? incidentTime : Infinity) : { nodes: [], edges: [] };
  const selected = [...graph.nodes, ...graph.edges].find((item) => item.id === selectedId);
  const citation = selected?.citations.find((c) => c.id === citationId);
  const recording = recordings.find((r) => r.id === citation?.recording_id);
  const busy = build.isPending || data?.state === 'queued' || data?.state === 'processing';
  const columns = groups.filter((kind) => graph.nodes.some((n) => n.kind === kind));
  const positions = new Map<string, { x: number; y: number }>();
  columns.forEach((kind, col) => graph.nodes.filter((n) => n.kind === kind)
    .forEach((node, row) => positions.set(node.id, { x: 24 + col * 260, y: 60 + row * 155 })));
  const width = Math.max(600, columns.length * 260 + 24);
  const height = Math.max(300, ...[...positions.values()].map((p) => p.y + 145));
  const choose = (id: string) => { setSelectedId(id); setCitationId(undefined); };
  return <section className={`mind-map knowledge-map${expanded ? ' knowledge-expanded' : ''}`} aria-labelledby="mind-map-heading">
    <div className="mind-map-heading"><div><p className="eyebrow">Statement-based knowledge map</p>
      <h3 id="mind-map-heading">Incident mind map</h3></div>
      <button disabled={!data?.ready || busy || (data.state === 'completed' && !data.stale)}
        onClick={() => build.mutate()}>{busy ? 'Building…' : data?.state === 'failed' ? 'Retry map build' : 'Build knowledge map'}</button></div>
    <p className="knowledge-title">{incidentTitle}</p>
    {(query.isError || build.isError || data?.error) && <p role="alert" className="error">{data?.error || messageFor(query.error || build.error)}</p>}
    {!data?.ready && <p className="knowledge-notice">{data?.reason || 'Checking transcript completion…'}</p>}
    {data?.stale && <p className="knowledge-notice">Sources changed. This is the previous map; build again when transcripts are complete.</p>}
    {!data?.version_id && <p className="mind-map-empty">The completed transcripts will be summarized into people, responders, places, major developments, and their connections. Every claim links to source evidence.</p>}
    {data?.version_id && <>
      <div className="knowledge-toolbar">
        {!standalone && <button onClick={() => setExpanded(!expanded)}>{expanded ? 'Compact map' : 'Expand map'}</button>}
        <label><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> Follow timeline</label>
        <span>{follow ? `Known by ${formatTime(incidentTime)}` : 'Final summary'}</span>
        <input aria-label="Find in knowledge map" placeholder="Find a person or topic" value={search} onChange={(e) => setSearch(e.target.value)} />
        <label>Zoom <select aria-label="Map zoom" value={zoom} onChange={(e) => setZoom(Number(e.target.value))}>
          <option value={0.6}>60%</option><option value={0.8}>80%</option><option value={1}>100%</option><option value={1.2}>120%</option>
        </select></label>
      </div>
      {!graph.nodes.length && <p>No supported connections {follow ? 'at this time' : 'were found in these transcripts'}.</p>}
      {!!graph.nodes.length && <div className="knowledge-scroll" tabIndex={0} aria-label="Knowledge graph canvas">
        <div style={{ width: width * zoom, height: height * zoom }}><div className="knowledge-canvas" style={{ width, height, transform: `scale(${zoom})` }}>
          <svg width={width} height={height} className="knowledge-lines" aria-label="Relationships">
            <defs><marker id={`arrow-${runId}`} markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#698b9b" /></marker></defs>
            {graph.edges.map((edge, index) => {
              const a = positions.get(edge.source!)!, b = positions.get(edge.target!)!;
              const x1 = a.x + 108, y1 = a.y + 108, x2 = b.x + 108, y2 = b.y;
              const bend = 20 + index % 4 * 16;
              return <g key={edge.id} role="button" tabIndex={0} aria-label={`Connection: ${edge.label}`}
                onClick={() => choose(edge.id)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); choose(edge.id); } }}
                className={`knowledge-edge ${selectedId === edge.id ? 'selected' : ''}`}>
                <title>{edge.label}{edge.uncertainty ? ` — ${edge.uncertainty}` : ''}</title>
                <path d={`M${x1},${y1} C${x1 + bend},${y1 + 50} ${x2 + bend},${y2 - 40} ${x2},${y2}`}
                  strokeDasharray={edge.uncertainty || edge.kind === 'possible_same_as' ? '6 4' : undefined} markerEnd={`url(#arrow-${runId})`} />
                <text x={(x1 + x2) / 2 + bend} y={(y1 + y2) / 2} textAnchor="middle">{edge.kind.replaceAll('_', ' ')}</text>
              </g>;
            })}
          </svg>
          {columns.map((kind, col) => <span className="knowledge-column" key={kind} style={{ left: 24 + col * 260 }}>{kind === 'responder' ? 'Police & responders' : `${kind}s`}</span>)}
          {graph.nodes.map((node) => <button key={node.id} className={`knowledge-node knowledge-${node.status}${selectedId === node.id ? ' selected' : ''}${search && !`${node.label} ${node.description}`.toLowerCase().includes(search.toLowerCase()) ? ' dimmed' : ''}`}
            style={{ left: positions.get(node.id)!.x, top: positions.get(node.id)!.y }} onClick={() => choose(node.id)}>
            <small>{node.kind} · {node.status}</small><strong>{node.label}</strong>
            <span>{node.uncertainty ? 'Tentative · ' : ''}{node.citations.length} source observation{node.citations.length === 1 ? '' : 's'}</span>
          </button>)}
        </div></div>
      </div>}
      <p className="knowledge-legend">Dashed: tentative connection · Yellow: disputed or updated · Red: contradicted statement. Select a node or connection to inspect its evidence.</p>
    </>}
    {selected && <aside className="knowledge-inspector" aria-label="Map evidence inspector">
      <div className="mind-map-heading"><h4>{selected.label}</h4><button onClick={() => setSelectedId(undefined)}>Close evidence</button></div>
      <p>{selected.description}</p>{selected.uncertainty && <p className="knowledge-notice">{selected.uncertainty}</p>}
      {graph.edges.filter((e) => e.source === selected.id || e.target === selected.id).map((edge) =>
        <button className="knowledge-relation" key={edge.id} onClick={() => choose(edge.id)}>{edge.label}</button>)}
      {selected.citations.map((source) => <div className="knowledge-citation" key={source.id}>
        <p><strong>{source.attribution}</strong>: {source.observation}</p>
        <button onClick={() => setCitationId(source.id)}>Open source · {recordings.find((r) => r.id === source.recording_id)?.camera_label || 'Recording'} · {formatTime(source.incident_start)}</button>
        <blockquote>{source.text}</blockquote>
      </div>)}
      {citation && recording && <div><p>Independent source clip · {formatTime(citation.local_start)}–{formatTime(citation.local_end)} recording time</p>
        <video key={citation.id} controls preload="metadata" src={recording.media_url} aria-label="Map source video"
          onLoadedMetadata={(e) => { e.currentTarget.currentTime = citation.local_start; }}
          onSeeking={(e) => { const v = e.currentTarget; if (v.currentTime < citation.local_start || v.currentTime > citation.local_end) v.currentTime = citation.local_start; }}
          onTimeUpdate={(e) => { const v = e.currentTarget; if (v.currentTime >= citation.local_end) { v.pause(); v.currentTime = citation.local_start; } }} />
      </div>}
    </aside>}
  </section>;
}
