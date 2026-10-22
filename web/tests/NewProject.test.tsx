import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NewProject } from "../src/components/NewProject";
import { I18nProvider } from "../src/i18n";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;
const range = (min: number, def: number, max: number) => ({ min, default: def, max });
const meta = {
  locales: ["en"],
  default_work_kind: "drama_series",
  default_pipeline: "vertical_drama",
  work_kinds: [
    { name: "drama_series", label: "Short-drama series", note: "In seasons.", pipeline: "vertical_drama", seasons: true, episodes: range(2, 12, 200), episode_sec: range(30, 120, 600), ask: ["seasons", "episodes", "episode_sec"] },
    { name: "advert", label: "Advert", note: "One message.", pipeline: "advert", seasons: false, episodes: range(1, 1, 1), episode_sec: range(5, 30, 120), ask: ["episode_sec"] },
  ],
  pipelines: [
    { name: "vertical_drama", label: "Vertical drama", note: "9:16.", delivery_profile: "p", width: 720, height: 1280, tier: "preview", models: {}, requires: [] },
    { name: "advert", label: "Advert plan", note: "16:9.", delivery_profile: "q", width: 1920, height: 1080, tier: "final", models: {}, requires: [] },
  ],
};
const estimate = (pipeline: string) => ({
  brief: {}, pipeline, tier: "preview", episodes: 12, shots: 360,
  tasks: [{ kind: "video.generate", model_ref: "ltx-2.5-video", count: 36, each_sec: 240, known: false, total_sec: 8640 }],
  total_sec: 8640, hours: 2.4, video_budget_hours: null, over_budget: false,
});

let calls: { url: string; method: string; body: unknown }[] = [];
beforeEach(() => {
  calls = [];
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string, init?: { method?: string; body?: string }) => {
    const body = init?.body ? (JSON.parse(init.body) as { kind?: string; pipeline?: string }) : undefined;
    calls.push({ url, method: init?.method ?? "GET", body });
    const json = url.includes("/api/i18n") ? en : url.includes("/api/meta") ? meta
      : url.includes("/estimate") ? estimate(body?.pipeline || (body?.kind === "advert" ? "advert" : "vertical_drama"))
      : { id: 7, name: "X" };
    return { ok: true, status: 200, json: async () => json, text: async () => JSON.stringify(json) };
  });
});
afterEach(() => vi.unstubAllGlobals());

function view() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nProvider>
        <MemoryRouter>
          <NewProject />
        </MemoryRouter>
      </I18nProvider>
    </QueryClientProvider>,
  );
}

describe("NewProject", () => {
  it("offers the kinds and plans of the server, asks only what the kind asks, and shows the estimate", async () => {
    view();
    expect(await screen.findByText("Short-drama series")).toBeInTheDocument();
    expect(screen.getByLabelText(/Seasons/)).toBeInTheDocument();
    expect(await screen.findByText(/about 360 shots/, {}, { timeout: 3000 })).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(/Advert/));
    await waitFor(() => expect(screen.queryByLabelText(/Seasons/)).not.toBeInTheDocument());
    expect((screen.getByRole("combobox") as HTMLSelectElement).value).toBe("advert");     // the kind recommends its plan
  });

  it("creates the project with the kind, the plan only when changed, and the answers", async () => {
    view();
    await screen.findByText("Short-drama series");
    fireEvent.change(screen.getByLabelText(/^Name/), { target: { value: "Saga" } });
    fireEvent.change(screen.getByLabelText(/Episodes per season/), { target: { value: "8" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url === "/api/projects")).toBe(true));
    const sent = calls.find((c) => c.method === "POST" && c.url === "/api/projects")!.body as { kind: string; pipeline: string; brief: { episodes_per_season: number } };
    expect(sent.kind).toBe("drama_series");
    expect(sent.pipeline).toBe("");
    expect(sent.brief.episodes_per_season).toBe(8);
  });
});
