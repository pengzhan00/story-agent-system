import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../src/i18n";
import { JobsPanel } from "../src/pages/QueuePage";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;
const job = (id: string, over: Record<string, unknown>) => ({
  id, project_id: 3, shot_id: 1, spec_id: null, spec_version: null, kind: "video.generate", provider: "p", model_ref: "ltx", tier: "draft", slot: "main", status: "transport_error",
  priority: 0, inputs: {}, params: {}, seed: 1, heavy: true, est_memory_gib: 0, est_seconds: 0, license_tag: "", commercial_ok: true, attempt: 1, max_attempts: 1, parent_job_id: null,
  progress: 0, progress_msg: "", error_code: "service_unreachable", error_message: null, error_params: {}, cancel_requested: false, created_at: "x", started_at: null, finished_at: null,
  actual_seconds: 0, retried_by: null, retried_status: null, retries: 0, ...over,
});
let calls: string[] = [];

beforeEach(() => {
  calls = [];
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string, init?: { method?: string }) => {
    calls.push(`${init?.method ?? "GET"} ${url}`);
    let json: unknown = {};
    if (url.includes("/api/i18n")) json = en;
    else if (url.includes("/api/queue")) json = { running: 0, queued: 1, running_message: "", queued_reasons: {} };
    else if (url.includes("/api/jobs"))
      json = [
        job("aaaaaaaa11111111", { retried_by: "cccccccc33333333", retried_status: "queued", retries: 1 }),
        job("bbbbbbbb22222222", {}),
        job("cccccccc33333333", { status: "queued", error_code: null, parent_job_id: "aaaaaaaa11111111" }),
        job("dddddddd44444444", { status: "queued", error_code: null, kind: "audio.tts", priority: 5 }),
        job("eeeeeeee55555555", { status: "queued", error_code: null, kind: "audio.tts", priority: 5 }),
      ];
    return { ok: true, status: 200, statusText: "", json: async () => json, text: async () => JSON.stringify(json) };
  });
});
afterEach(() => vi.unstubAllGlobals());

describe("job list: ids and retries", () => {
  it("shows each job's number and marks the ones already retried, so only the rest can be retried", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nProvider>
          <JobsPanel />
        </I18nProvider>
      </QueryClientProvider>,
    );
    expect(await screen.findAllByText("#aaaaaaaa")).not.toHaveLength(0);
    expect(screen.getAllByText("#bbbbbbbb")).toHaveLength(1);
    expect(screen.getAllByText("#cccccccc").length).toBeGreaterThan(0);          // the retry, and the "retried -> " pointer
    expect(screen.getByText(en["web.jobs.retried"]!)).toBeTruthy();
    expect(screen.getByText(new RegExp(en["web.jobs.retry_of"]!))).toBeTruthy();
    const buttons = screen.getAllByRole("button", { name: en["web.jobs.retry"]! });
    expect(buttons).toHaveLength(1);                                              // only #bbbbbbbb was not retried yet
    fireEvent.click(buttons[0]!);
    await waitFor(() => expect(calls).toContain("POST /api/jobs/bbbbbbbb22222222/retry"));
  });

  it("moves queued jobs to the front: one, or every queued job of the same kind", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nProvider>
          <JobsPanel />
        </I18nProvider>
      </QueryClientProvider>,
    );
    await screen.findAllByText("#dddddddd");
    expect(screen.getAllByRole("button", { name: en["web.jobs.top"]! })).toHaveLength(3);
    expect(screen.getAllByRole("button", { name: en["web.jobs.top_kind"]! })).toHaveLength(2);   // only the two audio jobs have a sibling
    fireEvent.click(screen.getAllByRole("button", { name: en["web.jobs.top_kind"]! })[0]!);
    await waitFor(() => expect(calls).toContain("POST /api/jobs/priority"));
  });
});
