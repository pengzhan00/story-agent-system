import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { WikiGraphEdge, WikiGraphNode } from "../api/types";
import { useT } from "../i18n";
import { ForceLayout, NO_COLOR, fitTransform, neighbourhood, radiusOf, type Palette, type Transform } from "../lib/graph";

// Colours that read on a light and on a dark page; the 8th is for "none of the listed groups".
export const GRAPH_COLORS = ["#4c6ef5", "#f08c00", "#2f9e44", "#d6336c", "#1098ad", "#9c36b5", "#e8590c", "#5c7cfa"];
const GREY = "#868e96";
const LABELS_FOR_TOP = 24;
const ZOOM_LABELS = 1.5;
const FRAME_BUDGET_MS = 8;
const MAX_TICKS_PER_FRAME = 8;
const FIT_EARLY = 15; // refit the view while the layout is still opening up, then once more, then when it settles
const FIT_LATE = 60;

export const colorOf = (palette: Palette, id: string) => {
  const i = palette.of.get(id) ?? NO_COLOR;
  return i < 0 ? GREY : (GRAPH_COLORS[i] ?? GREY);
};

interface Props {
  nodes: WikiGraphNode[];
  edges: WikiGraphEdge[];
  palette: Palette;
  height: number;
  /** the page that is open: drawn with a ring */
  current?: string;
  /** small, still picture for the page reader: no zoom controls or hover card */
  compact?: boolean;
  /** a search is on: pages that only give context are faded */
  fadeContext?: boolean;
  /** pages an answer rests on: drawn with a highlight */
  marked?: ReadonlySet<string>;
  onActivate: (node: WikiGraphNode) => void;
}

const WIDTH = 800; // the drawing box; the svg scales to the width it is given

export function WikiGraph({ nodes, edges, palette, height, current, compact = false, fadeContext = false, marked, onActivate }: Props) {
  const t = useT();
  const shown = useMemo(() => nodes.filter((n) => !n.isolated), [nodes]);
  const radius = useMemo(() => new Map(shown.map((n) => [n.id, radiusOf(n)])), [shown]);
  const drawn = useMemo(() => {
    const ids = new Set(shown.map((n) => n.id));
    return edges.filter((e) => ids.has(e.source) && ids.has(e.target));
  }, [shown, edges]);
  const [hover, setHover] = useState<string | null>(null);
  const [zoomedIn, setZoomedIn] = useState(false);
  const active = hover ?? (compact ? null : (current ?? null)); // the small picture only reacts to the pointer
  const near = useMemo(() => (active ? neighbourhood(active, drawn) : null), [active, drawn]);
  const labelled = useMemo(() => {
    const degree = new Map<string, number>();
    for (const e of drawn) {
      degree.set(e.source, (degree.get(e.source) ?? 0) + 1);
      degree.set(e.target, (degree.get(e.target) ?? 0) + 1);
    }
    const ranked = [...shown].sort((a, b) => (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0)).slice(0, compact ? shown.length : LABELS_FOR_TOP);
    return new Set(ranked.map((n) => n.id));
  }, [shown, drawn, compact]);

  const svgRef = useRef<SVGSVGElement>(null);
  const world = useRef<SVGGElement>(null);
  const nodeEls = useRef(new Map<string, SVGGElement>());
  const edgeEls = useRef(new Map<number, SVGLineElement>());
  const layout = useRef<ForceLayout | null>(null);
  const seeds = useRef(new Map<string, { x: number; y: number }>());
  const view = useRef<Transform>({ x: WIDTH / 2, y: height / 2, k: 1 });
  const touched = useRef(false); // the user moved or zoomed: stop re-fitting on their behalf
  const frame = useRef<number | null>(null);
  const fitAt = useRef(FIT_EARLY);
  const drag = useRef<{ kind: "pan" | "node"; id: string; sx: number; sy: number; vx: number; vy: number; moved: boolean } | null>(null);
  const lastSize = useRef({ w: WIDTH, h: height });

  const drawnRef = useRef(drawn);
  drawnRef.current = drawn;

  const paint = useCallback(() => {
    const lay = layout.current;
    if (!lay) return;
    for (const n of lay.nodes) nodeEls.current.get(n.id)?.setAttribute("transform", `translate(${n.x.toFixed(1)} ${n.y.toFixed(1)})`);
    for (const [i, el] of edgeEls.current) {
      const e = drawnRef.current[i];
      const a = e ? lay.index.get(e.source) : undefined;
      const b = e ? lay.index.get(e.target) : undefined;
      if (a === undefined || b === undefined) continue;
      const p = lay.nodes[a]!;
      const q = lay.nodes[b]!;
      el.setAttribute("x1", p.x.toFixed(1));
      el.setAttribute("y1", p.y.toFixed(1));
      el.setAttribute("x2", q.x.toFixed(1));
      el.setAttribute("y2", q.y.toFixed(1));
    }
  }, []);

  const applyView = useCallback(() => {
    const v = view.current;
    world.current?.setAttribute("transform", `translate(${v.x.toFixed(1)} ${v.y.toFixed(1)}) scale(${v.k.toFixed(3)})`);
    setZoomedIn((was) => (was === v.k >= ZOOM_LABELS ? was : v.k >= ZOOM_LABELS));
  }, []);

  const fit = useCallback(() => {
    const lay = layout.current;
    if (!lay) return;
    view.current = fitTransform(lay.nodes, lastSize.current.w, lastSize.current.h, compact ? 16 : 28, compact ? 1.2 : 1.4);
    applyView();
  }, [applyView, compact]);

  const run = useCallback(() => {
    if (frame.current !== null || typeof requestAnimationFrame !== "function") return;
    const step = () => {
      frame.current = null;
      const lay = layout.current;
      if (!lay) return;
      // as many ticks as fit in a frame's time budget, so a slow screen still settles quickly
      const start = performance.now();
      let done = 0;
      while (!lay.settled && done < MAX_TICKS_PER_FRAME && (done === 0 || performance.now() - start < FRAME_BUDGET_MS)) {
        lay.tick();
        done += 1;
      }
      paint();
      if (!touched.current && (lay.settled || lay.ticks >= fitAt.current)) {
        fit();
        fitAt.current = lay.ticks < FIT_EARLY ? FIT_EARLY : lay.ticks < FIT_LATE ? FIT_LATE : Infinity;
      }
      if (!lay.settled) frame.current = requestAnimationFrame(step);
    };
    frame.current = requestAnimationFrame(step);
  }, [paint, fit]);

  // (Re)build the simulation when the graph changes; positions of nodes that stay are kept.
  useEffect(() => {
    const lay = new ForceLayout(
      shown.map((n) => ({ id: n.id, r: radius.get(n.id) ?? 6 })),
      drawn,
      seeds.current,
    );
    layout.current = lay;
    fitAt.current = FIT_EARLY;
    if (shown.length <= 40 || typeof requestAnimationFrame !== "function") lay.settle();
    paint();
    if (!touched.current || seeds.current.size === 0) fit();
    run();
    return () => {
      if (frame.current !== null && typeof cancelAnimationFrame === "function") cancelAnimationFrame(frame.current);
      frame.current = null;
      for (const n of lay.nodes) seeds.current.set(n.id, { x: n.x, y: n.y });
    };
  }, [shown, drawn, radius, paint, fit, run]);

  useEffect(() => {
    lastSize.current = { w: WIDTH, h: height };
    if (!touched.current) fit();
  }, [height, fit]);

  const zoomAt = useCallback(
    (cx: number, cy: number, factor: number) => {
      const v = view.current;
      const k = Math.min(4, Math.max(0.2, v.k * factor));
      const f = k / v.k;
      view.current = { k, x: cx - (cx - v.x) * f, y: cy - (cy - v.y) * f };
      touched.current = true;
      applyView();
    },
    [applyView],
  );

  // Wheel zoom needs a non-passive listener (React's onWheel is passive).
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg || compact) return;
    const onWheel = (ev: WheelEvent) => {
      ev.preventDefault();
      const box = svg.getBoundingClientRect();
      zoomAt(((ev.clientX - box.left) / box.width) * WIDTH, ((ev.clientY - box.top) / box.height) * height, Math.exp(-ev.deltaY * 0.0015));
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, [compact, height, zoomAt]);

  const toBox = (ev: { clientX: number; clientY: number }) => {
    const box = svgRef.current!.getBoundingClientRect();
    return { x: (((ev.clientX || 0) - box.left) / (box.width || 1)) * WIDTH, y: (((ev.clientY || 0) - box.top) / (box.height || 1)) * height };
  };

  const down = (ev: React.PointerEvent<SVGSVGElement>) => {
    const target = (ev.target as Element).closest("[data-node]");
    const p = toBox(ev);
    const id = target?.getAttribute("data-node") ?? "";
    drag.current = { kind: id ? "node" : "pan", id, sx: p.x, sy: p.y, vx: view.current.x, vy: view.current.y, moved: false };
    if (!compact) ev.currentTarget.setPointerCapture?.(ev.pointerId);
  };
  const move = (ev: React.PointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    if (!d || compact) return;
    const p = toBox(ev);
    if (!d.moved && Math.hypot(p.x - d.sx, p.y - d.sy) < 4) return;
    d.moved = true;
    if (d.kind === "pan") {
      view.current = { ...view.current, x: d.vx + (p.x - d.sx), y: d.vy + (p.y - d.sy) };
      touched.current = true;
      applyView();
      return;
    }
    const lay = layout.current;
    const node = lay?.nodes[lay.index.get(d.id) ?? -1];
    if (!lay || !node) return;
    const v = view.current;
    node.x = (p.x - v.x) / v.k;
    node.y = (p.y - v.y) / v.k;
    node.pinned = true;
    lay.reheat(0.3);
    paint();
    run();
  };
  const up = () => {
    const d = drag.current;
    drag.current = null;
    if (!d) return;
    const lay = layout.current;
    const node = d.kind === "node" && lay ? lay.nodes[lay.index.get(d.id) ?? -1] : undefined;
    if (node) node.pinned = false;
    if (d.kind === "node" && !d.moved) {
      const hit = shown.find((n) => n.id === d.id);
      if (hit) onActivate(hit);
    }
  };

  const hovered = hover ? shown.find((n) => n.id === hover) : undefined;
  const dim = (id: string) => near !== null && !near.has(id);
  const nodeShape = (n: WikiGraphNode) => {
    const r = radius.get(n.id) ?? 6;
    if (n.kind === "tag") return <rect x={-r} y={-r} width={2 * r} height={2 * r} rx={3} />;
    if (n.kind === "model") return <polygon points={`0,${-r * 1.3} ${r * 1.3},0 0,${r * 1.3} ${-r * 1.3},0`} />;
    return <circle r={r} />;
  };

  return (
    <div className={compact ? "wiki-graph compact" : "wiki-graph"}>
      <svg
        ref={svgRef}
        className={zoomedIn ? "wiki-graph-svg labels-all" : "wiki-graph-svg"}
        viewBox={`0 0 ${WIDTH} ${height}`}
        role="img"
        aria-label={t("web.wiki.graph.aria")}
        style={compact ? { height } : undefined}
        onPointerDown={down}
        onPointerMove={move}
        onPointerUp={up}
        onPointerLeave={() => setHover(null)}
      >
        <g ref={world}>
          <g className="g-edges">
            {drawn.map((e, i) => (
              <line
                key={`${e.source}>${e.target}>${e.kind}`}
                ref={(el) => {
                  if (el) edgeEls.current.set(i, el);
                  else edgeEls.current.delete(i);
                }}
                className={`g-edge g-edge-${e.kind}${active && (e.source === active || e.target === active) ? " hl" : near ? " dim" : ""}`}
                strokeWidth={e.kind === "shared_tag" ? 0.8 + e.weight * 0.4 : 1}
              />
            ))}
          </g>
          <g className="g-nodes">
            {shown.map((n) => {
              const label = labelled.has(n.id) || n.id === active || (near?.has(n.id) ?? false);
              const faded = (fadeContext && !n.match) || dim(n.id);
              return (
                <g
                  key={n.id}
                  data-node={n.id}
                  ref={(el) => {
                    if (el) nodeEls.current.set(n.id, el);
                    else nodeEls.current.delete(n.id);
                  }}
                  className={`g-node g-${n.kind}${faded ? " dim" : ""}${n.id === current ? " current" : ""}${marked?.has(n.id) ? " marked" : ""}${label ? " lbl" : ""}`}
                  fill={n.kind === "page" ? colorOf(palette, n.id) : undefined}
                  tabIndex={0}
                  role="link"
                  aria-label={n.label}
                  onPointerEnter={() => setHover(n.id)}
                  onFocus={() => setHover(n.id)}
                  onBlur={() => setHover(null)}
                  onKeyDown={(ev) => {
                    if (ev.key === "Enter" || ev.key === " ") {
                      ev.preventDefault();
                      onActivate(n);
                    }
                  }}
                >
                  <title>{n.label}</title>
                  {nodeShape(n)}
                  <text className="g-label" x={(radius.get(n.id) ?? 6) + 4} y={4}>
                    {n.label}
                  </text>
                </g>
              );
            })}
          </g>
        </g>
      </svg>
      {!compact ? (
        <div className="wiki-graph-tools row">
          <button type="button" aria-label={t("web.wiki.graph.zoom_in")} onClick={() => zoomAt(WIDTH / 2, height / 2, 1.3)}>
            +
          </button>
          <button type="button" aria-label={t("web.wiki.graph.zoom_out")} onClick={() => zoomAt(WIDTH / 2, height / 2, 1 / 1.3)}>
            -
          </button>
          <button
            type="button"
            onClick={() => {
              touched.current = false;
              fit();
            }}
          >
            {t("web.wiki.graph.fit")}
          </button>
        </div>
      ) : null}
      {hovered && !compact ? (
        <div className="wiki-graph-card card small stack" role="status">
          <strong>{hovered.label}</strong>
          {hovered.kind === "page" ? (
            <>
              <span className="row">
                <span className="chip">{hovered.source}</span>
                {hovered.tags.slice(0, 4).map((tag) => (
                  <span key={tag} className="chip">
                    {tag}
                  </span>
                ))}
              </span>
              <span className="muted clamp">{hovered.summary}</span>
              <span className="muted">{t("web.wiki.graph.degree", { in: hovered.links_in, out: hovered.links_out })}</span>
            </>
          ) : hovered.kind === "model" ? (
            <>
              <span className="muted">{t("web.wiki.graph.model_refs", { refs: hovered.refs.join(", ") })}</span>
              <span className="muted">{t("web.wiki.graph.node_pages", { n: hovered.pages })}</span>
            </>
          ) : (
            <span className="muted">{t("web.wiki.graph.node_pages", { n: hovered.pages })}</span>
          )}
        </div>
      ) : null}
    </div>
  );
}
