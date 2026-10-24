// A small force-directed layout (no dependency) and the helpers the wiki graph view shares with its tests.
// The simulation is the usual one: nodes push each other away, linked nodes pull together, a weak pull to the centre keeps
// separate clusters on screen. Positions are plain numbers around (0, 0); the view translates and scales them.

export interface SimInput {
  id: string;
  r: number;
}

export interface SimEdge {
  source: string;
  target: string;
  kind: string;
}

export interface SimNode extends SimInput {
  x: number;
  y: number;
  vx: number;
  vy: number;
  pinned: boolean;
}

interface Spring {
  a: number;
  b: number;
  rest: number;
  k: number;
}

const SPRING: Record<string, { rest: number; k: number }> = {
  link: { rest: 70, k: 0.4 },
  mention: { rest: 90, k: 0.25 },
  tag: { rest: 100, k: 0.2 },
  shared_tag: { rest: 130, k: 0.08 },
};
const REPEL = 160;
const REACH = 420; // nodes further apart than this do not push each other
const GRAVITY = 0.04;
const DAMPING = 0.6;
const DECAY = 0.985;
export const SETTLED = 0.02;

export class ForceLayout {
  readonly nodes: SimNode[];
  readonly index: Map<string, number>;
  alpha = 1;
  ticks = 0;
  private readonly springs: Spring[] = [];

  /** `seeds` are earlier positions (by id) so a refreshed graph keeps its shape; the rest start on a spiral. */
  constructor(inputs: SimInput[], edges: SimEdge[], seeds?: ReadonlyMap<string, { x: number; y: number }>) {
    this.index = new Map();
    this.nodes = inputs.map((n, i) => {
      this.index.set(n.id, i);
      const seed = seeds?.get(n.id);
      const angle = i * 2.399963; // the golden angle: no two nodes start on the same spot
      const radius = 18 * Math.sqrt(i + 1);
      return { ...n, x: seed?.x ?? Math.cos(angle) * radius, y: seed?.y ?? Math.sin(angle) * radius, vx: 0, vy: 0, pinned: false };
    });
    for (const e of edges) {
      const a = this.index.get(e.source);
      const b = this.index.get(e.target);
      if (a === undefined || b === undefined || a === b) continue;
      const s = SPRING[e.kind] ?? SPRING.link!;
      this.springs.push({ a, b, rest: s.rest, k: s.k });
    }
    if (seeds && seeds.size > 0) this.alpha = 0.5;
  }

  get settled(): boolean {
    return this.alpha < SETTLED;
  }

  reheat(alpha = 0.3): void {
    this.alpha = Math.max(this.alpha, alpha);
  }

  tick(): void {
    const { nodes, springs, alpha } = this;
    const n = nodes.length;
    for (let i = 0; i < n; i++) {
      const p = nodes[i]!;
      for (let j = i + 1; j < n; j++) {
        const q = nodes[j]!;
        let dx = q.x - p.x;
        let dy = q.y - p.y;
        let d2 = dx * dx + dy * dy;
        if (d2 > REACH * REACH) continue;
        if (d2 < 0.01) {
          dx = (i % 2 === 0 ? 1 : -1) * 0.1;
          dy = (j % 2 === 0 ? 1 : -1) * 0.1;
          d2 = dx * dx + dy * dy;
        }
        const d = Math.sqrt(d2);
        const f = (REPEL * alpha) / d2;
        const touch = p.r + q.r + 6;
        const push = d < touch ? (touch - d) * 0.5 : 0;
        const fx = (dx / d) * (f * d + push);
        const fy = (dy / d) * (f * d + push);
        p.vx -= fx;
        p.vy -= fy;
        q.vx += fx;
        q.vy += fy;
      }
    }
    for (const s of springs) {
      const p = nodes[s.a]!;
      const q = nodes[s.b]!;
      const dx = q.x - p.x;
      const dy = q.y - p.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const pull = ((d - s.rest) / d) * s.k * alpha * 0.5;
      p.vx += dx * pull;
      p.vy += dy * pull;
      q.vx -= dx * pull;
      q.vy -= dy * pull;
    }
    for (const p of nodes) {
      if (p.pinned) {
        p.vx = 0;
        p.vy = 0;
        continue;
      }
      p.vx -= p.x * GRAVITY * alpha;
      p.vy -= p.y * GRAVITY * alpha;
      p.vx *= DAMPING;
      p.vy *= DAMPING;
      p.x += p.vx;
      p.y += p.vy;
    }
    this.alpha *= DECAY;
    this.ticks += 1;
  }

  /** Run until the layout is calm (or `max` ticks): for small graphs that should appear already settled. */
  settle(max = 300): void {
    for (let i = 0; i < max && !this.settled; i++) this.tick();
  }
}

export interface Transform {
  x: number;
  y: number;
  k: number;
}

/** The translate/scale that shows all nodes inside a `width` x `height` box, never magnified past `maxK`. */
export function fitTransform(nodes: readonly { x: number; y: number; r: number }[], width: number, height: number, pad = 28, maxK = 1.4): Transform {
  if (nodes.length === 0) return { x: width / 2, y: height / 2, k: 1 };
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const n of nodes) {
    x0 = Math.min(x0, n.x - n.r);
    y0 = Math.min(y0, n.y - n.r);
    x1 = Math.max(x1, n.x + n.r);
    y1 = Math.max(y1, n.y + n.r);
  }
  const k = Math.min(maxK, (width - 2 * pad) / Math.max(x1 - x0, 1), (height - 2 * pad) / Math.max(y1 - y0, 1));
  return { x: width / 2 - ((x0 + x1) / 2) * k, y: height / 2 - ((y0 + y1) / 2) * k, k };
}

// -- what the view draws ---------------------------------------------------------------------------------------------

export const PALETTE_SIZE = 8;
export const NO_COLOR = -1;
const TOP_TAGS = PALETTE_SIZE - 1;

interface Colourable {
  id: string;
  kind: string;
  source: string;
  tags: string[];
}

export interface Palette {
  by: "source" | "tag";
  /** colour index per node id; NO_COLOR for nodes that do not belong to any listed group */
  of: Map<string, number>;
  /** the groups, in colour order */
  legend: string[];
}

/** Colour page nodes by source, or by their most used tag (the seven most used tags get a colour, the rest are grey). */
export function paletteOf(nodes: readonly Colourable[], by: "source" | "tag"): Palette {
  const pages = nodes.filter((n) => n.kind === "page");
  const of = new Map<string, number>();
  let legend: string[];
  if (by === "source") {
    legend = [...new Set(pages.map((n) => n.source))].sort().slice(0, PALETTE_SIZE);
    for (const n of pages) of.set(n.id, legend.indexOf(n.source));
  } else {
    const counts = new Map<string, number>();
    for (const n of pages) for (const t of n.tags) counts.set(t, (counts.get(t) ?? 0) + 1);
    legend = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, TOP_TAGS).map(([t]) => t);
    for (const n of pages) {
      const first = n.tags.map((t) => legend.indexOf(t)).filter((i) => i >= 0).sort((a, b) => a - b)[0];
      of.set(n.id, first ?? NO_COLOR);
    }
  }
  return { by, of, legend };
}

/** Radius from the number of connections: a page with many links is a bigger dot. */
export function radiusOf(node: { kind: string; links_in: number; links_out: number; pages: number }): number {
  const degree = node.kind === "page" ? node.links_in + node.links_out : node.pages;
  return Math.min(22, 5 + 2.4 * Math.sqrt(degree));
}

/** The node and everything one edge away from it. */
export function neighbourhood(id: string, edges: readonly { source: string; target: string }[]): Set<string> {
  const out = new Set([id]);
  for (const e of edges) {
    if (e.source === id) out.add(e.target);
    else if (e.target === id) out.add(e.source);
  }
  return out;
}
