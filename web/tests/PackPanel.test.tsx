import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../src/i18n";
import { PackPanel } from "../src/pages/PackPanel";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;
const tr = (k: string): string => en[k] ?? k;
const report = (dry: boolean) => ({
  dry_run: dry,
  project_id: 1,
  created_project: false,
  story: { shots: { created: 3 } },
  specs: { created: 2 },
  assets: { imported: 4, duplicate: 1 },
  warnings: [{ code: "model_unknown", model: "x" }],
  unassigned: ["notes.docx"],
});
let calls: string[] = [];

beforeEach(() => {
  calls = [];
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string, init?: { method?: string }) => {
    calls.push(`${init?.method ?? "GET"} ${url}`);
    let json: unknown = {};
    if (url.includes("/api/i18n")) json = en;
    else if (url.includes("/api/auth/mode")) json = { mode: "off", first_user: false, locales: ["en"] };
    else if (url.includes("/api/auth/me")) json = { mode: "off", user: null, can_write: true, can_create_project: true, is_admin: true };
    else if (url.includes("/pack?")) json = { name: "demo.zip", bytes: 2097152, scope: "all", counts: {}, warnings: [] };
    else if (url.includes("import-pack")) json = report(url.includes("dry_run=true"));
    return { ok: true, status: 200, statusText: "", json: async () => json, text: async () => JSON.stringify(json) };
  });
});
afterEach(() => vi.unstubAllGlobals());

const mount = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <I18nProvider>
        <PackPanel id={1} />
      </I18nProvider>
    </QueryClientProvider>,
  );

describe("PackPanel", () => {
  it("makes a pack and offers the download", async () => {
    mount();
    fireEvent.click(await screen.findByRole("button", { name: tr("web.pack.make") }));
    const link = await screen.findByRole("link", { name: "Download demo.zip" });
    expect(link.getAttribute("href")).toBe("/api/packs/demo.zip");
    expect(calls.some((c) => c.includes("/projects/1/pack?scope=all&include_history=false"))).toBe(true);
  });

  it("previews before it imports and shows the counts", async () => {
    mount();
    const run = await screen.findByRole("button", { name: tr("web.pack.run") });
    expect((run as HTMLButtonElement).disabled).toBe(true);
    const input = document.querySelector("input[type=file]") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["zip"], "p.zip")] } });
    fireEvent.click(screen.getByRole("button", { name: tr("web.pack.preview") }));
    await screen.findByText(tr("web.pack.report_preview"));
    expect(screen.getByText("imported 4 · already there 1")).toBeTruthy();
    expect(screen.getByText(tr("pack.warn.model_unknown"))).toBeTruthy();
    await waitFor(() => expect((screen.getByRole("button", { name: tr("web.pack.run") }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: tr("web.pack.run") }));
    await waitFor(() => expect(calls.some((c) => c.includes("into=1") && c.includes("dry_run=false"))).toBe(true));
  });
});
