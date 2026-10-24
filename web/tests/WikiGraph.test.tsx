import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../src/i18n";
import { WikiPage } from "../src/pages/WikiPage";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;
const fill = (key: string, params: Record<string, string | number>) => Object.entries(params).reduce((s, [k, v]) => s.replace(`{${k}}`, String(v)), en[key]!);

const gnode = (id: string, label: string, over: Record<string, unknown> = {}) => ({
  id, kind: "page", label, slug: id, source: "vault", rating: "general", tags: ["AI"], summary: `about ${label}`, links_in: 1, links_out: 1, pages: 0, refs: [], model_kind: "", isolated: false, match: true, ...over,
});
const gedge = (source: string, target: string, kind = "link", weight = 1) => ({ source, target, kind, weight });
const graphOf = (nodes: unknown[], edges: unknown[], over: Record<string, unknown> = {}) => ({
  nodes, edges, total: nodes.length, hidden: 0, matched: nodes.length, truncated: false, limit: 300, focus: "", depth: 1,
  sources: [{ name: "vault", count: 3 }, { name: "docs", count: 2 }], tags: [{ name: "AI", count: 3 }], ...over,
});
const brief = (slug: string, title: string) => ({ slug, title, source: "vault", path: `${title}.md`, rating: "general", tags: ["AI"], summary: "", snippet: "", updated: null });

const base = graphOf(
  [gnode("vault/A", "Alpha"), gnode("vault/B", "Beta", { source: "docs" }), gnode("vault/Lonely", "Lonely page", { isolated: true, links_in: 0, links_out: 0 })],
  [gedge("vault/A", "vault/B")],
);

let calls: string[] = [];
let graphs: (url: string) => unknown = () => base;

beforeEach(() => {
  calls = [];
  graphs = () => base;
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string, init?: { method?: string }) => {
    calls.push(`${init?.method ?? "GET"} ${url}`);
    let json: unknown = {};
    if (url.includes("/api/i18n")) json = en;
    else if (url.includes("/api/wiki/graph")) json = graphs(url);
    else if (url.includes("/api/wiki/pages/")) json = { page: brief("vault/A", "Alpha"), meta: {}, links: [], backlinks: [], body: "# Alpha\n\ntext\n" };
    else if (url.includes("/api/wiki/pages")) json = { pages: [], total: 0, hidden: 0, sources: [], tags: [], synced_at: null, configured: true };
    return { ok: true, status: 200, statusText: "", json: async () => json, text: async () => JSON.stringify(json) };
  });
});
afterEach(() => vi.unstubAllGlobals());

function Where() {
  const loc = useLocation();
  return <output data-testid="where">{`${loc.pathname}${loc.search}`}</output>;
}

function mount(path: string) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <I18nProvider>
        <MemoryRouter initialEntries={[path]}>
          <Where />
          <Routes>
            <Route path="/wiki/*" element={<WikiPage />} />
          </Routes>
        </MemoryRouter>
      </I18nProvider>
    </QueryClientProvider>,
  );
}

const graphCalls = () => calls.filter((c) => c.includes("/api/wiki/graph"));
const where = () => screen.getByTestId("where").textContent;
/** a click on a dot: the view works with pointer events so that dragging and clicking can be told apart */
function clickNode(label: string) {
  const dot = screen.getByRole("link", { name: label });
  fireEvent.pointerDown(dot, { clientX: 10, clientY: 10 });
  fireEvent.pointerUp(dot, { clientX: 10, clientY: 10 });
}

describe("wiki graph view", () => {
  it("draws the linked pages, lists the isolated ones apart and opens a page on click", async () => {
    mount("/wiki?view=graph");
    expect(await screen.findByRole("img", { name: en["web.wiki.graph.aria"]! })).toBeTruthy();
    const svg = screen.getByRole("img", { name: en["web.wiki.graph.aria"]! });
    expect(svg.querySelectorAll("[data-node]")).toHaveLength(2);
    expect(svg.querySelectorAll("line.g-edge-link")).toHaveLength(1);
    expect(svg.textContent).not.toContain("Lonely page");                         // not on the canvas ...
    expect(screen.getByText(fill("web.wiki.graph.isolated", { n: 1 }))).toBeTruthy();   // ... but listed below it
    expect(screen.getByRole("link", { name: "Lonely page" }).getAttribute("href")).toBe("/wiki/vault/Lonely?view=graph");
    expect(screen.getByText(fill("web.wiki.graph.stats", { pages: 3, links: 1 }))).toBeTruthy();
    expect(calls.some((c) => c.includes("/api/wiki/pages?"))).toBe(false);         // the list is not fetched in this view

    clickNode("Alpha");
    expect(where()).toBe("/wiki/vault/A?view=graph");
    expect(await screen.findByRole("heading", { name: "Alpha", level: 2 })).toBeTruthy();   // the page opens beside the graph
    expect(calls.some((c) => c.includes("/api/wiki/pages/vault/A"))).toBe(true);
  });

  it("shows the title and summary of the dot under the pointer, and highlights its neighbours", async () => {
    mount("/wiki?view=graph");
    const alpha = await screen.findByRole("link", { name: "Alpha" });
    fireEvent.pointerEnter(alpha);
    expect(await screen.findByText("about Alpha")).toBeTruthy();
    expect(screen.getByText(fill("web.wiki.graph.degree", { in: 1, out: 1 }))).toBeTruthy();
    const svg = screen.getByRole("img", { name: en["web.wiki.graph.aria"]! });
    expect(svg.querySelector("line.hl")).toBeTruthy();
    fireEvent.pointerEnter(screen.getByRole("link", { name: "Beta" }));
    expect(await screen.findByText("about Beta")).toBeTruthy();
  });

  it("asks for the neighbourhood of the open page, the extra layers and a tag filter", async () => {
    mount("/wiki/vault/A?view=graph");
    await screen.findByRole("img", { name: en["web.wiki.graph.aria"]! });
    expect(graphCalls()[0]).not.toContain("focus=");
    fireEvent.change(screen.getByLabelText(en["web.wiki.graph.scope"]!), { target: { value: "2" } });
    await waitFor(() => expect(graphCalls().some((c) => c.includes("focus=vault%2FA") && c.includes("depth=2"))).toBe(true));
    fireEvent.click(screen.getByLabelText(en["web.wiki.graph.layer_models"]!));
    await waitFor(() => expect(graphCalls().some((c) => c.includes("include_models=true"))).toBe(true));
    fireEvent.click(screen.getByLabelText(en["web.wiki.graph.layer_tags"]!));
    await waitFor(() => expect(graphCalls().some((c) => c.includes("include_tags=true") && c.includes("include_models=true"))).toBe(true));
    expect(where()).toContain("layers=");
    fireEvent.click(screen.getByRole("button", { name: "AI" }));
    await waitFor(() => expect(graphCalls().some((c) => c.includes("tag=AI"))).toBe(true));
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "ltx" } });
    await waitFor(() => expect(graphCalls().some((c) => c.includes("q=ltx"))).toBe(true));
    fireEvent.change(screen.getByLabelText(en["web.wiki.source"]!), { target: { value: "docs" } });
    await waitFor(() => expect(graphCalls().some((c) => c.includes("source=docs"))).toBe(true));
  });

  it("tags filter by themselves and models show the notes that mention them", async () => {
    graphs = () =>
      graphOf(
        [gnode("vault/A", "Alpha"), gnode("tag:AI", "AI", { kind: "tag", slug: "", pages: 2, source: "", tags: [] }),
          gnode("model:ltx-2.5", "ltx-2.5", { kind: "model", slug: "", pages: 1, refs: ["ltx-2.5-av", "ltx-2.5-video"], source: "", tags: [] })],
        [gedge("vault/A", "tag:AI", "tag"), gedge("vault/A", "model:ltx-2.5", "mention", 3)],
      );
    mount("/wiki?view=graph&layers=tags,models");
    await screen.findByRole("link", { name: "ltx-2.5" });
    expect(graphCalls()[0]).toContain("include_tags=true");
    fireEvent.pointerEnter(screen.getByRole("link", { name: "ltx-2.5" }));
    expect(await screen.findByText(fill("web.wiki.graph.model_refs", { refs: "ltx-2.5-av, ltx-2.5-video" }))).toBeTruthy();
    clickNode("AI");
    expect(where()).toContain("tag=AI");
    clickNode("ltx-2.5");
    expect(where()).toContain("model=ltx-2.5");
    await waitFor(() => expect(graphCalls().some((c) => c.includes("focus=model%3Altx-2.5"))).toBe(true));
    expect(await screen.findByText(fill("web.wiki.graph.model_title", { model: "ltx-2.5" }))).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: en["web.wiki.graph.model_clear"]! }));
    expect(where()).not.toContain("model=");
  });

  it("says when the picture was cut to stay fast and can ask for more", async () => {
    graphs = (url) => graphOf([gnode("vault/A", "Alpha"), gnode("vault/B", "Beta")], [gedge("vault/A", "vault/B")], { truncated: true, matched: 800, limit: url.includes("limit=1000") ? 1000 : 300 });
    mount("/wiki?view=graph");
    expect(await screen.findByText(fill("web.wiki.graph.truncated", { shown: 2, matched: 800 }))).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: fill("web.wiki.graph.more", { n: 1000 }) }));
    await waitFor(() => expect(graphCalls().some((c) => c.includes("limit=1000"))).toBe(true));
  });

  it("tells about locked notes, an empty result, and fades pages that only give context to a search", async () => {
    graphs = () => graphOf([], [], { hidden: 2 });
    mount("/wiki?view=graph&q=zzz");
    expect(await screen.findByText(en["web.wiki.graph.empty"]!)).toBeTruthy();
    expect(screen.queryByRole("img", { name: en["web.wiki.graph.aria"]! })).toBeNull();
  });

  it("fades the context pages of a search", async () => {
    graphs = () => graphOf([gnode("vault/A", "Alpha"), gnode("vault/B", "Beta", { match: false })], [gedge("vault/A", "vault/B")], { hidden: 1 });
    mount("/wiki?view=graph&q=alpha");
    await screen.findByRole("link", { name: "Beta" });
    expect(screen.getByRole("link", { name: "Beta" }).getAttribute("class")).toContain("dim");
    expect(screen.getByRole("link", { name: "Alpha" }).getAttribute("class")).not.toContain("dim");
    expect(screen.getByText(en["web.wiki.graph.fade_hint"]!)).toBeTruthy();
    expect(screen.getByText(fill("web.wiki.hidden", { n: 1 }))).toBeTruthy();
  });

  it("switches between the list and the graph through the url, and shows a small graph in the page reader", async () => {
    mount("/wiki/vault/A");
    expect(await screen.findByRole("heading", { name: "Alpha", level: 2 })).toBeTruthy();
    expect(await screen.findByRole("region", { name: en["web.wiki.graph.mini_title"]! })).toBeTruthy();
    expect(graphCalls().some((c) => c.includes("focus=vault%2FA") && c.includes("include_models=true"))).toBe(true);
    const open = screen.getByRole("link", { name: en["web.wiki.graph.mini_open"]! });
    expect(open.getAttribute("href")).toBe("/wiki/vault/A?view=graph&scope=1");
    fireEvent.click(screen.getByRole("button", { name: en["web.wiki.view.graph"]! }));
    expect(where()).toContain("view=graph");
    await screen.findByRole("img", { name: en["web.wiki.graph.aria"]! });
    fireEvent.click(screen.getByRole("button", { name: en["web.wiki.view.list"]! }));
    expect(where()).not.toContain("view=graph");
  });

  it("does not draw a mini graph for a page with nothing around it", async () => {
    graphs = () => graphOf([gnode("vault/A", "Alpha", { isolated: true })], []);
    mount("/wiki/vault/A");
    await screen.findByRole("heading", { name: "Alpha", level: 2 });
    await waitFor(() => expect(graphCalls().length).toBeGreaterThan(0));
    expect(screen.queryByRole("region", { name: en["web.wiki.graph.mini_title"]! })).toBeNull();
  });
});
