import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../src/i18n";
import { UsersPage } from "../src/pages/UsersPage";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;
const row = (over: Record<string, unknown>) => ({
  id: 7, username: "kid", display_name: "", role: "viewer", active: true, last_login_at: null, created_at: "x",
  age: null, age_status: "pending", age_claim: "2012-04-01", birth_date: null, age_note: "", age_reviewed_by: null, age_reviewed_at: null, ...over,
});
let calls: { url: string; body: string }[] = [];

beforeEach(() => {
  calls = [];
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string, init?: { method?: string; body?: string }) => {
    calls.push({ url: `${init?.method ?? "GET"} ${url}`, body: String(init?.body ?? "") });
    let json: unknown = {};
    if (url.includes("/api/i18n")) json = en;
    else if (url.includes("/api/meta")) json = { auth_roles: ["admin", "viewer"] };
    else if (url.endsWith("/api/users")) json = [row({})];
    else if (url.includes("/api/users/7/age")) json = row({ age: 14, age_status: "approved", birth_date: "2012-04-01" });
    return { ok: true, status: 200, statusText: "", json: async () => json, text: async () => JSON.stringify(json) };
  });
});
afterEach(() => vi.unstubAllGlobals());

describe("UsersPage age review", () => {
  it("shows the waiting requests with the claimed date and approves or rejects them", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nProvider>
          <UsersPage />
        </I18nProvider>
      </QueryClientProvider>,
    );
    expect(await screen.findByText(en["web.users.age_pending_n"]!.replace("{n}", "1"))).toBeTruthy();
    expect(screen.getByText(`${en["web.users.age_claim"]!}: 2012-04-01`)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: en["web.users.age_approve"]! }));
    await waitFor(() => expect(calls.some((c) => c.url === "POST /api/users/7/age")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url === "POST /api/users/7/age")?.body ?? "{}")).toEqual({ approve: true, birth_date: "", note: "" });
    fireEvent.click(screen.getByRole("button", { name: en["web.users.age_reject"]! }));
    await waitFor(() => expect(calls.filter((c) => c.url === "POST /api/users/7/age")).toHaveLength(2));
    expect(JSON.parse(calls.filter((c) => c.url === "POST /api/users/7/age")[1]?.body ?? "{}").approve).toBe(false);
  });
});
