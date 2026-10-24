import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectScripts } from "../src/components/ProjectScripts";
import { I18nProvider } from "../src/i18n";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;

const brief = (over: Record<string, unknown>) => ({
  project_id: 1, project_name: "Drama", project_kind: "", rating: "general", locked: false, episode_no: 1, title: "Pilot", synopsis: "Two people, one kitchen.",
  scenes: 2, shots: 8, lines: 5, words: 900, status: "draft", updated_at: "2026-10-24T10:00:00Z", ...over,
});

let urls: string[] = [];
let posted: Record<string, unknown>[] = [];
let taken_names: string[] = [];

beforeEach(() => {
  urls = [];
  posted = [];
  taken_names = [];
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string, init?: { method?: string; body?: string }) => {
    urls.push(`${init?.method ?? "GET"} ${url}`);
    let json: unknown = {};
    if (url.includes("/publish")) {
      posted.push(JSON.parse(init?.body ?? "{}"));
      const taken = taken_names.includes(posted[posted.length - 1]!["name"] as string) && !posted[posted.length - 1]!["replace"];
      if (taken) return { ok: false, status: 409, statusText: "Conflict", json: async () => ({ error: { code: "conflict", message: "taken", remediation: "", detail: null } }), text: async () => "" };
      json = { ref: `library:script/${posted[posted.length - 1]!["name"]}` };
    } else if (url.includes("/api/i18n")) json = en;
    else if (url.includes("/api/scripts/3/1")) json = { ...brief({ project_id: 3, project_name: "Advert", project_kind: "advert", title: "Spot" }), text: "# Advert\n\n## Act 1\n\n- **Mia**: Buy it now.\n" };
    else if (url.includes("/api/scripts"))
      json = [brief({}), brief({ project_id: 3, project_name: "Advert", project_kind: "advert", title: "Spot", synopsis: "A 30 s spot." }), brief({ project_id: 4, project_name: "Private", rating: "restricted", locked: true, title: "", synopsis: "" })];
    return { ok: true, status: 200, statusText: "", json: async () => json, text: async () => JSON.stringify(json) };
  });
});
afterEach(() => vi.unstubAllGlobals());

function mount(q = "") {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <I18nProvider>
        <MemoryRouter>
          <ProjectScripts q={q} projectId={null} />
        </MemoryRouter>
      </I18nProvider>
    </QueryClientProvider>,
  );
}

describe("project scripts in the library", () => {
  it("lists the scripts of every project, the advert too, and opens one as readable text", async () => {
    mount();
    expect(await screen.findByText("Pilot")).toBeTruthy();
    expect(screen.getByText("Spot")).toBeTruthy();
    expect(screen.getAllByText("2 scenes · 8 shots · 5 lines of dialogue").length).toBe(3);
    fireEvent.click(screen.getAllByRole("button", { name: en["web.library.scripts.read"]! })[1]!);
    expect(await screen.findByText("Buy it now.", { exact: false })).toBeTruthy();
    expect(screen.getByRole("button", { name: en["web.library.scripts.download"]! })).toBeTruthy();
    expect(screen.getAllByRole("link", { name: en["web.library.scripts.open_project"]! })[1]!.getAttribute("href")).toBe("/projects/3/story");
  });

  it("files a script under a name, and asks before taking a name that is used", async () => {
    taken_names = ["Advert-1"];
    mount();
    await screen.findByText("Spot");
    fireEvent.click(screen.getAllByRole("button", { name: en["web.library.scripts.file"]! })[1]!);
    fireEvent.click(screen.getByRole("button", { name: en["web.library.file_it"]! }));
    const replace = await screen.findByLabelText(en["web.library.scripts.replace"]!);
    expect(posted).toEqual([{ name: "Advert-1", description: "", replace: false }]);
    fireEvent.click(replace);
    fireEvent.click(screen.getByRole("button", { name: en["web.library.file_it"]! }));
    expect(await screen.findByText("library:script/Advert-1")).toBeTruthy();
    expect(posted[1]).toEqual({ name: "Advert-1", description: "", replace: true });
  });

  it("shows a locked restricted project without its words and without a way to read it", async () => {
    mount();
    expect(await screen.findByText(en["web.library.scripts.locked"]!)).toBeTruthy();
    expect(screen.getAllByRole("button", { name: en["web.library.scripts.read"]! }).length).toBe(2);
    expect(urls.some((u) => u.includes("/api/scripts/4/"))).toBe(false);
    expect(screen.getAllByRole("button", { name: en["web.library.scripts.file"]! }).length).toBe(2); // not for the locked one
  });

  it("sends the search words to the server", async () => {
    mount("kettle");
    await screen.findByText("Pilot");
    expect(urls.some((u) => u.includes("GET /api/scripts?q=kettle"))).toBe(true);
  });
});
