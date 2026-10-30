import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { I18nProvider } from "../src/i18n";
import { ControlPage } from "../src/pages/ControlPage";
import { PublicationsPage } from "../src/pages/PublicationsPage";

const en = JSON.parse(readFileSync(join(__dirname, "../../storyagent/locales/en.json"), "utf8")) as Record<string, string>;
const writes: { path: string; body: Record<string, unknown> }[] = [];
const cap = { id: "image.generate", label_key: "media.kind.image.generate", outputs: { main: "image" }, inputs: [{ name: "prompt", type: "longtext", media: "", required: true }], bindings: [{ id: "test-image", provider: "test", verification: "configured", memory_gib: 1 }] };
beforeEach(() => {
  writes.length = 0;
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    let data: unknown = {};
    if (init?.method && init.method !== "GET") {
      writes.push({ path: url, body: JSON.parse(String(init.body)) as Record<string, unknown> });
      data = url.endsWith("/plans") ? { nodes: [] } : { id: "saved" };
    } else if (url.includes("/api/i18n")) data = en;
    else if (url === "/api/capabilities") data = [cap];
    else if (url === "/api/control/templates") data = [];
    else if (url === "/api/control/runs" || url.startsWith("/api/publications?")) data = { total: 0, items: [] };
    else if (url === "/api/publications/targets") data = [{ id: "target", platform: "Example", account: "artist", name: "Artist", channel: "" }];
    else if (url === "/api/publications/categories") data = [];
    else if (url.startsWith("/api/assets?")) data = [{ id: "a".repeat(32), kind: "upload.image", slot: "main", locked: false, path: "test.png" }];
    return { ok: true, status: 200, json: async () => data };
  });
});
afterEach(() => vi.unstubAllGlobals());
function mount(page: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><I18nProvider>{page}</I18nProvider></MemoryRouter></QueryClientProvider>);
}
it("requires a valid preview before submitting the same visible plan", async () => {
  mount(<ControlPage />);
  const add = await screen.findByRole("button", { name: en["web.control.add"] });
  await waitFor(() => expect(add).toBeEnabled());
  fireEvent.click(add);
  const prompt = await screen.findByLabelText(en["workshop.field.prompt"]!);
  fireEvent.change(prompt, { target: { value: "A porcelain teapot" } });
  const submit = screen.getByRole("button", { name: en["web.control.submit"] });
  expect(submit).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: en["web.control.preview"] }));
  await waitFor(() => expect(submit).toBeEnabled());
  fireEvent.click(submit);
  await waitFor(() => expect(writes.some(x => x.path === "/api/control/runs")).toBe(true));
  expect(writes.find(x => x.path === "/api/control/runs")!.body.nodes).toEqual(expect.arrayContaining([expect.objectContaining({ inputs: { prompt: "A porcelain teapot" } })]));
});
it("prepares a manual publication without claiming it was published", async () => {
  mount(<PublicationsPage />);
  fireEvent.change(await screen.findByLabelText(en["web.pub.post_title"]!), { target: { value: "My image" } });
  const target = screen.getByLabelText(en["web.pub.target"]!, { selector: "section select" });
  await waitFor(() => expect(target.querySelectorAll("option")).toHaveLength(2));
  fireEvent.change(target, { target: { value: "target" } });
  fireEvent.click(await screen.findByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: en["web.pub.save"] }));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]!.path).toBe("/api/publications");
  expect(writes[0]!.body.items).toEqual([{ asset_id: "a".repeat(32), role: "main" }]);
  expect(writes[0]!.body).not.toHaveProperty("status", "published");
});
