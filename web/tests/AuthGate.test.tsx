import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthGate } from "../src/components/AuthGate";
import { I18nProvider } from "../src/i18n";

const en = JSON.parse(readFileSync(join(__dirname, "..", "..", "storyagent", "locales", "en.json"), "utf-8")) as Record<string, string>;
const user = { id: 1, username: "ann", display_name: "Ann", role: "editor", active: true, last_login_at: null, created_at: "x" };

let signedIn = false;
let mode: "off" | "token" = "token";
let logins: unknown[] = [];
let registrations: unknown[] = [];

beforeEach(() => {
  signedIn = false;
  mode = "token";
  logins = [];
  registrations = [];
  localStorage.setItem("storyagent.locale", "en");
  vi.stubGlobal("fetch", async (url: string, init?: { method?: string; body?: string }) => {
    let status = 200;
    let json: unknown = {};
    if (url.includes("/api/i18n")) json = en;
    else if (url.includes("/api/auth/mode")) json = { mode, first_user: false, locales: ["en"], allow_registration: true };
    else if (url.includes("/api/auth/register")) {
      registrations.push(JSON.parse(init?.body ?? "{}"));
      signedIn = true;
      json = { token: "t", expires_at: "x", user };
    }
    else if (url.includes("/api/auth/me")) json = { mode, user: signedIn ? user : null, can_write: true, can_create_project: false, is_admin: false };
    else if (url.includes("/api/auth/login")) {
      logins.push(JSON.parse(init?.body ?? "{}"));
      const ok = (logins[logins.length - 1] as { password: string }).password === "right password";
      status = ok ? 200 : 401;
      signedIn = ok;
      json = ok ? { token: "t", expires_at: "x", user } : { error: { code: "login_failed", message: "Wrong username or password.", remediation: "" } };
    }
    return { ok: status < 400, status, statusText: "", json: async () => json };
  });
});
afterEach(() => vi.unstubAllGlobals());

function renderGate() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nProvider>
        <AuthGate>
          <p>app content</p>
        </AuthGate>
      </I18nProvider>
    </QueryClientProvider>,
  );
}

describe("AuthGate", () => {
  it("shows the app straight away when sign-in is off", async () => {
    mode = "off";
    renderGate();
    expect(await screen.findByText("app content")).toBeTruthy();
  });

  it("asks for a sign-in in token mode, reports a wrong password and then lets the user in", async () => {
    renderGate();
    expect(await screen.findByText(en["web.auth.signin_hint"]!)).toBeTruthy();
    expect(screen.queryByText("app content")).toBeNull();
    fireEvent.change(screen.getByLabelText(en["web.auth.username"]!), { target: { value: "ann" } });
    fireEvent.change(screen.getByLabelText(en["web.auth.password"]!), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: en["web.auth.signin"]! }));
    expect(await screen.findByText("Wrong username or password.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText(en["web.auth.password"]!), { target: { value: "right password" } });
    fireEvent.click(screen.getByRole("button", { name: en["web.auth.signin"]! }));
    await waitFor(() => expect(screen.getByText("app content")).toBeTruthy());
    expect(logins).toHaveLength(2);
  });

  it("registers with a birth date that waits for an admin, and lets the new account in", async () => {
    renderGate();
    fireEvent.click(await screen.findByRole("button", { name: en["web.auth.to_register"]! }));
    expect(screen.getByText(en["web.auth.birth_hint"]!)).toBeTruthy();
    const submit = screen.getByRole("button", { name: en["web.auth.register"]! }) as HTMLButtonElement;
    fireEvent.change(screen.getByLabelText(en["web.auth.username"]!), { target: { value: "kid" } });
    fireEvent.change(screen.getByLabelText(en["web.auth.password"]!), { target: { value: "right password" } });
    expect(submit.disabled).toBe(true);                                       // no birth date yet
    fireEvent.change(screen.getByLabelText(en["web.auth.birth_date"]!, { exact: false }), { target: { value: "2012-04-01" } });
    fireEvent.click(submit);
    await waitFor(() => expect(screen.getByText("app content")).toBeTruthy());
    expect(registrations).toEqual([{ username: "kid", password: "right password", birth_date: "2012-04-01", display_name: "" }]);
  });
});
