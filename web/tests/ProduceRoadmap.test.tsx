import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProduceRoadmap } from "../src/components/ProduceRoadmap";
import { I18nProvider } from "../src/i18n";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;
const task = (phase: number, state: string, est = 60, subject = "character.portrait") => ({
  spec_id: `${phase}${state}${Math.random()}`, subject_type: subject, subject_id: "1", label: "x", kind: "image.generate", model_ref: "m", phase, state, reason: "", est_seconds: est, needs: [],
});

beforeEach(() => {
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string) => {
    let json: unknown = {};
    if (url.includes("/api/i18n")) json = en;
    else if (url.includes("/produce/plan"))
      json = {
        tier: "draft", totals: {}, video: {},
        tasks: [task(1, "done"), task(1, "new"), task(2, "new", 120, "shot.keyframe"), task(2, "new", 120, "shot.keyframe"), task(3, "new", 600, "shot.video"), task(3, "skip", 0, "shot.video"), task(3, "blocked", 0, "shot.video")],
      };
    return { ok: true, status: 200, statusText: "", json: async () => json, text: async () => JSON.stringify(json) };
  });
});
afterEach(() => vi.unstubAllGlobals());

describe("ProduceRoadmap", () => {
  it("lists every phase, the one running now and the ones still waiting, with what is left", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nProvider>
          <ProduceRoadmap id={3} tier="draft" />
        </I18nProvider>
      </QueryClientProvider>,
    );
    expect(await screen.findByText(en["web.plan.phase.1"]!)).toBeTruthy();
    expect(screen.getByText(en["web.plan.phase.2"]!)).toBeTruthy();
    expect(screen.getByText(en["web.plan.phase.3"]!)).toBeTruthy();
    expect(screen.getByText(en["web.roadmap.summary"]!.replace("{done}", "1").replace("{total}", "6").replace("{eta}", "15m00s"))).toBeTruthy();
    expect(screen.getAllByText(new RegExp(en["web.roadmap.waiting"]!)).length).toBe(2);
    expect(screen.getByText(new RegExp(en["web.roadmap.stills"]!.replace("{n}", "1").replace(/[()]/g, "\\$&")))).toBeTruthy();
  });
});
