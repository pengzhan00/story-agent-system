import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UpdatePanel } from "../src/components/UpdatePanel";
import { I18nProvider } from "../src/i18n";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;
const commit = (short: string, subject: string) => ({ sha: short.repeat(10), short, subject });

const base = () => ({
  enabled: true, reason: "", reason_label: "", boot_id: "boot-1", started_at: 1, now: 1000,
  current: commit("aaa1111", "old version"), branch: "main", remote_ref: "origin/main", remote_known: true,
  clean: true, dirty: [] as string[], behind: [commit("ccc3333", "newest version"), commit("bbb2222", "older fix")],
  behind_count: 2, ahead_count: 0, checked_at: 900, tools_missing: [] as string[], busy: false, can_update: true,
  run: null as unknown, log_tail: [] as string[],
  work: { jobs: [] as { id: string; kind: string; model: string; heavy: boolean; project_id: number | null; progress: number; started_at: string | null }[], runs: [], queued: 0, heavy: 0 },
  restart: { supervised: true, phase: "none", phase_label: "", mode: "", requested_at: null as number | null, running: 0 },
});

let state = base();
let calls: { url: string; body: string }[] = [];
let down = false;

function mount() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <I18nProvider>
        <UpdatePanel />
      </I18nProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  state = base();
  calls = [];
  down = false;
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("confirm", () => true);
  vi.stubGlobal("fetch", async (url: string, init?: { method?: string; body?: string }) => {
    calls.push({ url: `${init?.method ?? "GET"} ${url}`, body: String(init?.body ?? "") });
    if (down && !url.includes("/api/i18n")) throw new TypeError("Failed to fetch");
    let json: unknown = {};
    if (url.includes("/api/i18n")) json = en;
    else if (url === "/api/update/run") {
      state = { ...state, busy: true };
      json = state;
    } else if (url.startsWith("/api/update")) json = state;
    return { ok: true, status: 200, statusText: "", json: async () => json, text: async () => JSON.stringify(json) };
  });
});
afterEach(() => vi.unstubAllGlobals());

describe("UpdatePanel", () => {
  it("shows the version and the waiting commits, and updates with a restart that waits for running work", async () => {
    state.work = { jobs: [{ id: "j1", kind: "video.generate", model: "ltx", heavy: true, project_id: 1, progress: 0.3, started_at: null }], runs: [], queued: 2, heavy: 1 };
    mount();
    expect(await screen.findByText("aaa1111 old version")).toBeTruthy();
    expect(screen.getByText("newest version")).toBeTruthy();
    expect(screen.getByText(en["web.update.work.wait_note"]!)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: en["web.update.run"]! }));
    await waitFor(() => expect(calls.some((c) => c.url === "POST /api/update/run")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url === "POST /api/update/run")!.body)).toEqual({ restart: true, interrupt: false });
  });

  it("asks twice and sends interrupt only when the person ticks it", async () => {
    state.work = { jobs: [{ id: "j1", kind: "video.generate", model: "ltx", heavy: true, project_id: 1, progress: 0, started_at: null }], runs: [], queued: 0, heavy: 1 };
    const confirm = vi.fn(() => true);
    vi.stubGlobal("confirm", confirm);
    mount();
    fireEvent.click(await screen.findByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: en["web.update.run"]! }));
    await waitFor(() => expect(calls.some((c) => c.url === "POST /api/update/run")).toBe(true));
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(JSON.parse(calls.find((c) => c.url === "POST /api/update/run")!.body)).toEqual({ restart: true, interrupt: true });
  });

  it("without a supervisor it only updates and says the restart is manual", async () => {
    state.restart.supervised = false;
    mount();
    fireEvent.click(await screen.findByRole("button", { name: en["web.update.run_only"]! }));
    await waitFor(() => expect(calls.some((c) => c.url === "POST /api/update/run")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url === "POST /api/update/run")!.body)).toEqual({ restart: false, interrupt: false });
    expect(screen.getByText(en["web.update.unsupervised"]!)).toBeTruthy();
  });

  it("cannot update from a dirty work tree or a development checkout", async () => {
    state.clean = false;
    state.dirty = [" M storyagent/cli.py"];
    state.can_update = false;
    state.enabled = false;
    state.reason_label = "development work tree";
    mount();
    expect(await screen.findByText(en["web.update.dirty"]!, { exact: false })).toBeTruthy();
    expect(screen.getByText("Updating is not available here: development work tree")).toBeTruthy();
    expect((screen.getByRole("button", { name: en["web.update.run"]! }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows the draining phase, lets the person interrupt, then reconnects and reports the new version", async () => {
    state.restart = { supervised: true, phase: "draining", phase_label: "", mode: "wait", requested_at: 990, running: 1 };
    state.work = { jobs: [{ id: "j1", kind: "video.generate", model: "ltx", heavy: true, project_id: 1, progress: 0.5, started_at: null }], runs: [], queued: 0, heavy: 1 };
    mount();
    expect(await screen.findByText("Waiting for 1 running jobs to finish; no new work is taken meanwhile...")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: en["web.update.interrupt_now"]! }));
    await waitFor(() => expect(calls.some((c) => c.url === "POST /api/update/restart")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url === "POST /api/update/restart")!.body)).toEqual({ interrupt: true });
  });

  it("notices the service going away and coming back as a new process", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      state.restart = { supervised: true, phase: "restarting", phase_label: "", mode: "wait", requested_at: 990, running: 0 };
      mount();
      expect(await screen.findByText(en["web.update.phase.restarting"]!)).toBeTruthy();
      down = true;
      await vi.advanceTimersByTimeAsync(2000);
      expect(await screen.findByText(en["web.update.reconnecting"]!)).toBeTruthy();
      down = false;
      state = { ...base(), boot_id: "boot-2", current: commit("ccc3333", "newest version"), behind: [], behind_count: 0 };
      await vi.advanceTimersByTimeAsync(2000);
      expect(await screen.findByText("Updated and restarted; now running ccc3333 newest version.")).toBeTruthy();
      expect(screen.getByRole("button", { name: en["web.update.reload"]! })).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });
});
