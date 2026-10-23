import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../src/i18n";
import { ToolsPage } from "../src/pages/ToolsPage";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;
const PREVIEW = {
  bindings: [{ name: "prompt", targets: ["2.text"], classes: ["CLIPTextEncode"], value: "a cat" }],
  warnings: [{ code: "no_seed", message: "No random seed found" }],
  output_node: "7",
  output_type: ["images"],
  defaults: {},
};
let calls: { method: string; url: string; body: unknown }[] = [];

beforeEach(() => {
  calls = [];
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string, init?: { method?: string; body?: string }) => {
    calls.push({ method: init?.method ?? "GET", url, body: init?.body ? JSON.parse(init.body) : undefined });
    let json: unknown = {};
    let status = 200;
    if (url.includes("/api/i18n")) json = en;
    else if (url.includes("/api/meta")) json = { kinds: [{ value: "image.generate", label: "Image" }, { value: "text.story", label: "Story" }] };
    else if (url.endsWith("/api/tools")) json = [{ ref: "klein", kind: "image.edit", provider: "comfy", family: "flux2", workflow: "flux2_klein_edit", mine: false }];
    else if (url.includes("/api/tools/import/comfy")) json = PREVIEW;
    else if (url.endsWith("/api/tools/comfy")) {
      json = { ref: "my-sd", workflow: "/cfg/workflows/my-sd.json", models_file: "/cfg/models.toml", restart_needed: true };
      status = 201;
    }
    return { ok: true, status, statusText: "", json: async () => json, text: async () => JSON.stringify(json) };
  });
});
afterEach(() => vi.unstubAllGlobals());

const mount = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <I18nProvider>
        <ToolsPage />
      </I18nProvider>
    </QueryClientProvider>,
  );

const pick = (text: string, name = "My SD.json") => {
  const file = new File([text], name, { type: "application/json" });
  Object.defineProperty(file, "text", { value: async () => text });
  fireEvent.change(screen.getByLabelText(en["web.tools.file"]!), { target: { files: [file] } });
};

describe("tools page", () => {
  it("previews what was found, then saves the workflow as a model", async () => {
    mount();
    expect(await screen.findByText("klein")).toBeTruthy();                                   // the models that run through ComfyUI
    const previewButton = screen.getByRole("button", { name: en["web.tools.preview"]! }) as HTMLButtonElement;
    expect(previewButton.disabled).toBe(true);                                               // no file yet
    expect(screen.queryByRole("option", { name: "Story" })).toBeNull();                      // text kinds cannot be imported
    pick(JSON.stringify({ "1": { class_type: "KSampler", inputs: {} } }));
    await waitFor(() => expect(previewButton.disabled).toBe(false));
    fireEvent.click(previewButton);
    expect(await screen.findByText("No random seed found")).toBeTruthy();
    expect(screen.getByText("2.text (CLIPTextEncode)")).toBeTruthy();
    const name = screen.getByLabelText(en["web.tools.name"]!, { selector: "input" }) as HTMLInputElement;
    await waitFor(() => expect(name.value).toBe("my-sd"));                                  // from the file name
    fireEvent.click(screen.getByRole("button", { name: en["web.tools.save"]! }));
    expect(await screen.findByText("Saved: my-sd")).toBeTruthy();
    const save = calls.find((c) => c.method === "POST" && c.url.endsWith("/api/tools/comfy"));
    expect(save?.body).toMatchObject({ kind: "image.generate", name: "my-sd", ref: "my-sd", timeout_sec: 1800, force: false });
  });

  it("says so when the file is not JSON", async () => {
    mount();
    await screen.findByText("klein");
    pick("not json at all");
    expect(await screen.findByText(en["web.tools.invalid_json"]!)).toBeTruthy();
  });
});
