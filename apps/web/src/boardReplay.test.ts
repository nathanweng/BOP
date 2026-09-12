import { describe, expect, it } from 'vitest';
import { boardPositions, boardProjection, scheduleBoard } from './boardReplay';
import type { BoardItem, KnowledgeGraph } from './boardReplay';

const item = (id: string, end: number): BoardItem => ({ id, kind: 'claim', label: id, description: id, uncertainty: '', status: 'active',
  citations: [{ id, segment_id: id, recording_id: 'cam', observation: id, attribution: 'Speaker', text: id,
    local_start: end - 10, local_end: end, incident_start: end - 10, incident_end: end }] });
const graph: KnowledgeGraph = { run_id: 'run', ready: true, reason: '', completed: 2, expected: 2, failed: 0, state: 'completed', stale: false,
  nodes: [item('earlier', 10), item('later', 40)], edges: [{ ...item('correction', 40), source: 'later', target: 'earlier', kind: 'contradicts' }] };

describe('evidence board replay', () => {
  it('reveals endpoints before a correction and reverses the status when rewound', () => {
    const schedule = scheduleBoard(graph);
    expect(schedule.steps.map((s) => s.id)).toEqual(['earlier', 'later', 'correction']);
    expect(boardProjection(graph, schedule, 0).nodes).toEqual([]);
    const before = boardProjection(graph, schedule, schedule.steps[1].at);
    expect(before.nodes[0].status).toBe('active');
    expect(before.edges).toEqual([]);
    expect(boardProjection(graph, schedule, schedule.duration).nodes[0].status).toBe('contradicted');
    expect(boardProjection(graph, schedule, schedule.steps[0].at).nodes[0].status).toBe('active');
  });
  it('compresses source gaps and keeps source time distinct from presentation time', () => {
    const schedule = scheduleBoard(graph);
    expect(schedule.steps[1].incident_time).toBe(40);
    expect(schedule.steps[1].at).toBeLessThan(10);
    expect(boardProjection(graph, schedule, Infinity, 10).nodes.map((n) => n.id)).toEqual(['earlier']);
  });
  it('uses stable positions and scheduling regardless of API item order', () => {
    const reversed = { ...graph, nodes: [...graph.nodes].reverse() };
    expect(scheduleBoard(reversed)).toEqual(scheduleBoard(graph));
    expect(boardPositions(graph, scheduleBoard(graph))).toEqual(boardPositions(reversed, scheduleBoard(reversed)));
  });
});
