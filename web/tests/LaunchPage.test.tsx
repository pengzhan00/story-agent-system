import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../src/i18n";
import { LaunchPage } from "../src/pages/LaunchPage";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;
const svc = (name: string, state: string, stoppable: boolean) => ({
  name, url: `http://x/${name}`, state, label: state, startable: true, stoppable, bulk: true, required: false, detail: "", hint: "", pid: null, elapsed_sec: null, timeout_sec: null, log: "",
});
let state = "running";
let calls: { url: string; body: string }[] = [];

beforeEach(() => {
  state = "running";
  calls = [];
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("confirm", () => true);
  vi.stubGlobal("fetch", async (url: string, init?: { method?: string; body?: string }) => {
    calls.push({ url: `${init?.method ?? "GET"} ${url}`, body: String(init?.body ?? "") });
    if (url.includes("/stop")) state = "stopped";
    let json: unknown = {};
    if (url.includes("/api/i18n")) json = en;
    else if (url.includes("/api/launch"))
      json = {
        ready: true, starting: false, startable: [], issues: [],
        memory: { total_gib: 128, free_gib: 24, swap_total_gib: 34, swap_used_gib: 33, swap_percent: 97 },
        services: [svc("acestep", state, true), svc("ollama", "running", false)],
      };
    return { ok: true, status: 200, statusText: "", json: async () => json, text: async () => JSON.stringify(json) };
  });
});
afterEach(() => vi.unstubAllGlobals());

describe("LaunchPage", () => {
  it("shows memory and a nearly full swap, and stops only the service that allows it", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nProvider>
          <MemoryRouter>
            <LaunchPage />
          </MemoryRouter>
        </I18nProvider>
      </QueryClientProvider>,
    );
    expect(await screen.findByText(en["web.launch.swap_warn"]!)).toBeTruthy();
    expect(screen.getAllByRole("button", { name: en["web.launch.stop"]! })).toHaveLength(1);       // ollama has no stop
    fireEvent.click(screen.getByRole("button", { name: en["web.launch.stop"]! }));
    await waitFor(() => expect(calls.some((c) => c.url === "POST /api/launch/acestep/stop")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url.endsWith("/stop"))?.body ?? "{}")).toEqual({ force: false });
    await waitFor(() => expect(screen.queryByRole("button", { name: en["web.launch.stop"]! })).toBeNull());
  });
});
