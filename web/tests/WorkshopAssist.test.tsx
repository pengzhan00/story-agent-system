import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WorkshopAssist } from "../src/components/WorkshopAssist";
import { I18nProvider } from "../src/i18n";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;
const calls: { method: string; url: string; body: Record<string, unknown> }[] = [];

const proposal = {
  inputs: [
    { name: "prompt", value: "calm lullaby, felt piano", current: "a song" },
    { name: "lyrics", value: "[verse] the sea sleeps", current: "" },
  ],
  params: [{ name: "steps", value: 12, current: 8 }],
  dropped: [{ name: "duration", part: "param", reason: "bad_value", detail: "above the maximum" }],
  notes: "Tags work best here [1].",
  sources: [{ n: 1, slug: "vault/Skills/ACE-Step-notes", title: "ACE-Step notes", source: "vault", rating: "general", heading: "", cited: true }],
  backend: "ollama",
  model: "m1",
  seconds: 3.2,
};

function mount(onApply: (i: Record<string, unknown>, p: Record<string, unknown>) => void) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nProvider>
        <MemoryRouter>
          <WorkshopAssist module="song" kind="audio.music" model="acestep-1.5-turbo" tier="final" values={{ prompt: "a song" }} params={{ steps: 8 }} onApply={onApply} />
        </MemoryRouter>
      </I18nProvider>
    </QueryClientProvider>,
  );
}

describe("WorkshopAssist", () => {
  beforeEach(() => {
    calls.length = 0;
    vi.stubGlobal("fetch", async (url: string, init?: { method?: string; body?: string }) => {
      if (url.includes("/api/i18n")) return { ok: true, status: 200, json: async () => en };
      const method = init?.method ?? "GET";
      if (url.includes("/api/workshop/assist")) {
        calls.push({ method, url, body: JSON.parse(init?.body ?? "{}") as Record<string, unknown> });
        if (method === "POST") return { ok: true, status: 200, json: async () => proposal };
        const requirements = method === "PUT" ? String((calls[calls.length - 1]?.body as { requirements?: string }).requirements) : "Always Mandarin";
        return { ok: true, status: 200, json: async () => ({ module: "song", requirements, updated_at: "", updated_by: "" }) };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("loads the module's standing requirements and saves a change", async () => {
    mount(vi.fn());
    const box = await screen.findByDisplayValue("Always Mandarin");
    fireEvent.change(box, { target: { value: "Always Mandarin, no guitar" } });
    fireEvent.click(screen.getByText(en["web.workshop.assist.save_requirements"] ?? ""));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.body.requirements === "Always Mandarin, no guitar")).toBe(true));
  });

  it("sends the form and the request, shows the proposal first and applies only what is ticked", async () => {
    const onApply = vi.fn();
    mount(onApply);
    fireEvent.change(await screen.findByPlaceholderText(en["web.workshop.assist.request_hint"] ?? ""), { target: { value: "a lullaby about the sea" } });
    fireEvent.click(screen.getByText(en["web.workshop.assist.run"] ?? ""));
    expect(await screen.findByText("calm lullaby, felt piano")).toBeTruthy();
    const post = calls.find((c) => c.method === "POST");
    expect(post?.body).toMatchObject({ module: "song", kind: "audio.music", model_ref: "acestep-1.5-turbo", request: "a lullaby about the sea", use_wiki: true, inputs: { prompt: "a song" }, params: { steps: 8 } });
    expect(onApply).not.toHaveBeenCalled();
    expect(screen.getByText("ACE-Step notes")).toBeTruthy();
    expect(screen.getByText(/above the maximum/)).toBeTruthy();
    fireEvent.click(screen.getAllByRole("checkbox")[2] as HTMLElement);       // untick the lyrics (0 = wiki, 1 = prompt, 2 = lyrics)
    fireEvent.click(screen.getByText(en["web.workshop.assist.apply"] ?? ""));
    expect(onApply).toHaveBeenCalledWith({ prompt: "calm lullaby, felt piano" }, { steps: 12 });
  });
});
