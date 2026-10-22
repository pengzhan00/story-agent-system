import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PromptAssist } from "../src/components/PromptAssist";
import { I18nProvider } from "../src/i18n";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;
const sent: Record<string, unknown>[] = [];

function mount(onApply: (s: string) => void) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nProvider>
        <PromptAssist text="雨夜的街道" onApply={onApply} purpose="image" projectId={3} />
      </I18nProvider>
    </QueryClientProvider>,
  );
}

describe("PromptAssist", () => {
  beforeEach(() => {
    sent.length = 0;
    vi.stubGlobal("fetch", async (url: string, init?: { body?: string }) => {
      if (url.includes("/api/i18n")) return { ok: true, status: 200, json: async () => en };
      if (url.includes("/api/ai/prompt")) {
        sent.push(JSON.parse(init?.body ?? "{}") as Record<string, unknown>);
        return { ok: true, status: 200, json: async () => ({ text: "a rainy street at night", action: "translate", backend: "ollama", model: "m1" }) };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("translates a prompt, shows the result first and replaces the text only when it is taken", async () => {
    const onApply = vi.fn();
    mount(onApply);
    fireEvent.click(await screen.findByText(en["web.ai.translate"] ?? ""));
    fireEvent.click(screen.getByText(en["web.ai.go"] ?? ""));
    expect(await screen.findByText("a rainy street at night")).toBeTruthy();
    expect(onApply).not.toHaveBeenCalled();
    expect(sent[0]).toMatchObject({ action: "translate", text: "雨夜的街道", target: "en", purpose: "image", project_id: 3 });
    fireEvent.click(screen.getByText(en["web.ai.apply"] ?? ""));
    await waitFor(() => expect(onApply).toHaveBeenCalledWith("a rainy street at night"));
  });

  it("sends the author's instruction with a suggestion", async () => {
    mount(vi.fn());
    fireEvent.click(await screen.findByText(en["web.ai.suggest"] ?? ""));
    fireEvent.change(screen.getByPlaceholderText(en["web.ai.instruction"] ?? ""), { target: { value: "more rain" } });
    fireEvent.click(screen.getByText(en["web.ai.go"] ?? ""));
    await waitFor(() => expect(sent[0]).toMatchObject({ action: "suggest", instruction: "more rain" }));
  });
});
