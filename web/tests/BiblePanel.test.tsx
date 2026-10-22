import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../src/i18n";
import { BiblePanel } from "../src/pages/BiblePanel";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;
const calls: { url: string; method: string; body?: string }[] = [];

describe("BiblePanel", () => {
  beforeEach(() => {
    calls.length = 0;
    vi.stubGlobal("fetch", async (url: string, init?: { method?: string; body?: string }) => {
      calls.push({ url, method: init?.method ?? "GET", body: init?.body });
      const json = url.includes("/api/i18n")
        ? en
        : url.endsWith("/api/projects/1")
          ? { id: 1, name: "P", description: "a quiet street" }
          : url.endsWith("/episodes")
            ? [{ id: 5, number: 1, title: "Pilot", summary: "they meet" }]
            : url.endsWith("/seasons")
              ? [{ number: 1, title: "", summary: "the whole season", episodes: 1 }]
              : url.endsWith("/shots")
                ? [{ id: 9, episode_id: 5, act_number: 1, scene_number: 1, location: "street", narration: "Rain.", dialogue: [{ character: "Ann", line: "Hello", emotion: "calm" }] }]
                : url.includes("/data/") || url.endsWith("/characters")
                  ? {}
                  : {};
      return { ok: true, status: 200, json: async () => json };
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("shows the premise, the outline and the script as it reads, and saves an edited premise", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <I18nProvider>
          <BiblePanel id={1} />
        </I18nProvider>
      </QueryClientProvider>,
    );
    const box = await screen.findByDisplayValue("a quiet street");
    expect(await screen.findByText("Rain.")).toBeTruthy();
    expect(screen.getByText(": Hello")).toBeTruthy();
    expect(screen.getByDisplayValue("the whole season")).toBeTruthy();

    fireEvent.change(box, { target: { value: "a rainy street" } });
    fireEvent.click(screen.getAllByText(en["web.save"] ?? "")[0] as HTMLElement);
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && c.url.endsWith("/api/projects/1") && c.body?.includes("a rainy street"))).toBe(true));
  });
});
