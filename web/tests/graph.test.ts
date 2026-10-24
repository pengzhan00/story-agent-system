import { describe, expect, it } from "vitest";
import { ForceLayout, SETTLED, fitTransform, neighbourhood, paletteOf, radiusOf, NO_COLOR } from "../src/lib/graph";

const node = (id: string, over: Record<string, unknown> = {}) => ({ id, kind: "page", source: "docs", tags: [] as string[], links_in: 0, links_out: 0, pages: 0, ...over });
const dist = (a: { x: number; y: number }, b: { x: number; y: number }) => Math.hypot(a.x - b.x, a.y - b.y);

describe("force layout", () => {
  it("settles, keeps nodes apart and draws linked nodes closer than unlinked ones", () => {
    const ids = ["a", "b", "c", "d", "e"].map((id) => ({ id, r: 6 }));
    const layout = new ForceLayout(ids, [
      { source: "a", target: "b", kind: "link" },
      { source: "b", target: "c", kind: "link" },
    ]);
    layout.settle(600);
    expect(layout.settled).toBe(true);
    expect(layout.alpha).toBeLessThan(SETTLED);
    const at = (id: string) => layout.nodes[layout.index.get(id)!]!;
    for (const p of layout.nodes) for (const q of layout.nodes) if (p !== q) expect(dist(p, q)).toBeGreaterThan(8);
    expect(dist(at("a"), at("b"))).toBeLessThan(dist(at("a"), at("e")));
    expect(layout.nodes.every((n) => Number.isFinite(n.x) && Number.isFinite(n.y))).toBe(true);
  });

  it("starts nodes on different spots and survives two nodes on the same spot", () => {
    const seeds = new Map([["a", { x: 5, y: 5 }], ["b", { x: 5, y: 5 }]]);
    const layout = new ForceLayout([{ id: "a", r: 5 }, { id: "b", r: 5 }], [], seeds);
    layout.settle();
    expect(dist(layout.nodes[0]!, layout.nodes[1]!)).toBeGreaterThan(5);
    const fresh = new ForceLayout(["x", "y", "z"].map((id) => ({ id, r: 5 })), []);
    expect(new Set(fresh.nodes.map((n) => `${n.x.toFixed(2)},${n.y.toFixed(2)}`)).size).toBe(3);
  });

  it("keeps earlier positions as the start of a refreshed graph, and a pinned node stays where it was put", () => {
    const first = new ForceLayout([{ id: "a", r: 5 }, { id: "b", r: 5 }], [{ source: "a", target: "b", kind: "link" }]);
    first.settle();
    const seeds = new Map(first.nodes.map((n) => [n.id, { x: n.x, y: n.y }]));
    const again = new ForceLayout([{ id: "a", r: 5 }, { id: "b", r: 5 }, { id: "c", r: 5 }], [{ source: "a", target: "b", kind: "link" }], seeds);
    expect(again.nodes[0]!.x).toBe(first.nodes[0]!.x);
    expect(again.alpha).toBeLessThan(1);            // a refresh does not explode the picture
    again.nodes[2]!.pinned = true;
    const { x, y } = again.nodes[2]!;
    again.reheat(1);
    for (let i = 0; i < 20; i++) again.tick();
    expect([again.nodes[2]!.x, again.nodes[2]!.y]).toEqual([x, y]);
  });

  it("ignores edges to nodes that are not drawn, and handles several hundred nodes", () => {
    const ids = Array.from({ length: 400 }, (_, i) => ({ id: `n${i}`, r: 6 }));
    const edges = ids.slice(1).map((n, i) => ({ source: n.id, target: `n${Math.floor(i / 3)}`, kind: "link" }));
    const layout = new ForceLayout(ids, [...edges, { source: "n1", target: "ghost", kind: "link" }]);
    const t0 = Date.now();
    for (let i = 0; i < 10; i++) layout.tick();
    expect(Date.now() - t0).toBeLessThan(5000);      // generous: a tick of 400 nodes takes a few milliseconds
    expect(layout.nodes.every((n) => Number.isFinite(n.x))).toBe(true);
  });
});

describe("view helpers", () => {
  it("fits all nodes into the box and never magnifies a tiny graph past the limit", () => {
    const nodes = [{ x: -300, y: -100, r: 10 }, { x: 500, y: 200, r: 10 }];
    const f = fitTransform(nodes, 800, 520);
    for (const n of nodes) {
      expect(n.x * f.k + f.x).toBeGreaterThanOrEqual(0);
      expect(n.x * f.k + f.x).toBeLessThanOrEqual(800);
      expect(n.y * f.k + f.y).toBeGreaterThanOrEqual(0);
      expect(n.y * f.k + f.y).toBeLessThanOrEqual(520);
    }
    expect(fitTransform([{ x: 0, y: 0, r: 5 }], 800, 520).k).toBeLessThanOrEqual(1.4);
    expect(fitTransform([], 800, 520)).toEqual({ x: 400, y: 260, k: 1 });
  });

  it("colours by source, or by the most used tags with the rest grey", () => {
    const nodes = [
      node("p1", { source: "obsidian", tags: ["AI", "video"] }),
      node("p2", { source: "docs", tags: ["video"] }),
      node("p3", { source: "docs", tags: ["rare"] }),
      node("t", { kind: "tag", source: "" }),
    ];
    const bySource = paletteOf(nodes, "source");
    expect(bySource.legend).toEqual(["docs", "obsidian"]);
    expect(bySource.of.get("p1")).toBe(1);
    expect(bySource.of.has("t")).toBe(false);          // only pages are coloured by group
    const byTag = paletteOf(nodes, "tag");
    expect(byTag.legend[0]).toBe("video");
    expect(byTag.of.get("p1")).toBe(byTag.of.get("p2"));
    const many = paletteOf(Array.from({ length: 12 }, (_, i) => node(`q${i}`, { tags: [`tag${i}`] })), "tag");
    expect(many.legend).toHaveLength(7);
    expect([...many.of.values()].filter((i) => i === NO_COLOR)).toHaveLength(5);
  });

  it("sizes by connections and finds the neighbours of a node", () => {
    expect(radiusOf({ kind: "page", links_in: 9, links_out: 7, pages: 0 })).toBeGreaterThan(radiusOf({ kind: "page", links_in: 0, links_out: 0, pages: 0 }));
    expect(radiusOf({ kind: "page", links_in: 900, links_out: 900, pages: 0 })).toBeLessThanOrEqual(22);
    expect(radiusOf({ kind: "tag", links_in: 0, links_out: 0, pages: 9 })).toBeGreaterThan(radiusOf({ kind: "tag", links_in: 0, links_out: 0, pages: 1 }));
    expect([...neighbourhood("a", [{ source: "a", target: "b" }, { source: "c", target: "a" }, { source: "c", target: "d" }])].sort()).toEqual(["a", "b", "c"]);
  });
});
