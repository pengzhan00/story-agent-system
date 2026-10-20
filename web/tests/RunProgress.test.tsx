import { render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeAll, describe, expect, it, vi } from "vitest";
import type { WorkflowRun } from "../src/api/types";
import { RunProgress, stageMessage } from "../src/components/RunProgress";
import { makeT } from "../src/i18n";
import { I18nProvider } from "../src/i18n";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "locales", "en.json"), "utf-8")) as Record<string, string>;

beforeAll(() => {
  vi.stubGlobal("fetch", async (url: string) => ({
    ok: true,
    json: async () => (url.includes("/api/meta") ? { locales: ["en"] } : en),
  }));
  localStorage.setItem("storyagent.locale", "en");
});

function run(over: Partial<WorkflowRun> = {}): WorkflowRun {
  return {
    id: "r1", project_id: 1, stages: ["analyze", "cast", "script"], params: {}, status: "running", cancel_requested: false,
    created_at: "", started_at: "", finished_at: null, error_code: null, error_message: null,
    progress: {
      stage: "cast", stage_index: 2, stage_count: 3, done: 1, total: 4, message: "Mara", percent: 37.5, elapsed_sec: 80, eta_sec: 110,
      stages: [
        { stage: "analyze", status: "completed", percent: 100, seconds: 8 },
        { stage: "cast", status: "running", percent: 25, seconds: null },
        { stage: "script", status: "pending", percent: 0, seconds: null },
      ],
    },
    ...over,
  };
}

const view = async (r: WorkflowRun) => {
  render(
    <I18nProvider>
      <RunProgress run={r} />
    </I18nProvider>,
  );
  await screen.findByText("37.5%", {}, { timeout: 2000 }).catch(() => undefined);
};

describe("RunProgress", () => {
  it("shows percent, step, item, message, elapsed, eta and one bar per stage", async () => {
    await view(run());
    expect(await screen.findByText("37.5%")).toBeInTheDocument();
    expect(screen.getByText(/Step 2 of 3/)).toBeInTheDocument();
    expect(screen.getByText(/1 of 4/)).toBeInTheDocument();
    expect(screen.getByText(/Mara/)).toBeInTheDocument();
    expect(screen.getByText(/Elapsed 1m20s/)).toBeInTheDocument();
    expect(screen.getByText(/About 1m50s left/)).toBeInTheDocument();
    expect(screen.getAllByRole("progressbar")).toHaveLength(4);
  });
  it("says it is estimating instead of showing a wrong ETA", async () => {
    const r = run();
    r.progress.eta_sec = null;
    await view(r);
    expect(await screen.findByText(/Estimating time left/)).toBeInTheDocument();
  });
  it("renders an indeterminate bar for a running stage with no count yet", async () => {
    const r = run();
    r.progress.stages[1]!.percent = null;
    await view(r);
    const bars = await screen.findAllByRole("progressbar");
    expect(bars.some((b) => b.classList.contains("bar-indeterminate"))).toBe(true);
  });
  it("explains a failure and what to do next", async () => {
    await view(run({ status: "failed", error_code: "llm.unreachable", error_message: "boom" }));
    expect(await screen.findByText(/language model service cannot be reached/)).toBeInTheDocument();
    expect(screen.getByText(/Start Ollama/)).toBeInTheDocument();
  });
});

describe("stageMessage", () => {
  const t = makeT(en);
  it("translates a leading i18n key and keeps the detail", () => {
    expect(stageMessage(t, "plain")).toBe("plain");
    expect(stageMessage(t, "spec.subject.shot.video|8")).toBe("Shot video 8");
    expect(stageMessage(t, "workflow.progress.assembling|shot 3/8")).toBe("Assembling the episode shot 3/8");
    expect(stageMessage(t, "nope.key|x")).toBe("nope.key|x");
  });
});
