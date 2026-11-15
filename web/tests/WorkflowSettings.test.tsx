import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { I18nProvider } from "../src/i18n";
import { WorkflowSpec } from "../src/pages/WorkflowPanel";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;
let settings: Record<string, unknown>;
let writes: Record<string, unknown>[];
beforeEach(() => {
  settings = { target_sec: 240 };
  writes = [];
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string, init?: { method?: string; body?: string }) => {
    let json: unknown = [];
    if (url.includes("/api/i18n")) json = en;
    else if (url.includes("/api/meta")) json = { delivery_profiles: [{ name: "open", label: "Open format", min_episode_sec: 0, max_episode_sec: 0, target_sec: null }] };
    else if (url.includes("/api/projects/7")) {
      if (init?.method === "PATCH") {
        const body = JSON.parse(init.body ?? "{}"); writes.push(body);
        settings = body.target_sec === null ? {} : { ...settings, ...body };
      }
      json = { id: 7, delivery_profile: "open", settings };
    }
    return { ok: true, status: 200, json: async () => json, text: async () => JSON.stringify(json) };
  });
});
afterEach(() => vi.unstubAllGlobals());
function mount() {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <I18nProvider><WorkflowSpec id={7} /></I18nProvider>
  </QueryClientProvider>);
}
it("clears the planning target for open-length content instead of imposing a preset", async () => {
  mount();
  const input = await screen.findByPlaceholderText(en["web.workflow.target_unlimited"]!);
  await waitFor(() => expect(input).toHaveValue(240));
  fireEvent.click(screen.getByRole("button", { name: en["web.workflow.target_unlimited"]! }));
  await waitFor(() => expect(writes).toEqual([{ target_sec: null }]));
  await waitFor(() => expect(input).toHaveValue(null));
});
it("allows arbitrary seconds and saving an empty target", async () => {
  mount();
  const input = await screen.findByPlaceholderText(en["web.workflow.target_unlimited"]!);
  fireEvent.change(input, { target: { value: "" } });
  fireEvent.change(input, { target: { value: "240" } });
  fireEvent.click(screen.getByRole("button", { name: en["web.save"]! }));
  await waitFor(() => expect(writes).toEqual([{ target_sec: 240 }]));
  fireEvent.change(input, { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: en["web.save"]! }));
  await waitFor(() => expect(writes[1]).toEqual({ target_sec: null }));
});
