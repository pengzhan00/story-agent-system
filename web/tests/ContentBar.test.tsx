import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ContentBar } from "../src/components/ContentBar";
import { I18nProvider } from "../src/i18n";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;
const tr = (k: string): string => en[k] ?? k;
const status = (unlocked: boolean, needsPassword: boolean, vault = true) => ({
  unlocked, expires_at: unlocked ? "2030-01-01T10:00:00.000Z" : null, ttl_sec: 900, needs_password: needsPassword, vault_ready: vault, encryption: "volume", default_rating: "general", ratings: [],
});
let calls: { url: string; body: string }[] = [];
let state = status(false, false);

beforeEach(() => {
  calls = [];
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string, init?: { method?: string; body?: string }) => {
    calls.push({ url: `${init?.method ?? "GET"} ${url}`, body: String(init?.body ?? "") });
    let json: unknown = {};
    if (url.includes("/api/i18n")) json = en;
    else if (url.includes("/api/auth/mode")) json = { mode: "off", first_user: false, locales: ["en"] };
    else if (url.includes("/api/auth/me")) json = { mode: "off", user: null, can_write: true, can_create_project: true, is_admin: true };
    else if (url.includes("/api/content/unlock")) {
      state = status(init?.method === "POST", state.needs_password);
      json = state;
    } else if (url.includes("/api/content/status")) json = state;
    return { ok: true, status: 200, statusText: "", json: async () => json, text: async () => JSON.stringify(json) };
  });
});
afterEach(() => vi.unstubAllGlobals());

const mount = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <I18nProvider>
        <ContentBar />
      </I18nProvider>
    </QueryClientProvider>,
  );

describe("ContentBar", () => {
  it("unlocks only after the adult confirmation and locks again on request", async () => {
    state = status(false, false);
    mount();
    const unlock = await screen.findByRole("button", { name: tr("web.content.unlock") });
    expect((unlock as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(unlock);
    await screen.findByRole("button", { name: tr("web.content.lock") });
    const post = calls.find((c) => c.url === "POST /api/content/unlock");
    expect(JSON.parse(post?.body ?? "{}")).toEqual({ confirm_adult: true, password: "" });
    fireEvent.click(screen.getByRole("button", { name: tr("web.content.lock") }));
    await waitFor(() => expect(calls.some((c) => c.url === "DELETE /api/content/unlock")).toBe(true));
    await screen.findByText(tr("web.content.locked"));
  });

  it("asks for the password when accounts are on and warns when the vault is not mounted", async () => {
    state = status(false, true, false);
    mount();
    const unlock = (await screen.findByRole("button", { name: tr("web.content.unlock") })) as HTMLButtonElement;
    fireEvent.click(screen.getByRole("checkbox"));
    expect(unlock.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(tr("web.content.password")), { target: { value: "secret pw" } });
    expect(unlock.disabled).toBe(false);
    expect(screen.getByText(tr("web.content.vault_missing"))).toBeTruthy();
  });
});
