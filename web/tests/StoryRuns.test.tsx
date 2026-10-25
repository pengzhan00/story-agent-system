import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StoryRuns } from "../src/components/StoryRuns";
import { I18nProvider } from "../src/i18n";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;

const run = (over: Record<string, unknown>, progress: Record<string, unknown> = {}) => ({
  id: "r1", project_id: 4, project_name: "beauty", stages: ["analyze", "cast", "script"], params: {}, status: "running", cancel_requested: false, error_code: null, error_message: null,
  created_at: "2026-10-25T02:25:00Z", started_at: "2026-10-25T02:25:01Z", finished_at: null,
  progress: { stage: "script", episode: null, stage_index: 3, stage_count: 3, done: 3, total: 9, message: "2.1", percent: 25.7, elapsed_sec: 108, eta_sec: 314, stages: [], ...progress }, ...over,
});

let rows: unknown[] = [];
let posted: string[] = [];

beforeEach(() => {
  rows = [];
  posted = [];
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string, init?: { method?: string }) => {
    let json: unknown = {};
    if (url.includes("/api/i18n")) json = en;
    else if (init?.method === "POST") {
      posted.push(url);
      json = run({ status: "cancelled" });
    } else if (url.includes("/api/workflow/runs")) json = rows;
    return { ok: true, status: 200, statusText: "", json: async () => json, text: async () => JSON.stringify(json) };
  });
});
afterEach(() => vi.unstubAllGlobals());

function mount() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <I18nProvider>
        <MemoryRouter>
          <StoryRuns />
        </MemoryRouter>
      </I18nProvider>
    </QueryClientProvider>,
  );
}

describe("story runs in the queue", () => {
  it("lists a project's running story steps with their progress and lets them be cancelled", async () => {
    rows = [run({}), run({ id: "r2", project_id: 3, project_name: "Advert", status: "completed" }, { percent: 100, elapsed_sec: 380, eta_sec: null })];
    mount();
    expect(await screen.findByRole("link", { name: "beauty" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "beauty" }).getAttribute("href")).toBe("/projects/4/workflow");
    expect(screen.getByRole("link", { name: "Advert" })).toBeTruthy();
    expect(screen.getAllByText(/Script · 3 of 9/).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: en["web.jobs.cancel"]! }).length).toBe(1); // only the running one
    fireEvent.click(screen.getByRole("button", { name: en["web.jobs.cancel"]! }));
    await vi.waitFor(() => expect(posted).toEqual(["/api/workflow/runs/r1/cancel"]));
  });

  it("says so when nothing has run", async () => {
    mount();
    expect(await screen.findByText(en["web.queue.story_empty"]!)).toBeTruthy();
  });
});
