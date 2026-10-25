import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VoicePicker } from "../src/components/VoicePicker";
import { I18nProvider } from "../src/i18n";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;
const voice = (id: string, name: string, description: string) => ({ id, role: "voice", name, ref: `library:voice/${name}`, description, asset: { id: `a-${id}` } });

beforeEach(() => {
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string) => {
    const json: unknown = url.includes("/api/i18n") ? en : [voice("1", "Mei", "young female"), voice("2", "Reed", "adult male")];
    return { ok: true, status: 200, statusText: "", json: async () => json, text: async () => JSON.stringify(json) };
  });
});
afterEach(() => vi.unstubAllGlobals());

describe("choosing a voice by ear", () => {
  it("plays the voice picked in the list and applies it only on the button", async () => {
    const applied: string[] = [];
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nProvider>
          <VoicePicker value="" onApply={(r) => applied.push(r)} />
        </I18nProvider>
      </QueryClientProvider>,
    );
    await screen.findByRole("option", { name: "Reed" });
    expect(screen.queryByLabelText(en["web.plan.voice_listen"]!)).toBeNull();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "library:voice/Reed" } });
    const player = (await screen.findByLabelText(en["web.plan.voice_listen"]!)) as HTMLAudioElement;
    expect(player.getAttribute("src")).toBe("/api/assets/a-2/file");
    expect(screen.getByText("adult male")).toBeTruthy();
    expect(applied).toEqual([]);                                   // listening changes nothing
    fireEvent.click(screen.getByRole("button", { name: en["web.plan.voice_use"]! }));
    expect(applied).toEqual(["library:voice/Reed"]);
  });
});
