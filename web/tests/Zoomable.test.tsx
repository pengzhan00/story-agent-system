import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AssetPreview } from "../src/components/Candidates";
import { PreviewDialog } from "../src/components/PreviewDialog";
import { I18nProvider } from "../src/i18n";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;
const asset = (kind: string, locked = false) => ({ id: "a1", kind, slot: "main", locked, path: "x", project_id: 1 }) as never;

beforeEach(() => {
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string) => {
    const json = url.includes("/api/i18n") ? en : {};
    return { ok: true, status: 200, statusText: "", json: async () => json, text: async () => "" };
  });
});
afterEach(() => vi.unstubAllGlobals());

const mount = (kind: string, locked = false) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <I18nProvider>
        <AssetPreview asset={asset(kind, locked)} />
      </I18nProvider>
    </QueryClientProvider>,
  );

describe("AssetPreview enlarge", () => {
  it.each(["image.generate", "upload.image"])("opens %s large on click and closes it again", async (kind) => {
    mount(kind);
    expect(document.querySelector("dialog")).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: en["web.media.enlarge"]! }));
    const big = document.querySelector("dialog.lightbox img") as HTMLImageElement;
    expect(big.getAttribute("src")).toBe("/api/assets/a1/file");
    fireEvent.click(screen.getByRole("button", { name: en["web.close"]! }));
    expect(document.querySelector("dialog")).toBeNull();
  });

  it.each(["video.generate", "upload.video"])("opens %s large from its enlarge button", async (kind) => {
    mount(kind);
    fireEvent.click(await screen.findByRole("button", { name: en["web.media.enlarge"]! }));
    const big = document.querySelector("dialog.lightbox video") as HTMLVideoElement;
    expect(big.getAttribute("src")).toBe("/api/assets/a1/file");
  });
});


it("keeps uploaded images masked while locked", async () => {
  mount("upload.image", true);
  await screen.findByRole("img", { name: en["web.content.masked"]! });
  expect(document.querySelector("img")?.getAttribute("src")).toBe("/api/assets/a1/mask");
  expect(document.querySelector('[src="/api/assets/a1/file"]')).toBeNull();
  expect(screen.queryByRole("button", { name: en["web.media.enlarge"]! })).toBeNull();
});

it.each(["audio.music", "upload.audio"])("retains %s playback", async (kind) => {
  mount(kind);
  await waitFor(() => expect(document.querySelector("audio")?.getAttribute("src")).toBe("/api/assets/a1/file"));
});

it("keeps unsupported files as download links", async () => {
  mount("upload.model");
  expect((await screen.findByRole("link")).getAttribute("href")).toBe("/api/assets/a1/file");
});

it("ignores a late close event after the media dialog has reopened", async () => {
  mount("upload.image");
  fireEvent.click(await screen.findByRole("button", { name: en["web.media.enlarge"]! }));
  const dialog = document.querySelector("dialog") as HTMLDialogElement;
  expect(dialog.open).toBe(true);
  fireEvent(dialog, new Event("close"));
  expect(document.querySelector("dialog")).toBe(dialog);
  dialog.open = false;
  fireEvent(dialog, new Event("close"));
  expect(document.querySelector("dialog")).toBeNull();
});

it("keeps the episode preview open on stale events, but handles a real close", async () => {
  const onClose = vi.fn();
  render(<MemoryRouter><I18nProvider><PreviewDialog assetId="a1" title="Episode" seconds={null} projectId={1} onClose={onClose} /></I18nProvider></MemoryRouter>);
  await screen.findByRole("heading", { name: "Episode" });
  const dialog = document.querySelector("dialog") as HTMLDialogElement;
  fireEvent(dialog, new Event("close"));
  expect(onClose).not.toHaveBeenCalled();
  dialog.open = false;
  fireEvent(dialog, new Event("close"));
  expect(onClose).toHaveBeenCalledOnce();
});
