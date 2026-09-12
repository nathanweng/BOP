export interface BoardCitation {
  id: string; segment_id: string; recording_id: string; observation: string; attribution: string;
  text: string; local_start: number; local_end: number; incident_start: number; incident_end: number;
}
export interface BoardItem {
  id: string; kind: string; label: string; description: string; uncertainty: string;
  source?: string; target?: string; status: string; citations: BoardCitation[];
}
export interface ReplayStep { id: string; type: 'node' | 'edge'; at: number; hold: number; incident_time: number }
export interface ReplayManifest { schema: number; duration: number; steps: ReplayStep[] }
export interface KnowledgeGraph {
  run_id: string; ready: boolean; reason: string; completed: number; expected: number; failed: number;
  state: string; error?: string; version_id?: string; stale: boolean; nodes: BoardItem[]; edges: BoardItem[];
  replay?: ReplayManifest;
  progress?: { stage: string; completed_batches: number; total_batches: number };
  sources?: Record<string, { time_mentions: string[]; speaker_words: unknown[]; text_fingerprint: string }>;
}

export function scheduleBoard(graph: KnowledgeGraph): ReplayManifest {
  if (graph.replay) return graph.replay;
  const known = new Map(graph.nodes.map((n) => [n.id, Math.max(...n.citations.map((c) => c.incident_end))]));
  const entries = [...graph.nodes.map((n) => ({ item: n, type: 'node' as const, time: known.get(n.id)! })),
    ...graph.edges.filter((e) => known.has(e.source!) && known.has(e.target!)).map((e) => ({ item: e, type: 'edge' as const,
      time: Math.max(known.get(e.source!)!, known.get(e.target!)!, ...e.citations.map((c) => c.incident_end)) }))]
    .sort((a, b) => a.time - b.time || (a.type === b.type ? a.item.id.localeCompare(b.item.id) : a.type === 'node' ? -1 : 1));
  let cursor = 1;
  const steps = entries.map(({ item, type, time }) => {
    const hold = Math.min(12, Math.max(4, 3 + item.label.split(/\s+/).length / 3));
    const step = { id: item.id, type, incident_time: time, at: cursor, hold };
    cursor += hold;
    return step;
  });
  return { schema: 1, duration: steps.length ? cursor : 0, steps };
}

export function boardProjection(graph: KnowledgeGraph, manifest: ReplayManifest, position: number, cutoff = Infinity) {
  const shown = new Set(manifest.steps.filter((s) => s.at <= position && s.incident_time <= cutoff).map((s) => s.id));
  const nodes = graph.nodes.filter((n) => shown.has(n.id)).map((n) => ({ ...n, status: 'active' }));
  const ids = new Set(nodes.map((n) => n.id));
  const edges = graph.edges.filter((e) => shown.has(e.id) && ids.has(e.source!) && ids.has(e.target!));
  for (const n of nodes) {
    const kinds = edges.filter((e) => e.target === n.id).map((e) => e.kind);
    n.status = kinds.includes('contradicts') ? 'contradicted' : kinds.includes('disputes') ? 'disputed'
      : kinds.includes('updates') ? 'superseded' : 'active';
  }
  return { nodes, edges };
}

export function boardPositions(graph: KnowledgeGraph, manifest: ReplayManifest) {
  const nodes = new Map(graph.nodes.map((n) => [n.id, n]));
  const points: Record<string, { x: number; y: number }> = {};
  manifest.steps.filter((s) => s.type === 'node' && nodes.has(s.id)).forEach((step, i) => {
    // Fixed pin slots, independent of playback/filtering; no simulation jumps on polling.
    const row = Math.floor(i / 4), column = row % 2 ? 3 - i % 4 : i % 4;
    points[step.id] = { x: 170 + column * 305, y: 155 + row * 230 + (column % 2 ? 24 : 0) };
  });
  return points;
}
