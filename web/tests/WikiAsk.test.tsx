import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../src/i18n";
import { WikiPage } from "../src/pages/WikiPage";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;

const source = (n: number, slug: string, title: string, cited: boolean, heading = "") => ({ n, slug, title, source: "docs", rating: "general", heading, snippet: `text of ${title}`, cited });
const answer = (over: Record<string, unknown> = {}) => ({
  found: true, grounded: true, answer: "Start it with the launch screen [1], then check the port [2].", reason: "", backend: "ollama", model: "m1", seconds: 3.2,
  sources: [source(1, "docs/tools/ACESTEP", "ACE-Step", true, "Start"), source(2, "docs/RUNBOOK", "Runbook", true), source(3, "docs/OTHER", "Other", false)], ...over,
});
const gnode = (id: string, label: string, over: Record<string, unknown> = {}) => ({
  id, kind: "page", label, slug: id, source: "docs", rating: "general", tags: ["AI"], summary: `about ${label}`, links_in: 1, links_out: 1, pages: 0, refs: [], model_kind: "", isolated: false, match: true, ...over,
});

let asked: unknown[] = [];
let reply: unknown = answer();

beforeEach(() => {
  asked = [];
  reply = answer();
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string, init?: { method?: string; body?: string }) => {
    let json: unknown = {};
    if (url.includes("/api/i18n")) json = en;
    else if (url.includes("/api/wiki/ask")) {
      asked.push(JSON.parse(init?.body ?? "{}"));
      json = reply;
    } else if (url.includes("/api/wiki/graph")) {
      json = {
        nodes: [gnode("model:lustify", "lustify", { kind: "model", slug: "", refs: ["lustify-krea2"], source: "" }), gnode("docs/A", "Alpha note"), gnode("docs/B", "Beta note", { summary: "" })],
        edges: [{ source: "docs/A", target: "model:lustify", kind: "mention", weight: 2 }],
        total: 2, hidden: 0, matched: 2, truncated: false, limit: 300, focus: "model:lustify", depth: 1, sources: [], tags: [],
      };
    } else if (url.includes("/api/wiki/pages")) json = { pages: [], total: 0, hidden: 0, sources: [], tags: [], synced_at: null, configured: true };
    return { ok: true, status: 200, statusText: "", json: async () => json, text: async () => JSON.stringify(json) };
  });
});
afterEach(() => vi.unstubAllGlobals());

function mount(path: string) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <I18nProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/wiki/*" element={<WikiPage />} />
          </Routes>
        </MemoryRouter>
      </I18nProvider>
    </QueryClientProvider>,
  );
}

async function ask(question: string) {
  fireEvent.change(await screen.findByPlaceholderText(en["web.wiki.ask.placeholder"]!), { target: { value: question } });
  fireEvent.click(screen.getByRole("button", { name: en["web.wiki.ask.submit"]! }));
}

describe("asking the knowledge base", () => {
  it("shows the answer with its marks as links to the pages, and the sources it used", async () => {
    mount("/wiki");
    await ask("How do I start ACE-Step?");
    const link = await screen.findByRole("link", { name: "1" });
    expect(link.getAttribute("href")).toBe("/wiki/docs/tools/ACESTEP");
    expect(screen.getByRole("link", { name: "2" }).getAttribute("href")).toBe("/wiki/docs/RUNBOOK");
    const sources = within(screen.getByRole("heading", { name: en["web.wiki.ask.sources"]! }).parentElement!);
    expect(sources.getByText("ACE-Step")).toBeTruthy();
    expect(sources.queryByText("Other")).toBeNull(); // read but not used: kept apart
    expect(screen.getByText("Also read, not used (1)")).toBeTruthy();
    expect(asked).toEqual([{ question: "How do I start ACE-Step?", language: "en" }]);
  });

  it("says so when nothing matches, and when the answer points at nothing", async () => {
    reply = answer({ found: false, grounded: false, answer: "", reason: "no_match", sources: [] });
    mount("/wiki");
    await ask("quantum chromodynamics");
    expect(await screen.findByText(en["web.wiki.ask.none"]!)).toBeTruthy();
    reply = answer({ grounded: false, answer: "It depends." });
    await ask("something else");
    expect(await screen.findByText(en["web.wiki.ask.ungrounded"]!)).toBeTruthy();
  });
});

describe("a model node", () => {
  it("lists the pages that mention it, each readable with one click", async () => {
    mount("/wiki?view=graph&model=lustify");
    const list = await screen.findByText("1 pages — click one to read it");
    const entry = within(list.parentElement!);
    expect(entry.getByRole("link", { name: "Alpha note" }).getAttribute("href")).toContain("/wiki/docs/A");
    expect(entry.getByText("about Alpha note")).toBeTruthy();
    expect(entry.queryByText("Beta note")).toBeNull(); // near it in the picture, but does not mention it
    await waitFor(() => expect(screen.getByText("Notes that mention lustify")).toBeTruthy());
  });
});
