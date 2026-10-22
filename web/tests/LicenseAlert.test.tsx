import { fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LicenseAlert } from "../src/components/LicenseAlert";
import { I18nProvider } from "../src/i18n";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;

describe("LicenseAlert", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal("fetch", async (url: string) => ({ ok: true, json: async () => (url.includes("/api/i18n") ? en : {}) }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("stays until it is dismissed, then stays away for that model", async () => {
    const { unmount } = render(<I18nProvider><LicenseAlert model="m1" licenseTag="x" /></I18nProvider>);
    expect((await screen.findByRole("alert")).textContent).toContain("m1");
    fireEvent.click(screen.getByRole("button"));
    expect(screen.queryByRole("alert")).toBeNull();
    unmount();
    render(<I18nProvider><LicenseAlert model="m1" /></I18nProvider>);
    await screen.findByText("", { selector: "body" });
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
