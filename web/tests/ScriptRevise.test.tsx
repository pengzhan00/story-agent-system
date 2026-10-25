import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ScriptRevise, sceneOf } from "../src/components/ScriptRevise";
import { I18nProvider } from "../src/i18n";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;

let posted: { url: string; body: { stages: string[]; params: Record<string, unknown> } }[] = [];

beforeEach(() => {
  posted = [];
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string, init?: { method?: string; body?: string }) => {
    let json: unknown = {};
    if (url.includes("/api/i18n")) json = en;
    else if (init?.method === "POST") posted.push({ url, body: JSON.parse(init.body ?? "{}") });
    return { ok: true, status: 200, statusText: "", json: async () => json, text: async () => JSON.stringify(json) };
  });
});
afterEach(() => vi.unstubAllGlobals());

const issues = [
  { severity: "high", where: "Act 1/Scene 3/Shots", problem: "Dialogue is flat" },
  { severity: "low", where: "Act 2/Scene 2", problem: "Transition is abrupt" },
  { severity: "medium", where: "Act 2/Scene 1", problem: "Action repeats" },
];

function mount() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <I18nProvider>
        <ScriptRevise id={4} episode={1} issues={issues} />
      </I18nProvider>
    </QueryClientProvider>,
  );
}

describe("revising the script with feedback", () => {
  it("finds the scene an issue points at", () => {
    expect(sceneOf("Act 1/Scene 3/Shots")).toBe("1.3");
    expect(sceneOf("第2幕 第1场")).toBe("2.1");
    expect(sceneOf("whole script")).toBeNull();
  });

  it("rewrites only the scenes of the ticked issues, high and medium ones ticked to begin with", async () => {
    mount();
    await screen.findByText(en["web.revise.title"]!);
    fireEvent.click(screen.getByRole("button", { name: en["web.revise.run"]! }));
    await vi.waitFor(() => expect(posted.length).toBe(1));
    expect(posted[0]!.url).toBe("/api/projects/4/workflow/runs");
    expect(posted[0]!.body.stages).toEqual(["script"]);
    expect(posted[0]!.body.params["revise_scenes"]).toEqual(["1.3", "2.1"]);
    expect(String(posted[0]!.body.params["feedback"])).toContain("Dialogue is flat");
    expect(String(posted[0]!.body.params["feedback"])).not.toContain("abrupt");
  });

  it("rewrites every scene when the editor adds notes of their own", async () => {
    mount();
    await screen.findByText(en["web.revise.title"]!);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "More menace" } });
    fireEvent.click(screen.getByRole("button", { name: en["web.revise.run"]! }));
    await vi.waitFor(() => expect(posted.length).toBe(1));
    expect(posted[0]!.body.params["revise_scenes"]).toBeUndefined();
    expect(String(posted[0]!.body.params["feedback"])).toContain("More menace");
  });
});
