import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AssetPreview } from "../src/components/Candidates";
import { I18nProvider } from "../src/i18n";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;
const asset = (kind: string) => ({ id: "a1", kind, slot: "main", locked: false, path: "x", project_id: 1 }) as never;

beforeEach(() => {
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string) => {
    const json = url.includes("/api/i18n") ? en : {};
    return { ok: true, status: 200, statusText: "", json: async () => json, text: async () => "" };
  });
});
afterEach(() => vi.unstubAllGlobals());

const mount = (kind: string) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <I18nProvider>
        <AssetPreview asset={asset(kind)} />
      </I18nProvider>
    </QueryClientProvider>,
  );

describe("AssetPreview enlarge", () => {
  it("opens a picture large on click and closes it again", async () => {
    mount("image.generate");
    expect(document.querySelector("dialog")).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: en["web.media.enlarge"]! }));
    const big = document.querySelector("dialog.lightbox img") as HTMLImageElement;
    expect(big.getAttribute("src")).toBe("/api/assets/a1/file");
    fireEvent.click(screen.getByRole("button", { name: en["web.close"]! }));
    expect(document.querySelector("dialog")).toBeNull();
  });

  it("opens a video large from its enlarge button", async () => {
    mount("video.generate");
    fireEvent.click(await screen.findByRole("button", { name: en["web.media.enlarge"]! }));
    const big = document.querySelector("dialog.lightbox video") as HTMLVideoElement;
    expect(big.getAttribute("src")).toBe("/api/assets/a1/file");
  });
});
