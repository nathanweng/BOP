import { useEffect, useMemo, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { api, messageFor } from './api';
import { formatTime } from './clock';
import { layoutGraph } from './graphLayout';
import type { GraphPoint } from './graphLayout';
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
interface Props {
  incidentId: string; incidentTitle: string; runId: string; recordings: Recording[];
  incidentTime: number; standalone?: boolean;
  onSeek: (seconds: number, recordingId: string) => void; seekDisabled?: boolean;
}
const kinds: Record<string, { label: string; color: string }> = {
  person: { label: 'People', color: '#c6d8b4' }, responder: { label: 'Responders', color: '#92bebe' },
  event: { label: 'Events', color: '#e4d39f' }, claim: { label: 'Claims', color: '#b8b0d5' },
  location: { label: 'Places', color: '#92b6d7' }, object: { label: 'Objects', color: '#d4b593' },
  question: { label: 'Questions', color: '#dbb4ba' }, recording: { label: 'Recordings', color: '#bac2bb' },
};
function projectGraph(graph: KnowledgeGraph | undefined, cutoff: number) {
  if (!graph) return { nodes: [], edges: [] };
  const visible = (item: Item) => item.citations.every((c) => c.incident_end <= cutoff);
  const nodes = graph.nodes.filter(visible).map((node) => ({ ...node, status: 'active' }));
  const ids = new Set(nodes.map((n) => n.id));
  const edges = graph.edges.filter((e) => visible(e) && ids.has(e.source!) && ids.has(e.target!));
  for (const node of nodes) {
    const relations = edges.filter((e) => e.target === node.id).map((e) => e.kind);
    node.status = relations.includes('contradicts') ? 'contradicted' : relations.includes('disputes') ? 'disputed'
      : relations.includes('updates') ? 'superseded' : 'active';
  }
  return { nodes, edges };
}
function labelLines(label: string) {
  const words = label.split(/\s+/);
  const lines = [''];
  for (const word of words) {
    const i = lines.length - 1;
    if ((lines[i] + ' ' + word).trim().length > 25 && lines[i]) lines.push(word);
    else lines[i] = (lines[i] + ' ' + word).trim();
  }
  return lines.slice(0, 2).map((line, i) => line.length > 25 ? line.slice(0, 24) + '…' : i === 1 && lines.length > 2 ? line.slice(0, 23) + '…' : line);
}
export function MindMap(props: Props) {
  return <KnowledgeMap key={props.runId} {...props} />;
}
function KnowledgeMap({ incidentId, runId, recordings, incidentTime, onSeek, seekDisabled }: Props) {
  const [follow, setFollow] = useState(false);
  const [selectedId, setSelectedId] = useState<string>();
  const [hoveredId, setHoveredId] = useState<string>();
  const [search, setSearch] = useState('');
  const [kind, setKind] = useState('all');
  const [local, setLocal] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [moved, setMoved] = useState<Record<string, GraphPoint>>({});
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef<{ id?: string; x: number; y: number; origin: GraphPoint; moved: boolean } | null>(null);
  const didDrag = useRef(false);
  const query = useQuery({ queryKey: ['knowledge', incidentId, runId],
    queryFn: ({ signal }) => api.getKnowledge(incidentId, signal), refetchInterval: 3000, retry: false });
  const build = useMutation({ mutationFn: () => api.buildKnowledge(incidentId), onSuccess: () => { void query.refetch(); } });
  const data = query.data?.run_id === runId ? query.data : undefined;
  const cutoff = follow ? incidentTime : Infinity;
  const graph = useMemo(() => projectGraph(data, cutoff), [data, cutoff]);
  const positions = useMemo(() => layoutGraph(graph.nodes, graph.edges), [graph]);
  const selected = [...graph.nodes, ...graph.edges].find((item) => item.id === selectedId);
  const active = hoveredId || selected?.id;
  const neighbors = new Set<string>(active ? [active] : []);
  graph.edges.forEach((edge) => {
    if (edge.id === active || edge.source === active || edge.target === active) {
      neighbors.add(edge.id); neighbors.add(edge.source!); neighbors.add(edge.target!);
    }
  });
  const localIds = new Set<string>(selected ? [selected.id] : []);
  graph.edges.forEach((edge) => {
    if (edge.id === selected?.id || edge.source === selected?.id || edge.target === selected?.id) {
      localIds.add(edge.source!); localIds.add(edge.target!);
    }
  });
  const nodes = graph.nodes.filter((node) => (kind === 'all' || node.kind === kind) && (!local || !selected || localIds.has(node.id)));
  const nodeIds = new Set(nodes.map((node) => node.id));
  const edges = graph.edges.filter((edge) => nodeIds.has(edge.source!) && nodeIds.has(edge.target!));
  const matches = new Set(nodes.filter((node) => `${node.label} ${node.description}`.toLowerCase().includes(search.toLowerCase().trim())).map((n) => n.id));
  const point = (id: string) => moved[id] || positions.get(id)!;
  const extent = Math.max(340, ...[...positions.values()].map((p) => Math.abs(p.x) + 110));
  const extentY = Math.max(230, ...[...positions.values()].map((p) => Math.abs(p.y) + 90));
  const scale = Math.min(850 / (extent * 2), 490 / (extentY * 2)) * zoom;
  const choose = (id: string) => { if (!didDrag.current) setSelectedId(id); };
  const reset = () => { setZoom(1); setPan({ x: 0, y: 0 }); setMoved({}); };
  const changeZoom = (delta: number) => setZoom((value) => Math.min(3, Math.max(.5, value + delta)));
  const startDrag = (event: ReactPointerEvent<SVGSVGElement>) => {
    didDrag.current = false;
    if (event.button !== 0 || (event.target as Element).closest('.graph-edge')) return;
    const id = (event.target as Element).closest('[data-node-id]')?.getAttribute('data-node-id') || undefined;
    drag.current = { id, x: event.clientX, y: event.clientY, origin: id ? point(id) : pan, moved: false };
    didDrag.current = false;
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const moveDrag = (event: ReactPointerEvent<SVGSVGElement>) => {
    const current = drag.current;
    if (!current || !svg.current) return;
    const rect = svg.current.getBoundingClientRect();
    const ratio = Math.min(rect.width / 1000, rect.height / 620);
    const dx = (event.clientX - current.x) / ratio, dy = (event.clientY - current.y) / ratio;
    if (Math.hypot(dx, dy) > 3) current.moved = true;
    if (!current.moved) return;
    didDrag.current = true;
    if (current.id) setMoved((value) => ({ ...value, [current.id!]: { x: current.origin.x + dx / scale, y: current.origin.y + dy / scale } }));
    else setPan({ x: current.origin.x + dx, y: current.origin.y + dy });
  };
  useEffect(() => {
    const canvas = svg.current;
    if (!canvas) return;
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      setZoom((value) => Math.min(3, Math.max(.5, value + (event.deltaY < 0 ? .1 : -.1))));
    };
    canvas.addEventListener('wheel', wheel, { passive: false });
    return () => canvas.removeEventListener('wheel', wheel);
  }, [data?.version_id]);
  const busy = build.isPending || data?.state === 'queued' || data?.state === 'processing';
  return <section className="mind-map knowledge-map" aria-labelledby="mind-map-heading">
    <div className="mind-map-heading"><div><p className="eyebrow">RELATIONSHIPS / SOURCE-LINKED CONTEXT</p><h2 id="mind-map-heading">Incident mind map</h2></div>
      <button disabled={!data?.ready || busy || (data.state === 'completed' && !data.stale)} onClick={() => build.mutate()}>
        {busy ? 'Building…' : data?.state === 'failed' ? 'Retry map build' : data?.version_id ? 'Rebuild map' : 'Build mind map'}</button></div>
    {(query.isError || build.isError || data?.error) && <p role="alert" className="error">{data?.error || messageFor(query.error || build.error)} {query.isError && <button onClick={() => void query.refetch()}>Retry</button>}</p>}
    {!data?.ready && !data?.version_id && <p className="knowledge-notice">{data?.reason || 'Checking transcript completion…'}</p>}
    {data?.stale && <p className="knowledge-notice">Sources changed. Rebuild when transcripts are complete.</p>}
    {!data?.version_id && <div className="map-empty"><span aria-hidden="true">◎</span><h3>Connections start with context.</h3><p>Complete the transcripts, then build a map of people, events, and their connections.</p></div>}
    {data?.version_id && <>
      <div className="knowledge-toolbar">
        <input type="search" aria-label="Find in knowledge map" placeholder="Find a person or topic…" value={search} onChange={(e) => setSearch(e.target.value)} />
        <label><span className="sr-only">Node type</span><select aria-label="Node type" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="all">All types</option>{[...new Set(graph.nodes.map((n) => n.kind))].sort().map((value) => <option key={value} value={value}>{kinds[value]?.label || value}</option>)}</select></label>
        <label><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> Follow timeline</label>
        <label><input type="checkbox" checked={local} disabled={!selected} onChange={(e) => setLocal(e.target.checked)} /> Focus connections</label>
        <span className="graph-count" role="status">{search ? `${matches.size} matches` : `${nodes.length} nodes · ${edges.length} links`}</span>
      </div>
      <div className={`graph-workspace${selected ? ' has-inspector' : ''}`}>
        <div className="graph-surface">
          <div className="graph-caption"><span className="status-dot" />{follow ? `Known by ${formatTime(incidentTime)}` : 'Full transcript summary'}</div>
          <svg ref={svg} className="graph-svg" viewBox="0 0 1000 620" tabIndex={0} role="group" aria-label="Interactive incident graph"
            onPointerDown={startDrag} onPointerMove={moveDrag}
            onPointerUp={(e) => { if (drag.current?.id && !drag.current.moved) setSelectedId(drag.current.id); drag.current = null; if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId); }}
            onPointerCancel={() => { drag.current = null; }}

            onKeyDown={(e) => {
              if (e.target !== e.currentTarget) return;
              const shifts: Record<string, GraphPoint> = { ArrowLeft: { x: 40, y: 0 }, ArrowRight: { x: -40, y: 0 }, ArrowUp: { x: 0, y: 40 }, ArrowDown: { x: 0, y: -40 } };
              if (shifts[e.key]) { e.preventDefault(); setPan((p) => ({ x: p.x + shifts[e.key].x, y: p.y + shifts[e.key].y })); }
              if (e.key === '+' || e.key === '=') { e.preventDefault(); changeZoom(.2); }
              if (e.key === '-') { e.preventDefault(); changeZoom(-.2); }
              if (e.key === '0') reset();
              if (e.key === 'Escape') { setSelectedId(undefined); setLocal(false); }
            }}>
            <g transform={`translate(${500 + pan.x} ${290 + pan.y}) scale(${scale})`}>
              {edges.map((edge) => {
                const a = point(edge.source!), b = point(edge.target!);
                const highlighted = neighbors.has(edge.id);
                return <g key={edge.id} role="button" tabIndex={0} aria-label={`Connection: ${edge.label}`}
                  className={`graph-edge${highlighted ? ' highlighted' : ''}${active && !highlighted ? ' dimmed' : ''}`}
                  onClick={() => choose(edge.id)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSelectedId(edge.id); } }}>
                  <title>{edge.label}</title>
                  <line className="edge-hit" x1={a.x} y1={a.y} x2={b.x} y2={b.y} />
                  <line className="edge-line" x1={a.x} y1={a.y} x2={b.x} y2={b.y} strokeDasharray={edge.uncertainty || edge.kind === 'possible_same_as' ? '5 6' : undefined} />
                  {selected?.id === edge.id && <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 12} textAnchor="middle">{edge.kind.replaceAll('_', ' ')}</text>}
                </g>;
              })}
              {nodes.map((node) => {
                const p = point(node.id);
                const degree = edges.filter((edge) => edge.source === node.id || edge.target === node.id).length;
                const color = node.status === 'contradicted' ? '#ef9a9e' : node.status !== 'active' ? '#e4c77c' : kinds[node.kind]?.color || '#bac2bb';
                return <g key={node.id} data-node-id={node.id} role="button" tabIndex={0} aria-label={`${node.label}, ${node.kind}, ${node.status}`}
                  aria-pressed={selected?.id === node.id} transform={`translate(${p.x} ${p.y})`}
                  className={`graph-node knowledge-${node.status}${selected?.id === node.id ? ' selected' : ''}${(search.trim() && !matches.has(node.id)) || (active && !neighbors.has(node.id)) ? ' dimmed' : ''}`}
                  onMouseEnter={() => setHoveredId(node.id)} onMouseLeave={() => setHoveredId(undefined)}
                  onClick={() => choose(node.id)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSelectedId(node.id); } }}>
                  <title>{node.label}{node.uncertainty ? ` — ${node.uncertainty}` : ''}</title>
                  <rect className="node-hit" x="-96" y="-24" width="192" height="96" rx="8" />
                  <circle className="node-halo" r={20 + Math.min(degree, 6)} />
                  <circle className="node-dot" r={7 + Math.min(degree, 6)} fill={color} />
                  <text textAnchor="middle" y="36">{labelLines(node.label).map((line, i) => <tspan key={i} x="0" dy={i ? 19 : 0}>{line}</tspan>)}</text>
                </g>;
              })}
            </g>
          </svg>
          {!nodes.length && <p className="graph-no-results">No connections {follow ? 'at this time' : 'match this filter'}.</p>}
          {!!nodes.length && search.trim() && !matches.size && <p className="graph-no-results">No matching people or topics.</p>}
          <div className="graph-controls" role="group" aria-label="Graph navigation">
            <button onClick={() => changeZoom(-.2)} disabled={zoom <= .5} aria-label="Zoom out">−</button>
            <output aria-label="Graph zoom">{Math.round(zoom * 100)}%</output>
            <button onClick={() => changeZoom(.2)} disabled={zoom >= 3} aria-label="Zoom in">+</button>
            <button onClick={reset}>Reset view</button>
          </div>
          <span className="graph-help">Drag to move · + / − to zoom · Select to inspect</span>
        </div>
        <aside className="knowledge-inspector" aria-label="Map details">
          {selected ? <>
            <div className="inspector-heading"><span className="eyebrow">{selected.kind.replaceAll('_', ' ')} / {selected.status}</span><button aria-label="Close details" onClick={() => { setSelectedId(undefined); setLocal(false); }}>×</button></div>
            <h3>{selected.label}</h3><p>{selected.description}</p>
            {selected.uncertainty && <p className="knowledge-notice">{selected.uncertainty}</p>}
            {graph.edges.some((e) => e.source === selected.id || e.target === selected.id) && <h4>Connections</h4>}
            {graph.edges.filter((e) => e.source === selected.id || e.target === selected.id).map((edge) => <button className="knowledge-relation" key={edge.id} onClick={() => setSelectedId(edge.id)}>{edge.label}<span aria-hidden="true">↗</span></button>)}
            {selected.source && selected.target && <div className="relation-endpoints">{[selected.source, selected.target].map((id) => <button key={id} onClick={() => setSelectedId(id)}>{graph.nodes.find((n) => n.id === id)?.label}</button>)}</div>}
            <h4>Sources <span>{selected.citations.length}</span></h4>
            {selected.citations.map((source) => <div className="knowledge-citation" key={source.id}>
              <button className="map-source-time" disabled={seekDisabled} onClick={() => onSeek(source.incident_start, source.recording_id)} aria-label={`Jump to incident at ${formatTime(source.incident_start)}`}>
                {formatTime(source.incident_start)} <span>{recordings.find((r) => r.id === source.recording_id)?.camera_label || 'Recording'} ↗</span></button>
              <p><strong>{source.attribution}</strong> · {source.observation}</p>
              <details><summary>Transcript excerpt</summary><blockquote>{source.text}</blockquote></details>
            </div>)}
          </> : <div className="inspector-empty"><span className="eyebrow">EXPLORE THE INCIDENT</span><h3>Follow a connection.</h3><p>Select a node to see its context, relationships, and source timestamps.</p><span className="inspector-symbol" aria-hidden="true">◎</span></div>}
        </aside>
      </div>
      <div className="graph-legend">{[...new Set(graph.nodes.map((n) => n.kind))].map((value) => <span key={value}><i style={{ background: kinds[value]?.color || '#bac2bb' }} />{kinds[value]?.label || value}</span>)}<span className="legend-note">Dashed: tentative · Amber: disputed / updated · Red: contradicted</span></div>
    </>}
  </section>;
}
