import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../src/i18n";
import { ReviewPanel } from "../src/pages/ReviewPanel";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;
const asset = { id: "a1", job_id: "j1", project_id: 1, shot_id: 5, spec_id: "sp1", spec_version: 1, kind: "video.generate", slot: "main", path: "x.mp4", sha256: "", bytes: 1, duration_sec: 5, width: 704, height: 1280, fps: 24, has_audio: false, metadata: {}, provenance: {}, version: 1, is_current: true, created_at: "t", trashed_at: null };
const board = [{ shot_id: 5, episode: 1, act: 1, scene: 2, shot: 3, location: "Alley", assets: [asset], open_notes: 0, redoing_notes: 0, resolved_notes: 0 }];
const spec = { id: "sp1", subject_type: "shot.video", subject_id: "5", slot: "main", prompt: "she walks", version: 1 };

let posts: { url: string; body: unknown }[] = [];
let notes: unknown[] = [];

beforeEach(() => {
  posts = [];
  notes = [];
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string, init?: { method?: string; body?: string }) => {
    let json: unknown = {};
    if (url.includes("/api/i18n")) json = en;
    else if (url.includes("/api/auth/mode")) json = { mode: "off", first_user: false, locales: ["en"] };
    else if (url.includes("/review/board")) json = board;
    else if (url.includes("/specs")) json = [spec];
    else if (url.includes("/api/auth/me")) json = { mode: "off", user: null, can_write: true, can_create_project: true, is_admin: true };
    else if (init?.method === "POST" && url.includes("/notes")) {
      const body = JSON.parse(init.body ?? "{}") as Record<string, unknown>;
      posts.push({ url, body });
      const note = { id: 1, project_id: 1, slot: "main", asset_id: "a1", status: "open", author: "local", resolution: "", resolved_by: "", resolved_at: null, redo_spec_version: null, redo_job_ids: [], created_at: "t", updated_at: "t", ...body };
      notes = [note];
      json = note;
    } else if (url.includes("/notes")) json = notes;
    return { ok: true, status: 200, statusText: "", json: async () => json, text: async () => JSON.stringify(json) };
  });
});
afterEach(() => vi.unstubAllGlobals());

describe("ReviewPanel", () => {
  it("lists the shots, writes a note on the take in use and shows it with a redo action", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <I18nProvider>
          <ReviewPanel id={1} />
        </I18nProvider>
      </QueryClientProvider>,
    );
    expect((await screen.findAllByText(/Alley/)).length).toBeGreaterThan(0);
    fireEvent.change(await screen.findByLabelText(en["web.review.note"]!), { target: { value: "She turns away." } });
    fireEvent.click(screen.getByRole("button", { name: en["web.review.add_note"]! }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]!.body).toMatchObject({ subject_type: "shot.video", subject_id: "5", slot: "main", shot_id: 5, asset_id: "a1", body: "She turns away." });
    expect(await screen.findByText("She turns away.")).toBeTruthy();
    expect(screen.getByRole("button", { name: en["web.review.redo"]! })).toBeTruthy();
  });
});
