import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, expect, it, vi } from "vitest";
import { I18nProvider } from "../src/i18n";
import { History } from "../src/pages/WorkshopPage";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
it("clears selected finished workshop results and refreshes the list, leaving active jobs", async () => {
  let removed: string[] = [];
  localStorage.setItem("storyagent.locale", "en");
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.stubGlobal("fetch", async (url: string, init?: { body?: string }) => {
    let data: unknown = en;
    if (url.includes("/api/jobs/history/remove")) {
      removed = JSON.parse(init?.body ?? "{}").ids;
      data = { removed: removed.length };
    } else if (url.includes("/api/workshop/history")) {
      data = ["failed", "done", "active"].filter((id) => !removed.includes(id)).map((id) => ({
        assets: [], job: { id, kind: "image.generate", model_ref: "h3-image", slot: "main", inputs: {}, params: {},
          status: id === "active" ? "running" : id === "done" ? "completed" : "failed_validation",
          created_at: "2026-11-15T10:53:00Z", error_code: null, progress: 0, progress_msg: "" },
      }));
    }
    return { ok: true, status: 200, json: async () => data, text: async () => JSON.stringify(data) };
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <I18nProvider><History module="image" modules={[]} onUse={() => undefined} onAgain={() => undefined} /></I18nProvider>
  </QueryClientProvider>);
  const selectAll = await screen.findByRole("checkbox", { name: en["web.jobs.select_page"]! });
  expect(screen.getByRole("checkbox", { name: "Select job #active" })).toBeDisabled();
  fireEvent.click(selectAll);
  fireEvent.click(screen.getByRole("button", { name: en["web.jobs.remove_selected"]! }));
  await waitFor(() => expect(removed).toEqual(["failed", "done"]));
  await waitFor(() => expect(screen.queryByRole("checkbox", { name: "Select job #failed" })).not.toBeInTheDocument());
  expect(screen.getByRole("checkbox", { name: "Select job #active" })).toBeDisabled();
});
