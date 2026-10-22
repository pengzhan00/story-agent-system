import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../src/i18n";
import { PipelinePanel } from "../src/components/PipelinePanel";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;
const cand = (ref: string, paired = true, rating = "general") => ({ ref, family: "f", rating, paired, remote: false, commercial_ok: true, license_tag: "" });
const view = (over: boolean) => ({
  plan: "p", overridden: over,
  notes: over ? [{ code: "auto_paired", kind: "image.edit", model: "alt-edit" }] : [],
  rows: [
    { kind: "image.generate", default: "flux", chosen: over ? "alt" : "flux", overridden: over, auto: false, candidates: [cand("flux"), cand("alt", true, "restricted")] },
    { kind: "image.edit", default: "flux-edit", chosen: over ? "alt-edit" : "flux-edit", overridden: false, auto: over, candidates: [cand("flux-edit", !over), cand("alt-edit", over)] },
  ],
});
let state = view(false);
let calls: { url: string; body: string }[] = [];

beforeEach(() => {
  state = view(false);
  calls = [];
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string, init?: { method?: string; body?: string }) => {
    calls.push({ url: `${init?.method ?? "GET"} ${url}`, body: String(init?.body ?? "") });
    let json: unknown = {};
    if (url.includes("/api/i18n")) json = en;
    else if (url.includes("/pipeline")) {
      if (init?.method === "PUT") state = view(Object.keys(JSON.parse(init.body ?? "{}").models).length > 0);
      if (init?.method === "DELETE") state = view(false);
      json = state;
    }
    return { ok: true, status: init?.method === "DELETE" ? 204 : 200, statusText: "", json: async () => json, text: async () => JSON.stringify(json) };
  });
});
afterEach(() => vi.unstubAllGlobals());

describe("PipelinePanel", () => {
  it("changes one model, shows the pairing note, and goes back to the recommendation in one click", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nProvider>
          <PipelinePanel projectId={3} />
        </I18nProvider>
      </QueryClientProvider>,
    );
    const select = (await screen.findByLabelText(en["media.kind.image.generate"]!)) as HTMLSelectElement;
    expect(select.value).toBe("flux");
    expect((screen.getByRole("button", { name: en["web.pipeline.reset_all"]! }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(select, { target: { value: "alt" } });
    await waitFor(() => expect(calls.some((c) => c.url === "PUT /api/projects/3/pipeline")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url.startsWith("PUT"))?.body ?? "{}")).toEqual({ models: { "image.generate": "alt" } });
    expect(await screen.findByText(en["web.pipeline.note.auto_paired"]!.replace("{kind}", en["media.kind.image.edit"]!).replace("{model}", "alt-edit"))).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: en["web.pipeline.reset_all"]! }));
    await waitFor(() => expect(calls.some((c) => c.url === "DELETE /api/projects/3/pipeline")).toBe(true));
    await waitFor(() => expect((screen.getByLabelText(en["media.kind.image.generate"]!) as HTMLSelectElement).value).toBe("flux"));
  });
});
