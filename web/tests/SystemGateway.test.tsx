import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, expect, it, vi } from "vitest";
import { I18nProvider } from "../src/i18n";
import { SystemPage } from "../src/pages/SystemPage";

vi.mock("../src/components/UpdatePanel", () => ({ UpdatePanel: () => null }));
vi.mock("../src/components/MaintenancePanel", () => ({ MaintenancePanel: () => null }));
const en = JSON.parse(readFileSync(join(__dirname, "../../storyagent/locales/en.json"), "utf8"));
afterEach(() => vi.unstubAllGlobals());
it("offers the independent startup and exit control when the local entrance is present", async () => {
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string) => {
    const data = url.includes("/api/i18n") ? en : url.includes("/__launcher/boot") ? { token: "local" } : { entries: [] };
    return { ok: true, headers: new Headers({ "content-type": "application/json" }), json: async () => data };
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <I18nProvider><SystemPage /></I18nProvider>
  </QueryClientProvider>);
  expect(await screen.findByRole("link", { name: en["gateway.manage_link"] })).toHaveAttribute("href", "/__launcher/");
});
