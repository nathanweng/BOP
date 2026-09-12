import { useEffect, useMemo, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { api, messageFor } from './api';
import { formatTime } from './clock';
import { boardPositions, boardProjection, scheduleBoard } from './boardReplay';
import type { BoardItem, KnowledgeGraph } from './boardReplay';
import type { Recording } from './types';
export type { KnowledgeGraph } from './boardReplay';

interface Props {
  incidentId: string; incidentTitle: string; runId: string; recordings: Recording[];
  incidentTime: number; standalone?: boolean;
  onSeek: (seconds: number, recordingId: string) => void; seekDisabled?: boolean;
}
type Point = { x: number; y: number };
const kinds: Record<string, string> = { person: 'Person', responder: 'Responder', location: 'Place', object: 'Object',
  event: 'Development', claim: 'Reported claim', question: 'Open question', recording: 'Recording' };

export function MindMap(props: Props) {
  const query = useQuery({ queryKey: ['knowledge', props.incidentId, props.runId],
    queryFn: ({ signal }) => api.getKnowledge(props.incidentId, signal), refetchInterval: 3000, retry: false });
  const retry = useMutation({ mutationFn: () => api.buildKnowledge(props.incidentId), onSuccess: () => { void query.refetch(); } });
  const data = query.data?.run_id === props.runId ? query.data : undefined;
  const busy = data?.state === 'queued' || data?.state === 'processing' || retry.isPending;
  const stages: Record<string, string> = { queued: 'Board queued', analyzing: 'Analyzing sources', preparing: 'Preparing evidence board', ready: 'Board ready', waiting: 'Waiting for transcripts' };
  return <section className="mind-map knowledge-map crime-board" aria-labelledby="mind-map-heading">
    <div className="mind-map-heading"><div><p className="eyebrow">INCIDENT RECONSTRUCTION / EVIDENCE BOARD</p>
      <h2 id="mind-map-heading">Incident mind map</h2></div><span className="board-status"><i className={busy ? 'working' : ''} />
      {busy ? stages[data?.progress?.stage || 'analyzing'] : data?.version_id ? 'Ready to replay' : 'Awaiting transcripts'}</span></div>
    {(query.isError || retry.isError || data?.error) && <div className="error" role="alert">
      <p>{data?.error || messageFor(query.error || retry.error)}</p>
      <button disabled={busy} onClick={() => query.isError ? void query.refetch() : retry.mutate()}>Retry map build</button></div>}
    {(busy || !data?.version_id) && <div className={`board-build ${busy ? 'is-building' : ''}`} role="status">
      <div className="board-build-sketch" aria-hidden="true"><i /><i /><i /><span /></div>
      <div><strong>{busy ? stages[data?.progress?.stage || 'analyzing'] : 'Your evidence board builds automatically.'}</strong>
        <p>{busy ? 'Connecting source-backed observations. You can keep reviewing the incident.' : data?.reason || 'Checking transcript completion…'}</p>
        <small>{busy && data?.progress?.total_batches ? `${data.progress.completed_batches} of ${data.progress.total_batches} source batches complete`
          : data ? `${data.completed} of ${data.expected} transcript windows complete` : 'Loading…'}</small>
        {busy && <div className="board-build-track" aria-hidden="true"><span /></div>}</div>
    </div>}
    {data?.stale && <p className="knowledge-notice">Sources changed. The previous board stays available while the update is prepared.</p>}
    {data?.version_id && <EvidenceBoard key={`${props.runId}:${data.version_id}`} {...props} data={data} />}
  </section>;
}

function EvidenceBoard({ data, recordings, incidentId, incidentTime, onSeek, seekDisabled }: Props & { data: KnowledgeGraph }) {
  const manifest = useMemo(() => scheduleBoard(data), [data]);
  const positions = useMemo(() => boardPositions(data, manifest), [data, manifest]);
  const storageKey = `bop:board-layout:${incidentId}:${data.version_id}`;
  const [moved, setMoved] = useState<Record<string, Point>>(() => {
    try {
      const parsed = JSON.parse(localStorage.getItem(storageKey) || '{}');
      const result: Record<string, Point> = {};
      for (const [id, p] of Object.entries(parsed)) {
        if (positions[id] && p && typeof p === 'object' && 'x' in p && 'y' in p
          && typeof p.x === 'number' && typeof p.y === 'number' && Number.isFinite(p.x) && Number.isFinite(p.y)) result[id] = { x: p.x, y: p.y };
      }
      return result;
    } catch { return {}; }
  });
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [speed, setSpeed] = useState(1);
  const [follow, setFollow] = useState(false);
  const [selectedId, setSelectedId] = useState<string>();
  const [sourceId, setSourceId] = useState<string>();
  const [search, setSearch] = useState('');
  const [focus, setFocus] = useState(false);
  const [showList, setShowList] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState<Point>({ x: 0, y: 0 });
  const camera = useRef<Point>({ x: 0, y: 0 });
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef<{ id?: string; start: Point; origin: Point; moved: boolean } | null>(null);
  const projection = useMemo(() => boardProjection(data, manifest, follow ? Infinity : position, follow ? incidentTime : Infinity), [data, manifest, position, follow, incidentTime]);
  const currentStep = [...manifest.steps].reverse().find((s) => s.at <= position);
  const selected = [...projection.nodes, ...projection.edges].find((n) => n.id === selectedId);
  const active = !selected && playing && position < manifest.duration && currentStep ? [...projection.nodes, ...projection.edges].find((n) => n.id === currentStep.id) : undefined;
  const detail = selected || active;
  const neighborIds = new Set(selected ? [selected.id] : []);
  projection.edges.forEach((e) => { if (e.id === selected?.id || e.source === selected?.id || e.target === selected?.id) { neighborIds.add(e.source!); neighborIds.add(e.target!); } });
  const nodes = projection.nodes.filter((n) => !focus || !selected || neighborIds.has(n.id));
  const ids = new Set(nodes.map((n) => n.id));
  const edges = projection.edges.filter((e) => ids.has(e.source!) && ids.has(e.target!));
  const source = detail?.citations.find((c) => c.id === sourceId);
  const recording = recordings.find((r) => r.id === source?.recording_id);
  const finished = position >= manifest.duration;
  const point = (id: string) => moved[id] || positions[id];
  const pause = () => { setPlaying(false); setPan(camera.current); };
  const choose = (id: string) => { pause(); setSelectedId(id); setSourceId(undefined); };
  const scrub = (value: number) => {
    setPlaying(false); setFollow(false); setSelectedId(undefined); setSourceId(undefined); setPosition(value);
    const step = [...manifest.steps].reverse().find((s) => s.at <= value);
    const id = step?.type === 'node' ? step.id : data.edges.find((e) => e.id === step?.id)?.source;
    const p = id ? point(id) : undefined;
    setPan({ x: 0, y: p ? Math.min(0, 300 - p.y * zoom) : 0 });
  };
  useEffect(() => { try { localStorage.setItem(storageKey, JSON.stringify(moved)); } catch { /* Private storage: keep session layout. */ } }, [moved, storageKey]);
  useEffect(() => {
    if (!playing || follow || finished) return;
    let previous = performance.now();
    const timer = window.setInterval(() => {
      const now = performance.now();
      const delta = Math.min((now - previous) / 1000, .25) * speed;
      previous = now;
      if (!document.hidden) setPosition((p) => Math.min(manifest.duration, p + delta));
    }, 50);
    return () => window.clearInterval(timer);
  }, [playing, follow, speed, manifest.duration, finished]);
  const isPlaying = playing && !finished && !follow;
  const togglePlay = () => {
    setFollow(false); setSelectedId(undefined); setSourceId(undefined);
    if (finished) { setPosition(0); setPlaying(true); } else if (playing) pause(); else setPlaying(true);
  };
  const resetView = () => { setZoom(1); setPan({ x: 0, y: 0 }); };
  const startDrag = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (e.button !== 0 || (e.target as Element).closest('.graph-edge')) return;
    pause();
    const id = (e.target as Element).closest('[data-node-id]')?.getAttribute('data-node-id') || undefined;
    if (id) setSelectedId(id);
    drag.current = { id, start: { x: e.clientX, y: e.clientY }, origin: id ? point(id) : camera.current, moved: false };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const moveDrag = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (!drag.current || !svg.current) return;
    const d = drag.current, rect = svg.current.getBoundingClientRect();
    const ratio = Math.min(rect.width / 1300, rect.height / 760);
    const dx = (e.clientX - d.start.x) / ratio, dy = (e.clientY - d.start.y) / ratio;
    if (Math.hypot(dx, dy) < 4 && !d.moved) return;
    d.moved = true;
    if (d.id) setMoved((old) => ({ ...old, [d.id!]: { x: d.origin.x + dx / zoom, y: d.origin.y + dy / zoom } }));
    else setPan({ x: d.origin.x + dx, y: d.origin.y + dy });
  };
  // Follow the newly introduced evidence vertically without changing any card positions.
  const activePoint = currentStep ? point(currentStep.type === 'node' ? currentStep.id : data.edges.find((e) => e.id === currentStep.id)?.source || '') : undefined;
  const cameraY = playing && !follow && activePoint ? Math.min(0, 300 - activePoint.y * zoom) : pan.y;
  useEffect(() => { camera.current = { x: pan.x, y: cameraY }; }, [pan.x, cameraY]);
  const hiddenMatches = nodes.filter((n) => `${n.label} ${n.description}`.toLowerCase().includes(search.toLowerCase())).length;
  return <>
    <div className="board-player" role="group" aria-label="Reconstruction playback">
      <button className="primary board-play" disabled={!manifest.duration} onClick={togglePlay}>{isPlaying ? 'Pause reconstruction' : finished ? 'Replay reconstruction' : 'Play reconstruction'}</button>
      <input type="range" aria-label="Reconstruction progress" min={0} max={manifest.duration || 1} step={.1} value={position} onChange={(e) => scrub(Number(e.target.value))} />
      <output>{formatTime(position)} / {formatTime(manifest.duration)}</output>
      <select aria-label="Reconstruction speed" value={speed} onChange={(e) => setSpeed(Number(e.target.value))}><option value={.5}>0.5×</option><option value={1}>1×</option><option value={2}>2×</option><option value={4}>4×</option></select>
      <button onClick={() => scrub(manifest.duration)}>Final board</button>
    </div>
    <div className="knowledge-toolbar board-toolbar">
      <input type="search" aria-label="Find in knowledge map" placeholder="Find visible evidence…" value={search} onChange={(e) => setSearch(e.target.value)} />
      <label><input type="checkbox" checked={follow} onChange={(e) => { setPlaying(false); setSelectedId(undefined); setFollow(e.target.checked); }} /> Follow timeline</label>
      <label><input type="checkbox" checked={focus} disabled={!selected} onChange={(e) => setFocus(e.target.checked)} /> Focus connections</label>
      <button onClick={() => { setPlaying(false); setShowList(!showList); }}>{showList ? 'Hide evidence list' : 'Evidence list'}</button>
      <span className="graph-count">{search ? `${hiddenMatches} matches` : `${nodes.length} evidence points · ${edges.length} connections`}</span>
    </div>
    <div className={`board-workspace ${detail ? 'with-details' : ''}`}>
      <div className="board-surface">
        <div className="board-caption"><span className="eyebrow">SOURCE-BASED RECONSTRUCTION</span><span>Incident time {formatTime(follow ? incidentTime : currentStep?.incident_time || 0)}</span></div>
        <svg ref={svg} className="board-svg" viewBox="0 0 1300 760" role="group" aria-label="Interactive incident graph" tabIndex={0}
          onPointerDown={startDrag} onPointerMove={moveDrag} onPointerCancel={() => { drag.current = null; }}
          onPointerUp={(e) => { const d = drag.current; if (d?.id && !d.moved) choose(d.id); drag.current = null; if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId); }}
          onKeyDown={(e) => { if (e.target !== e.currentTarget) return;
            const shifts: Record<string, Point> = { ArrowLeft: { x: 70, y: 0 }, ArrowRight: { x: -70, y: 0 }, ArrowUp: { x: 0, y: 70 }, ArrowDown: { x: 0, y: -70 } };
            if (shifts[e.key]) { e.preventDefault(); setPlaying(false); setPan((p) => ({ x: p.x + shifts[e.key].x, y: p.y + shifts[e.key].y })); }
            if (e.key === ' ') { e.preventDefault(); togglePlay(); }
            if (e.key === '+' || e.key === '=') setZoom((z) => Math.min(2, z + .2));
            if (e.key === '-') setZoom((z) => Math.max(.4, z - .2));
            if (e.key === 'Escape') { setSelectedId(undefined); setSourceId(undefined); }
          }}>
          <defs><marker id="board-arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0 0L0 6L8 3Z" fill="context-stroke" /></marker></defs>
          <g transform={`translate(${pan.x} ${cameraY}) scale(${zoom})`}>
            {edges.map((edge) => {
              const a = point(edge.source!), b = point(edge.target!);
              const curve = `M ${a.x} ${a.y - 73} Q ${(a.x + b.x) / 2} ${Math.min(a.y, b.y) - 130} ${b.x} ${b.y - 73}`;
              return <g key={edge.id} role="button" tabIndex={0} aria-label={`Connection: ${edge.label}`} className={`graph-edge board-thread thread-${edge.kind}${currentStep?.id === edge.id ? ' current' : ''}`}
                onClick={() => choose(edge.id)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); choose(edge.id); } }}>
                <title>{edge.label}</title><path className="edge-hit" d={curve} />
                <path className={`thread-line${edge.uncertainty || edge.kind === 'possible_same_as' ? ' tentative' : ''}`} d={curve} pathLength={1} markerEnd="url(#board-arrow)" />
              </g>;
            })}
            {nodes.map((node, index) => <EvidenceCard key={node.id} item={node} point={point(node.id)} number={index + 1}
              incidentId={incidentId} versionId={data.version_id!} selected={selected?.id === node.id} active={currentStep?.id === node.id}
              dimmed={!!search && !`${node.label} ${node.description}`.toLowerCase().includes(search.toLowerCase())} onChoose={() => choose(node.id)} />)}
          </g>
        </svg>
        {!nodes.length && <div className="board-intro"><span className="eyebrow">FOLLOW THE EVIDENCE</span><h3>{manifest.duration ? 'Watch the incident take shape.' : 'No supported connections found.'}</h3>
          <p>{manifest.duration ? 'Evidence appears in sequence. Connections reveal what changed and what remains uncertain.' : 'The completed sources contain no substantive evidence for a board.'}</p>
          {!!manifest.duration && !follow && <button className="primary" onClick={togglePlay}>Start reconstruction</button>}</div>}
        <div className="graph-controls board-navigation" role="group" aria-label="Graph navigation"><button aria-label="Zoom out" onClick={() => setZoom((z) => Math.max(.4, z - .2))}>−</button>
          <output aria-label="Graph zoom">{Math.round(zoom * 100)}%</output><button aria-label="Zoom in" onClick={() => setZoom((z) => Math.min(2, z + .2))}>+</button>
          <button onClick={resetView}>Reset view</button><button onClick={() => { setMoved({}); resetView(); }}>Reset pins</button></div>
        <p className="board-footnote">Drag cards or board · Schematic relationships, not physical positions</p>
      </div>
      {detail && <aside className="knowledge-inspector board-inspector" aria-label="Map details">
        <div className="inspector-heading"><span className="eyebrow">{selected ? 'EVIDENCE DETAILS' : 'NOW APPEARING'}</span><button aria-label="Close details" onClick={() => { setSelectedId(undefined); setSourceId(undefined); setPlaying(false); }}>×</button></div>
        <h3>{detail.label}</h3><span className={`board-detail-status knowledge-${detail.status}`}>{detail.status === 'active' ? 'Reported' : detail.status}</span>
        <p>{detail.description}</p>{detail.uncertainty && <p className="knowledge-notice">{detail.uncertainty}</p>}
        {projection.edges.filter((e) => e.source === detail.id || e.target === detail.id).map((e) => <button key={e.id} className="knowledge-relation" onClick={() => choose(e.id)}>{e.label} ↗</button>)}
        <h4>Source evidence</h4>{detail.citations.map((c) => <div key={c.id} className="knowledge-citation">
          <small>{recordings.find((r) => r.id === c.recording_id)?.camera_label || 'Recording'} · {formatTime(c.incident_start)}</small>
          <p><strong>{c.attribution}</strong> · {c.observation}</p>
          <details><summary>Transcript excerpt</summary><blockquote>{c.text}</blockquote>
            {!!data.sources?.[c.segment_id]?.time_mentions.length && <small>Literal time mentions: {data.sources[c.segment_id].time_mentions.join(', ')}</small>}</details>
          <button onClick={() => { setPlaying(false); setSelectedId(detail.id); setSourceId(c.id); }}>Review clip</button>{' '}
          <button disabled={seekDisabled} aria-label={`Jump to incident at ${formatTime(c.incident_start)}`} onClick={() => { setPlaying(false); onSeek(c.incident_start, c.recording_id); }}>Jump to incident ↗</button>
        </div>)}
        {source && recording && <video key={source.id} aria-label="Board source video" src={recording.media_url} controls preload="metadata"
          onLoadedMetadata={(e) => { e.currentTarget.currentTime = source.local_start; }}
          onSeeking={(e) => { if (e.currentTarget.currentTime < source.local_start || e.currentTarget.currentTime > source.local_end) e.currentTarget.currentTime = source.local_start; }}
          onTimeUpdate={(e) => { if (e.currentTarget.currentTime >= source.local_end) { e.currentTarget.pause(); e.currentTarget.currentTime = source.local_start; } }} />}
      </aside>}
    </div>
    {showList && <div className="board-evidence-list" aria-label="Visible evidence list">{projection.nodes.map((n) => <button key={n.id} onClick={() => choose(n.id)}>{n.label}<small>{kinds[n.kind] || n.kind} · {n.status}</small></button>)}</div>}
    <div className="graph-legend board-legend"><span>● Pinned source evidence</span><span>— Supported connection</span><span>┄ Tentative connection</span><span>Amber: updated / disputed</span><span>Red: contradicted</span></div>
  </>;
}

function EvidenceCard({ item, point, number, selected, active, dimmed, onChoose, incidentId, versionId }: {
  item: BoardItem; point: Point; number: number; selected: boolean; active: boolean; dimmed: boolean; onChoose: () => void; incidentId: string; versionId: string;
}) {
  const [imageFailed, setImageFailed] = useState(false);
  const citation = item.citations[0];
  return <g data-node-id={item.id} transform={`translate(${point.x} ${point.y})`} role="button" tabIndex={0}
    aria-label={`${item.label}, ${item.kind}, ${item.status}`} aria-pressed={selected}
    className={`graph-node board-card knowledge-${item.status}${selected ? ' selected' : ''}${active ? ' active' : ''}${dimmed ? ' dimmed' : ''}`}
    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onChoose(); } }}>
    <title>{item.label}</title><rect className="board-paper" x={-126} y={-68} width={252} height={170} rx={3} />
    <rect className="board-tape" x={-30} y={-79} width={60} height={20} transform="rotate(-4)" />
    <circle className="node-dot board-pin" cx={0} cy={-72} r={5} />
    <foreignObject x={-111} y={-50} width={222} height={142}><div className="board-card-content">
      <div className="board-card-meta"><span>{kinds[item.kind] || item.kind}</span><span>{String(number).padStart(2, '0')}</span></div>
      <strong>{item.label}</strong>
      <div className="board-card-source">{citation && !imageFailed && <img alt="Source frame" loading="lazy" draggable={false}
        src={`/api/incidents/${incidentId}/knowledge/${versionId}/sources/${citation.segment_id}/thumbnail`} onError={() => setImageFailed(true)} />}
        <span>{citation ? formatTime(citation.incident_start) : ''}<small>{item.status === 'active' ? `${new Set(item.citations.map((c) => c.segment_id)).size} source excerpt(s)` : item.status}</small></span></div>
    </div></foreignObject>
  </g>;
}
