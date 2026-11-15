import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Revisions } from "../src/components/Revisions";
import { I18nProvider } from "../src/i18n";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;
const say = (key: string, vars: Record<string, string | number> = {}) =>
  Object.entries(vars).reduce((text, [k, v]) => text.replaceAll(`{${k}}`, String(v)), en[key]!);

const item = (id: string, scene: string, status: string, source = "logic") => ({
  id, episode: 1, scene, source, status, notes: `[${scene}] she names the sender too early`,
  before_text: "**Shot 1**\nShe reads the name aloud.\n- **Lin Wan**: It is from Zhao Heng.",
  after_text: "**Shot 1**\nShe turns the parcel over.\n- **Lin Wan**: Who sent this?",
  created_at: "2026-11-15T10:00:00Z", decided_at: null,
});

let items = [item("a1", "1.2", "pending"), item("b2", "2.1", "pending", "review"), item("c3", "1.1", "applied")];
let posted: { url: string; body: Record<string, unknown> }[] = [];

beforeEach(() => {
  posted = [];
  items = [item("a1", "1.2", "pending"), item("b2", "2.1", "pending", "review"), item("c3", "1.1", "applied")];
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string, init?: { method?: string; body?: string }) => {
    let json: unknown = {};
    if (url.includes("/api/i18n")) json = en;
    else if (init?.method === "POST") {
      const body = JSON.parse(init.body ?? "{}") as Record<string, unknown>;
      posted.push({ url, body });
      if (url.includes("/revisions/decide")) {
        const accept = (body["accept"] as string[] | undefined) ?? [];
        json = { applied: items.filter((i) => accept.includes(i.id)).map((i) => i.scene), discarded: [], reverted: body["revert"] ? ["1.1"] : [], conflicts: [] };
        items = items.filter((i) => !accept.includes(i.id));
      }
    } else if (url.includes("/revisions")) json = { episode: 1, items };
    return { ok: true, status: 200, statusText: "", json: async () => json, text: async () => JSON.stringify(json) };
  });
});
afterEach(() => vi.unstubAllGlobals());

function mount() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
      <I18nProvider>
        <Revisions id={4} episode={1} busy={false} refreshKey="r1" />
      </I18nProvider>
    </QueryClientProvider>,
  );
}

describe("rewrites are read before they reach the script", () => {
  it("lists every waiting rewrite next to what it replaces and applies only the ticked ones", async () => {
    mount();
    expect(await screen.findByText(say("web.revisions.waiting", { n: 2 }))).toBeTruthy();
    // both sides are shown, the lines that go and the lines that come are marked
    expect(screen.getAllByText("She reads the name aloud.")[0]!.className).toBe("del");
    expect(screen.getAllByText("She turns the parcel over.")[0]!.className).toBe("add");
    expect(screen.getAllByText("**Shot 1**")[0]!.className).toBe("");
    fireEvent.click(screen.getByLabelText(say("web.revisions.scene", { scene: "2.1" })));        // leave this one out
    fireEvent.click(screen.getByRole("button", { name: say("web.revisions.accept", { n: 1 }) }));
    await vi.waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]!.url).toContain("/api/projects/4/revisions/decide");
    expect(posted[0]!.body).toEqual({ episode: 1, accept: ["a1"] });
    expect(await screen.findByText(say("web.revisions.done", { applied: 1, discarded: 0, reverted: 0 }))).toBeTruthy();
    // what was changed is built on: one click does the stages after the script again
    fireEvent.click(screen.getByRole("button", { name: en["web.revisions.redo"]! }));
    await vi.waitFor(() => expect(posted).toHaveLength(2));
    expect(posted[1]!.url).toContain("/api/projects/4/workflow/runs");
    expect(posted[1]!.body).toEqual({ params: { episode: 1 } });
  });

  it("takes back a rewrite that a run to the film applied by itself", async () => {
    mount();
    fireEvent.click(await screen.findByText(say("web.revisions.applied", { n: 1 })));
    fireEvent.click(screen.getByRole("button", { name: en["web.revisions.revert"]! }));
    await vi.waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]!.body).toEqual({ episode: 1, revert: ["c3"] });
  });

  it("shows nothing when there is nothing to read", async () => {
    items = [];
    const { container } = mount();
    await vi.waitFor(() => expect(container.textContent).toBe(""));
  });
});
