import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../src/i18n";
import { DeliveryPanel } from "../src/pages/DeliveryPanel";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;
const tr = (k: string): string => en[k] ?? k;
const platform = (name: string, isDefault: boolean) => ({
  name, label_key: `platform.${name}`, note_key: `platform.${name}.note`, width: 1080, height: 1920, fps: 30, reframe: "crop", loudnorm_i: -14, loudnorm_tp: -1,
  loudnorm_lra: 9, crf: 20, max_mb: 0, max_duration_sec: 0, audio_bitrate: "192k", subtitles: "sidecar", cover_sec: 1, trailer_sec: 15, default: isDefault,
});
let calls: { url: string; body: string }[] = [];

beforeEach(() => {
  calls = [];
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string, init?: { method?: string; body?: string }) => {
    calls.push({ url: `${init?.method ?? "GET"} ${url}`, body: String(init?.body ?? "") });
    let json: unknown = {};
    if (url.includes("/api/i18n")) json = en;
    else if (url.includes("/api/auth/mode")) json = { mode: "off", first_user: false, locales: ["en"] };
    else if (url.includes("/api/auth/me")) json = { mode: "off", user: null, can_write: true, can_create_project: true, is_admin: true };
    else if (url.includes("/api/platforms")) json = [platform("vertical_feed", true), platform("broadcast_tv", false)];
    else if (url.includes("/episodes") && url.includes("/delivery"))
      json = { name: "demo-ep01.zip", bytes: 1, platform: "broadcast_tv", warnings: ["over_duration"], subtitles: ["zh-CN", "en"], files: [], result: { size_mb: 3.5, loudness_lufs: -23.1 } };
    else if (url.includes("/episodes")) json = [{ id: 1, number: 1 }];
    return { ok: true, status: 200, statusText: "", json: async () => json, text: async () => JSON.stringify(json) };
  });
});
afterEach(() => vi.unstubAllGlobals());

describe("DeliveryPanel", () => {
  it("delivers for the chosen platform with the wanted subtitle languages and shows the notes", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nProvider>
          <DeliveryPanel id={1} />
        </I18nProvider>
      </QueryClientProvider>,
    );
    const select = await screen.findByLabelText(tr("web.delivery.platform"));
    await screen.findByRole("option", { name: tr("platform.broadcast_tv") });
    fireEvent.change(select, { target: { value: "broadcast_tv" } });
    fireEvent.change(screen.getByLabelText(tr("web.delivery.languages")), { target: { value: "en, ja" } });
    fireEvent.click(screen.getByRole("button", { name: tr("web.delivery.make") }));
    const link = await screen.findByRole("link", { name: "Download demo-ep01.zip" });
    expect(link.getAttribute("href")).toBe("/api/deliveries/demo-ep01.zip");
    expect(await screen.findByText(tr("delivery.warn.over_duration"))).toBeTruthy();
    const post = calls.find((c) => c.url.startsWith("POST") && c.url.includes("/delivery"));
    expect(JSON.parse(post?.body ?? "{}")).toEqual({ platform: "broadcast_tv", languages: ["en", "ja"] });
  });
});
