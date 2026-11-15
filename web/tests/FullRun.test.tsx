import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FullRun, ProduceResult, StoryCheck } from "../src/components/FullRun";
import { I18nProvider } from "../src/i18n";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;
const say = (key: string, vars: Record<string, string | number> = {}) =>
  Object.entries(vars).reduce((text, [k, v]) => text.replace(`{${k}}`, String(v)), en[key]!);

const down = { code: "service_down", level: "block", subject: "comfy", detail: "krea-2" };
const voices = { code: "voices_auto", level: "warn", subject: "Lin Wan", detail: "3" };

let posted: { url: string; body: { stages?: string[]; params: Record<string, unknown> } }[] = [];
let got: string[] = [];
let preflight: unknown = { ready: true, stages: ["script", "produce", "compose"], findings: [voices] };
let refuse = false;

beforeEach(() => {
  posted = [];
  got = [];
  refuse = false;
  preflight = { ready: true, stages: ["script", "produce", "compose"], findings: [voices] };
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string, init?: { method?: string; body?: string }) => {
    let json: unknown = {};
    let status = 200;
    if (url.includes("/api/i18n")) json = en;
    else if (url.includes("/preflight")) {
      got.push(url);
      json = preflight;
    } else if (init?.method === "POST") {
      posted.push({ url, body: JSON.parse(init.body ?? "{}") });
      if (refuse) {
        status = 409;
        json = { error: { code: "not_ready", message: en["api.error.not_ready"], remediation: en["api.error.not_ready.remediation"], detail: [down] } };
      }
    }
    return { ok: status < 400, status, statusText: "", json: async () => json, text: async () => JSON.stringify(json) };
  });
});
afterEach(() => vi.unstubAllGlobals());

function mount(episodes = "") {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
      <I18nProvider>
        <FullRun id={4} episode={2} episodes={episodes} disabled={false} />
      </I18nProvider>
    </QueryClientProvider>,
  );
}

describe("one click to the film", () => {
  it("starts a full run of the chosen episode", async () => {
    mount();
    fireEvent.click(await screen.findByText(en["web.workflow.make_everything"]!));
    await vi.waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]!.url).toContain("/api/projects/4/workflow/runs");
    expect(posted[0]!.body).toEqual({ params: { full: true, episode: 2 } });
  });

  it("runs the episodes of the batch field when one is given", async () => {
    mount("1-3");
    fireEvent.click(await screen.findByText(en["web.workflow.make_everything"]!));
    await vi.waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]!.body).toEqual({ params: { full: true, episodes: "1-3" } });
  });

  it("shows what a refused run is missing, in words", async () => {
    refuse = true;
    mount();
    fireEvent.click(await screen.findByText(en["web.workflow.make_everything"]!));
    expect(await screen.findByText(en["api.error.not_ready"]!)).toBeTruthy();
    expect(await screen.findByText(say("preflight.service_down", { subject: "comfy", detail: "krea-2" }))).toBeTruthy();
  });

  it("checks readiness without starting anything", async () => {
    mount();
    fireEvent.click(await screen.findByText(en["web.preflight.check"]!));
    const stages = ["script", "produce", "compose"].map((s) => en[`workflow.stage.${s}`]).join(" → ");
    expect(await screen.findByText(say("web.preflight.ready", { stages }))).toBeTruthy();
    expect(screen.getByText(say("preflight.voices_auto", { subject: "Lin Wan", detail: "3" }))).toBeTruthy();
    expect(got[0]).toContain("/api/projects/4/preflight?episode=2");
    expect(posted).toHaveLength(0);
  });

  it("lists what blocks the run when it is not ready", async () => {
    preflight = { ready: false, stages: ["produce", "compose"], findings: [down, voices] };
    mount();
    fireEvent.click(await screen.findByText(en["web.preflight.check"]!));
    expect(await screen.findByText(en["web.preflight.blocked"]!)).toBeTruthy();
    expect(screen.getByText(say("preflight.service_down", { subject: "comfy", detail: "krea-2" }))).toBeTruthy();
  });
});

describe("what the checks and the media stage left behind", () => {
  const wrap = (node: React.ReactNode) =>
    render(
      <QueryClientProvider client={new QueryClient()}>
        <I18nProvider>{node}</I18nProvider>
      </QueryClientProvider>,
    );

  it("shows the story check: score, rewrites and what is still open", async () => {
    const summary = {
      story_check: {
        outline: { score: 8, rounds: 1, rewritten: [], open: [], open_high: 0 },
        script: { score: 6.5, rounds: 1, rewritten: ["1.2"], open_high: 1, open: [{ severity: "high", where: "2.1", problem: "she names the sender too early", fix: "she only asks" }] },
      },
    };
    wrap(<StoryCheck summary={summary} />);
    expect(await screen.findByText(en["web.storycheck.title"]!)).toBeTruthy();
    expect(screen.getByText(say("web.storycheck.line", { part: en["web.storycheck.outline"]!, score: "8.0", rounds: 1 }))).toBeTruthy();
    expect(screen.getByText(en["web.storycheck.clean"]!)).toBeTruthy();
    expect(screen.getByText(say("web.storycheck.rewritten", { scenes: "1.2" }))).toBeTruthy();
    expect(screen.getByText(say("web.storycheck.open", { n: 1, high: 1 }))).toBeTruthy();
    expect(screen.getByText(/she names the sender too early/)).toBeTruthy();
  });

  it("shows nothing for a script written before the checks existed", () => {
    const { container } = wrap(<StoryCheck summary={{ shots: 12 }} />);
    expect(container.textContent).toBe("");
  });

  it("lists the assets that failed and the voices that were given", async () => {
    const summary = { made: 30, reused: 2, failed: 1, voices_given: ["Lin Wan"], voices_missing: [], failures: [{ subject: "shot.video", id: "17", code: "timeout", message: "took too long" }] };
    wrap(<ProduceResult summary={summary} />);
    expect(await screen.findByText(say("web.produce.result", { made: 30, reused: 2, failed: 1 }))).toBeTruthy();
    expect(screen.getByText(say("web.produce.voices_given", { names: "Lin Wan" }))).toBeTruthy();
    expect(screen.getByText(en["web.produce.failures"]!)).toBeTruthy();
    expect(screen.getByText(/took too long/)).toBeTruthy();
  });
});
