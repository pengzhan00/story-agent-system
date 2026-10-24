import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../src/i18n";
import { SetupPage } from "../src/pages/SetupPage";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;

const step = (over: Record<string, unknown>) => ({
  id: "x.1", component: "x", kind: "command", shell: "", note_key: "", source: "", file: "", size_gib: null,
  needs_service: "", runnable: true, done: false, error: "", ...over,
});
const comp = (name: string, over: Record<string, unknown>) => ({
  name, capability: "base", need: "required", state: "missing", label_key: `setup.c.${name}`, note_key: `setup.c.${name}.note`,
  size_gib: 1, measured: true, license: "MIT", url: "", checks: [{ label: name, state: "missing", detail: "" }], after: [], blocked_by: [],
  steps: [], ...over,
});
const flux = step({ id: "flux_klein_base_4b.2", component: "flux_klein_base_4b", kind: "download", shell: "hf download black-forest-labs/FLUX.2-klein-base-4B --local-dir /m", source: "huggingface.co/black-forest-labs/FLUX.2-klein-base-4B", file: "FLUX.2-klein-base-4B", size_gib: 22 });
const STATE = {
  profile: "full", root: "", default_root: "~/storyagent-tools",
  profiles: [
    { name: "full", label_key: "setup.profile.full", note_key: "setup.profile.full.note", required: [], recommended: [] },
    { name: "story", label_key: "setup.profile.story", note_key: "setup.profile.story.note", required: [], recommended: [] },
  ],
  capabilities_all: ["base", "image"],
  machine: [{ key: "memory", state: "warn", params: { value: "16", min: "36", rec: "64" } }],
  capabilities: [
    { name: "base", need: "required", state: "partial", label_key: "setup.cap.base", note_key: "setup.cap.base.note", components: ["uv", "ffmpeg_full"] },
    { name: "image", need: "required", state: "missing", label_key: "setup.cap.image", note_key: "setup.cap.image.note", components: ["flux_klein_base_4b"] },
  ],
  components: [
    comp("uv", { state: "ok", checks: [{ label: "uv", state: "ok", detail: "/bin/uv" }] }),
    comp("ffmpeg_full", { steps: [step({ id: "ffmpeg_full.1", component: "ffmpeg_full", shell: "brew install ffmpeg-full" })] }),
    comp("flux_klein_base_4b", { capability: "image", size_gib: 22, steps: [flux], after: ["uv"] }),
  ],
  downloads: [flux], missing_gib: 24, download_gib: 22, ready: false, health_required: ["tool.ffmpeg"], next_step: "ffmpeg_full.1",
};
let calls: { url: string; body: string }[] = [];

beforeEach(() => {
  calls = [];
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("confirm", () => true);
  vi.stubGlobal("fetch", async (url: string, init?: { method?: string; body?: string }) => {
    calls.push({ url: `${init?.method ?? "GET"} ${url}`, body: String(init?.body ?? "") });
    let json: unknown = {};
    if (url.includes("/api/i18n")) json = en;
    else if (url.includes("/api/setup/config") && init?.method === "POST")
      json = { files: [], written: [{ path: "/cfg/services.toml", backup: "/cfg/services.toml.bak-setup-1" }] };
    else if (url.includes("/api/setup/config"))
      json = { files: [{ name: "services.toml", path: "/cfg/services.toml", exists: true, changed: true, changes: ["healthcheck.required = ['tool.ffmpeg']"], text: "" }] };
    else if (url.includes("/api/setup/verify")) json = { ok: false, entries: [{ name: "ollama", category: "service", state: "not_running", label: "Not running", detail: "x", ok: false, required: true }] };
    else if (url.includes("/api/setup")) json = STATE;
    return { ok: true, status: 200, statusText: "", json: async () => json, text: async () => JSON.stringify(json) };
  });
});
afterEach(() => vi.unstubAllGlobals());

const mount = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <I18nProvider>
        <MemoryRouter>
          <SetupPage />
        </MemoryRouter>
      </I18nProvider>
    </QueryClientProvider>,
  );

describe("SetupPage", () => {
  it("shows the machine, what is missing and the download list, and never runs a step itself", async () => {
    mount();
    expect(await screen.findByText(en["web.setup.not_ready"]!)).toBeTruthy();
    expect(screen.getByText(en["web.setup.downloads_total"]!.replace("{count}", "1").replace("{gib}", "22"))).toBeTruthy();
    expect(screen.getByText(en["setup.machine.memory.warn"]!.replace("{value}", "16").replace("{rec}", "64"))).toBeTruthy();
    expect(screen.getByText(en["setup.c.flux_klein_base_4b"]!)).toBeTruthy();
    expect(screen.getAllByText(/huggingface.co\/black-forest-labs/).length).toBeGreaterThan(0);     // source is in the list
    // opening the steps of a download shows command, source, file and size - there is no run button anywhere
    fireEvent.click(screen.getAllByRole("button", { name: /Steps \(1\)/ })[1]!);
    expect(await screen.findByText(/hf download black-forest-labs/)).toBeTruthy();
    expect(screen.getByText(/It runs only when you confirm it in a terminal/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^run$/i })).toBeNull();
    expect(calls.every((c) => c.url.startsWith("GET"))).toBe(true);
  });

  it("previews the configuration and writes it only on the button", async () => {
    mount();
    expect(await screen.findByText(/healthcheck.required/)).toBeTruthy();
    expect(calls.some((c) => c.url.startsWith("POST /api/setup/config"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: en["web.setup.write"]! }));
    await waitFor(() => expect(calls.some((c) => c.url === "POST /api/setup/config")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url === "POST /api/setup/config")!.body)).toMatchObject({ profile: "full", write: true });
    expect(await screen.findByText(/bak-setup-1/)).toBeTruthy();
  });

  it("runs the check on demand", async () => {
    mount();
    fireEvent.click(await screen.findByRole("button", { name: en["web.setup.verify_run"]! }));
    expect(await screen.findByText(en["web.setup.verify_bad"]!)).toBeTruthy();
    expect(screen.getByText("ollama")).toBeTruthy();
  });
});
