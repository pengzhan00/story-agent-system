import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../src/i18n";
import { Markdown, headingsOf, parseBlocks } from "../src/lib/markdown";
import { WikiPage } from "../src/pages/WikiPage";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;

const brief = (slug: string, title: string, over: Record<string, unknown> = {}) => ({
  slug, title, source: "vault", path: `${title}.md`, rating: "general", tags: ["AI"], summary: `about ${title}`, snippet: "", updated: "2026-10-20T00:00:00+00:00", ...over,
});
const listing = (pages: unknown[], over: Record<string, unknown> = {}) => ({
  pages, total: pages.length, hidden: 0, sources: [{ name: "vault", count: pages.length }], tags: [{ name: "AI", count: pages.length }], synced_at: "2026-10-23T08:00:00+00:00", configured: true, ...over,
});

const graphOf = (nodes: unknown[], edges: unknown[] = [], over: Record<string, unknown> = {}) => ({
  nodes, edges, total: nodes.length, hidden: 0, matched: nodes.length, truncated: false, limit: 300, focus: "", depth: 1,
  sources: [{ name: "vault", count: nodes.length }], tags: [{ name: "AI", count: nodes.length }], ...over,
});

let calls: string[] = [];
function stubApi(extra: Record<string, unknown> = {}) {
  vi.stubGlobal("fetch", async (url: string, init?: { method?: string }) => {
    calls.push(`${init?.method ?? "GET"} ${url}`);
    let json: unknown = {};
    if (url.includes("/api/i18n")) json = en;
    else if (url.includes("/api/wiki/graph")) json = extra.graph ?? graphOf([]);
    else if (url.includes("/api/wiki/sync")) json = { added: 2, updated: 0, unchanged: 0, removed: 0, sources: { vault: 2 }, warnings: [{ code: "no_include", source: "vault", detail: "" }] };
    else if (url.includes("/api/wiki/pages/")) {
      json = {
        page: brief("vault/Matrix", "Matrix"), meta: {}, links: [], backlinks: [brief("vault/Other", "Other")],
        body: "# Matrix\n\n## Speeds\n\nSee [Other](wiki:vault/Other) and [outside](https://example.com) and [bad](javascript:alert(1)).\n\n## More\n\n- one\n- two\n",
      };
    } else if (url.includes("/api/wiki/pages")) json = extra.list ?? listing([brief("vault/Matrix", "Matrix"), brief("vault/Other", "Other", { rating: "restricted" })]);
    return { ok: true, status: 200, statusText: "", json: async () => json, text: async () => JSON.stringify(json) };
  });
}

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

beforeEach(() => {
  calls = [];
  localStorage.setItem("storyagent.locale", "en");
});
afterEach(() => vi.unstubAllGlobals());

describe("markdown reader", () => {
  it("parses headings, lists, tables, quotes and code, and builds the table of contents", () => {
    const blocks = parseBlocks("# T\n\ntext\n\n## A\n\n- x\n  - y\n- z\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n> q\n\n```py\ncode **not bold**\n```\n\n## A\n");
    expect(blocks.map((b) => b.kind)).toEqual(["heading", "paragraph", "heading", "list", "table", "quote", "code", "heading"]);
    expect(headingsOf(blocks).map((h) => h.id)).toEqual(["t", "a", "a-2"]);
    const list = blocks[3];
    expect(list?.kind === "list" && list.items.length).toBe(2);
  });

  it("never turns text into markup: scripts, javascript: links and foreign pictures stay text", () => {
    const { container } = render(
      <MemoryRouter>
        <Markdown source={"<script>alert(1)</script> [bad](javascript:alert(1)) ![x](file:///etc/passwd) **bold** `code`"} />
      </MemoryRouter>,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("strong")?.textContent).toBe("bold");
    expect(container.textContent).toContain("<script>alert(1)</script>");
  });
});

describe("wiki page", () => {
  it("lists the pages, opens one, links to other pages and shows what links here", async () => {
    stubApi();
    mount("/wiki/vault/Matrix");
    expect(await screen.findByRole("heading", { name: "Matrix", level: 2 })).toBeTruthy();
    expect(await screen.findByText("Speeds")).toBeTruthy();
    const inBody = (await screen.findAllByRole("link", { name: "Other" })).filter((a) => a.closest(".markdown"));
    expect(inBody).toHaveLength(1);
    expect(inBody[0]?.getAttribute("href")).toBe("/wiki/vault/Other");
    expect(screen.getByText(en["web.wiki.backlinks"]!)).toBeTruthy();
    expect(screen.getByText(en["web.wiki.restricted"]!)).toBeTruthy();                // the restricted note in the list
    expect(screen.queryByRole("link", { name: "bad" })).toBeNull();                    // javascript: is not a link
    expect(screen.getByRole("link", { name: "outside" }).getAttribute("rel")).toContain("noopener");
  });

  it("searches through the url query, tells about locked notes and syncs on request", async () => {
    stubApi({ list: listing([], { total: 0, hidden: 2, sources: [], tags: [], configured: false }) });
    mount("/wiki?q=ltx");
    await waitFor(() => expect(calls.some((c) => c.includes("/api/wiki/pages?q=ltx"))).toBe(true));
    expect(await screen.findByText(en["web.wiki.empty"]!)).toBeTruthy();
    expect(screen.getByText(en["web.wiki.not_configured"]!)).toBeTruthy();
    expect(screen.getByText(en["web.wiki.hidden"]!.replace("{n}", "2"))).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: en["web.wiki.sync"]! }));
    expect(await screen.findByText(en["web.wiki.sync_done"]!.replace("{added}", "2").replace("{updated}", "0").replace("{removed}", "0"))).toBeTruthy();
    expect(screen.getByText(en["web.wiki.warn.no_include"]!.replace("{source}", "vault").replace("{detail}", ""))).toBeTruthy();
    expect(calls).toContain("POST /api/wiki/sync");
  });
});
