import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../src/i18n";
import { GrammarPanel } from "../src/pages/GrammarPanel";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;
const tr = (k: string): string => en[k] ?? k;
const violation = { rule: "speaker_in_frame", severity: "error", shot_id: 7, episode: 1, act: 1, scene: 2, shot: 3, params: { character: "Ann" }, fixable: true, fix: { type: "add_character", value: "Ann" } };
const note = { rule: "single_action", severity: "info", shot_id: 8, episode: 1, act: 1, scene: 2, shot: 4, params: { connectors: "then" }, fixable: false, fix: null };
let violations: unknown[] = [];
let posts: unknown[] = [];

beforeEach(() => {
  violations = [violation, note];
  posts = [];
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string, init?: { method?: string; body?: string }) => {
    let json: unknown = {};
    if (url.includes("/api/i18n")) json = en;
    else if (url.includes("/api/auth/mode")) json = { mode: "off", first_user: false, locales: ["en"] };
    else if (url.includes("/api/auth/me")) json = { mode: "off", user: null, can_write: true, can_create_project: true, is_admin: true };
    else if (url.includes("/episodes")) json = [{ id: 1, number: 1 }];
    else if (init?.method === "POST" && url.includes("/grammar/fix")) {
      posts.push(JSON.parse(init.body ?? "{}"));
      violations = [note];
      json = { fixed: [{ shot_id: 7, rule: "speaker_in_frame" }], left: [], report: { shots: 2, unclassified: 0, counts: { error: 0, warning: 0, info: 1 }, violations } };
    } else if (url.includes("/grammar")) json = { shots: 2, unclassified: 0, counts: { error: violations.length > 1 ? 1 : 0, warning: 0, info: 1 }, violations };
    return { ok: true, status: 200, statusText: "", json: async () => json, text: async () => JSON.stringify(json) };
  });
});
afterEach(() => vi.unstubAllGlobals());

describe("GrammarPanel", () => {
  it("lists the problems with their repair and repairs one shot", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nProvider>
          <GrammarPanel id={1} />
        </I18nProvider>
      </QueryClientProvider>,
    );
    await screen.findByText(/Ann speaks but is not in the frame/);
    expect(screen.getByText("1.1.2.3")).toBeTruthy();
    expect(screen.getByText(/put Ann in the shot/)).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: tr("web.grammar.fix") })[0]!);
    await waitFor(() => expect(posts).toEqual([{ episode: null, rules: ["speaker_in_frame"], shot_ids: [7] }]));
    await waitFor(() => expect(screen.queryByText(/Ann speaks but is not in the frame/)).toBeNull());
    expect(screen.getByText(/More than one action in one shot/)).toBeTruthy();
  });
});
