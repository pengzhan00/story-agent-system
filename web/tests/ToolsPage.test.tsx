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
const TEMPLATES = [{ name: "cli", models: '[models."my-cli"]\nkind = "audio.tts"\n', services: "" }];
const OK = { ref: "my-cli", kind: "audio.tts", provider: "cli", ok: true, issues: [{ level: "warning", code: "license_empty", message: "Licence is empty" }] };
const BAD = { ref: "my-cli", kind: "audio.tts", provider: "cli", ok: false, issues: [{ level: "error", code: "cli_argv", message: "argv must be a list" }] };
const option = (flag: string, role: string, kind: string, extra: Record<string, unknown> = {}) => ({
  flag, flags: [flag], metavars: kind === "bool" ? [] : ["N"], description: `${flag} text`, kind, default: null, role, param: flag.slice(2), repeat: false, include: true, ...extra,
});
const PARSED = {
  program: "/tools/h3", ran: true, roles: ["param", "switch", "prompt", "image", "output", "fixed", "skip"],
  options: [option("--prompt", "prompt", "string"), option("--output", "output", "path"), option("--first-frame", "image", "path"), option("--width", "param", "int", { default: 864 })],
};
const BUILT = { models: '[models."h3"]\nkind = "video.generate"\n', command: "/tools/h3 --prompt '<prompt>' --output '<work>/output.mp4'", argv: [] };
let checkAnswer: unknown = OK;
let calls: { method: string; url: string; body: unknown }[] = [];

beforeEach(() => {
  calls = [];
  checkAnswer = OK;
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string, init?: { method?: string; body?: string }) => {
    calls.push({ method: init?.method ?? "GET", url, body: init?.body ? JSON.parse(init.body) : undefined });
    let json: unknown = {};
    let status = 200;
    if (url.includes("/api/i18n")) json = en;
    else if (url.includes("/api/meta")) json = { kinds: [{ value: "image.generate", label: "Image" }, { value: "text.story", label: "Story" }] };
    else if (url.endsWith("/api/tools")) json = [{ ref: "klein", kind: "image.edit", provider: "comfy", family: "flux2", workflow: "flux2_klein_edit", mine: false }];
    else if (url.endsWith("/api/tools/templates")) json = TEMPLATES;
    else if (url.endsWith("/api/tools/cli/parse")) json = PARSED;
    else if (url.endsWith("/api/tools/cli/build")) json = BUILT;
    else if (url.endsWith("/api/tools/connector/check")) json = checkAnswer;
    else if (url.endsWith("/api/tools/connector")) {
      json = { ref: "my-cli", reloaded: true, error: "", issues: [] };
      status = 201;
    } else if (url.includes("/api/tools/import/comfy")) json = PREVIEW;
    else if (url.endsWith("/api/tools/comfy")) {
      json = { ref: "my-sd", workflow: "/cfg/workflows/my-sd.json", models_file: "/cfg/models.toml", reloaded: true, error: "" };
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

  it("fills the editor from a starting point, checks it and saves it", async () => {
    mount();
    await screen.findByText("klein");
    const picker = screen.getByLabelText(en["web.tools.start_from"]!) as HTMLSelectElement;
    fireEvent.change(picker, { target: { value: "cli" } });
    const models = screen.getByLabelText(en["web.tools.models_toml"]!) as HTMLTextAreaElement;
    await waitFor(() => expect(models.value).toContain('[models."my-cli"]'));
    fireEvent.click(screen.getByRole("button", { name: en["web.tools.check"]! }));
    expect(await screen.findByText("Check passed: my-cli")).toBeTruthy();
    expect(screen.getByText("Licence is empty")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: en["web.tools.save_connector"]! }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/api/tools/connector"))).toBe(true));
    const saved = calls.find((c) => c.method === "POST" && c.url.endsWith("/api/tools/connector"));
    expect(saved?.body).toMatchObject({ models: models.value, services: "", force: false });
  });

  it("shows what is wrong and does not save", async () => {
    checkAnswer = BAD;
    mount();
    await screen.findByText("klein");
    fireEvent.change(screen.getByLabelText(en["web.tools.start_from"]!), { target: { value: "cli" } });
    await waitFor(() => expect((screen.getByLabelText(en["web.tools.models_toml"]!) as HTMLTextAreaElement).value).not.toBe(""));
    fireEvent.click(screen.getByRole("button", { name: en["web.tools.save_connector"]! }));
    expect(await screen.findByText("argv must be a list")).toBeTruthy();
    expect(screen.getByText(en["web.tools.checked_bad"]!)).toBeTruthy();
    expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/api/tools/connector"))).toBe(false);
  });

  it("reads a tool's usage into rows and hands the built configuration to the connector editor", async () => {
    mount();
    fireEvent.change(await screen.findByLabelText(en["web.tools.wiz_program"]!), { target: { value: "/tools/h3" } });
    fireEvent.click(screen.getByRole("button", { name: en["web.tools.wiz_read"]! }));
    expect(await screen.findByText("--first-frame N")).toBeTruthy();
    const role = screen.getByLabelText("--first-frame role") as HTMLSelectElement;
    expect(role.value).toBe("image");                                                       // files are typed, not pathed
    expect(screen.queryByLabelText("--first-frame value")).toBeNull();                      // no path to type for an input file
    fireEvent.change(screen.getByLabelText("--width value"), { target: { value: "720" } });
    fireEvent.click(screen.getByLabelText("--prompt"));                                     // leave one option out
    fireEvent.change(screen.getByLabelText(en["web.tools.wiz_ref"]!), { target: { value: "h3-cli" } });
    fireEvent.click(screen.getByRole("button", { name: en["web.tools.wiz_build"]! }));
    expect(await screen.findByText(/--output '<work>\/output.mp4'/)).toBeTruthy();
    const build = calls.find((c) => c.url.endsWith("/api/tools/cli/build"))!.body as { ref: string; options: { flag: string; include: boolean; default: unknown }[] };
    expect(build.ref).toBe("h3-cli");
    expect(build.options.find((o) => o.flag === "--prompt")!.include).toBe(false);
    expect(build.options.find((o) => o.flag === "--width")!.default).toBe(720);
    await waitFor(() => expect((screen.getByLabelText(en["web.tools.models_toml"]!) as HTMLTextAreaElement).value).toContain('[models."h3"]'));
  });
});
