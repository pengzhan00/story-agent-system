import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProducePlan } from "../src/components/ProducePlan";
import { I18nProvider } from "../src/i18n";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;

const task = {
  spec_id: "s1", subject_type: "character.portrait", subject_id: "11", label: "Ann", kind: "image.generate", model_ref: "flux", phase: 1, state: "new", reason: "", est_seconds: 60, needs: [],
};
const spec = {
  id: "s1", project_id: 4, subject_type: "character.portrait", subject_id: "11", slot: "main", kind: "image.generate", model_ref: "flux", version: 1, parent_version: null,
  prompt: "a tired woman in a rain-lit doorway", negative_prompt: "", text_payload: "", params: {}, seed: null, refs: [], depends_on: [], locked: false, author: "agent", note: "", created_at: "2026-10-25T00:00:00Z",
};
let gates: { reason: string; name: string; detail: string }[] = [];

beforeEach(() => {
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string) => {
    const json: unknown = url.includes("/api/i18n")
      ? en
      : url.includes("/specs")
        ? [spec]
        : url.includes("/api/library")
          ? []
          : url.includes("/api/meta")
            ? { models: [], defaults: {} }
            : { tier: "draft", gates, tasks: [task], totals: { count: 1, est_seconds: 60, by_state: {} }, video: { shots: 0, chosen: 0, est_seconds: 0, budget_seconds: null } };
    return { ok: true, status: 200, statusText: "", json: async () => json, text: async () => JSON.stringify(json) };
  });
});
afterEach(() => vi.unstubAllGlobals());

function mount() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <I18nProvider>
        <MemoryRouter>
          <ProducePlan id={4} onClose={() => undefined} />
        </MemoryRouter>
      </I18nProvider>
    </QueryClientProvider>,
  );
}

describe("the production plan", () => {
  it("says what would stop every job and does not offer to start until that is fixed", async () => {
    gates = [{ reason: "adult_not_confirmed", name: "Ann", detail: "" }];
    mount();
    expect(await screen.findByText(/Ann.*not confirmed as an adult/)).toBeTruthy();
    expect((screen.getByRole("button", { name: en["web.plan.confirm"]! }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("starts as usual when nothing stops it", async () => {
    gates = [];
    mount();
    await screen.findByText("Ann");
    await vi.waitFor(() => expect((screen.getByRole("button", { name: en["web.plan.confirm"]! }) as HTMLButtonElement).disabled).toBe(false));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows each task's prompt and opens it for editing and confirming", async () => {
    gates = [];
    mount();
    expect(await screen.findByText(/a tired woman in a rain-lit doorway/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: en["web.plan.details"]! }));
    expect(await screen.findByDisplayValue("a tired woman in a rain-lit doorway")).toBeTruthy();
    expect(screen.getByRole("button", { name: en["web.plan.make_only"]! })).toBeTruthy();
    expect(screen.getByRole("button", { name: en["web.plan.confirmed"]! })).toBeTruthy();   // new tasks start selected
  });
});
