export interface GraphPoint { x: number; y: number }
interface Node { id: string }
interface Edge { source?: string; target?: string }

// A deterministic spring layout keeps polling from making the graph jump.
// Node spacing reserves room for two short label lines, not just the circles.
export function layoutGraph(nodes: Node[], edges: Edge[]): Map<string, GraphPoint> {
  const ordered = [...nodes].sort((a, b) => a.id.localeCompare(b.id));
  const points = new Map<string, GraphPoint>();
  const adjacency = new Map(ordered.map((node) => [node.id, new Set<string>()]));
  edges.forEach((edge) => {
    if (adjacency.has(edge.source || '') && adjacency.has(edge.target || '')) {
      adjacency.get(edge.source!)!.add(edge.target!);
      adjacency.get(edge.target!)!.add(edge.source!);
    }
  });
  const seen = new Set<string>();
  const components: string[][] = [];
  for (const node of ordered) {
    if (seen.has(node.id)) continue;
    const component = [node.id];
    seen.add(node.id);
    for (let i = 0; i < component.length; i++) {
      for (const neighbor of adjacency.get(component[i])!) {
        if (!seen.has(neighbor)) { seen.add(neighbor); component.push(neighbor); }
      }
    }
    components.push(component);
  }
  components.forEach((component, index) => {
    const centerAngle = index / components.length * Math.PI * 2;
    const centerRadius = components.length > 1 ? Math.sqrt(nodes.length) * 140 : 0;
    component.forEach((id, i) => {
      const angle = i * Math.PI * (3 - Math.sqrt(5));
      const radius = Math.sqrt(i + 1) * 85;
      points.set(id, { x: Math.cos(centerAngle) * centerRadius + Math.cos(angle) * radius,
        y: Math.sin(centerAngle) * centerRadius + Math.sin(angle) * radius });
    });
  });
  for (let step = 0; step < 220; step++) {
    const forces = new Map(ordered.map((node) => [node.id, { x: 0, y: 0 }]));
    for (let i = 0; i < ordered.length; i++) {
      const a = points.get(ordered[i].id)!;
      const fa = forces.get(ordered[i].id)!;
      for (let j = i + 1; j < ordered.length; j++) {
        const b = points.get(ordered[j].id)!;
        const fb = forces.get(ordered[j].id)!;
        const dx = a.x - b.x || 0.01, dy = a.y - b.y || 0.01;
        const distance = Math.max(1, Math.hypot(dx, dy));
        const repel = 6200 / (distance * distance);
        let fx = dx / distance * repel, fy = dy / distance * repel;
        if (Math.abs(dx) < 190 && Math.abs(dy) < 100) {
          // Resolve along the shortest escape axis to prevent label collisions.
          if ((190 - Math.abs(dx)) < (100 - Math.abs(dy))) fx += Math.sign(dx) * (190 - Math.abs(dx)) * .2;
          else fy += Math.sign(dy) * (100 - Math.abs(dy)) * .2;
        }
        fa.x += fx; fa.y += fy; fb.x -= fx; fb.y -= fy;
      }
    }
    for (const edge of edges) {
      const a = points.get(edge.source || ''), b = points.get(edge.target || '');
      if (!a || !b) continue;
      const dx = b.x - a.x, dy = b.y - a.y;
      const distance = Math.max(1, Math.hypot(dx, dy));
      const spring = (distance - 230) * .018;
      const fa = forces.get(edge.source!)!, fb = forces.get(edge.target!)!;
      fa.x += dx / distance * spring; fa.y += dy / distance * spring;
      fb.x -= dx / distance * spring; fb.y -= dy / distance * spring;
    }
    const cooling = 1 - step / 260;
    for (const node of ordered) {
      const p = points.get(node.id)!, f = forces.get(node.id)!;
      p.x += Math.max(-12, Math.min(12, f.x - p.x * .001)) * cooling;
      p.y += Math.max(-12, Math.min(12, f.y - p.y * .001)) * cooling;
    }
  }
  return points;
}
