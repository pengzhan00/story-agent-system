import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { UNAUTHORIZED_EVENT, useAuthMode, useLogin, useMe } from "../api/auth";
import { useI18n } from "../i18n";
import { ErrorNote } from "./ErrorNote";

function LoginPage({ firstUser }: { firstUser: boolean }) {
  const { t, locale, locales, setLocale } = useI18n();
  const login = useLogin();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate({ username, password });
  };
  return (
    <div className="login">
      <form className="login-card stack" onSubmit={submit}>
        <h1>{t("web.app.name")}</h1>
        <p className="muted">{t("web.auth.signin_hint")}</p>
        {firstUser ? <p className="note">{t("web.auth.first_user")}</p> : null}
        <label className="field">
          <span>{t("web.auth.username")}</span>
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus required />
        </label>
        <label className="field">
          <span>{t("web.auth.password")}</span>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
        </label>
        <ErrorNote error={login.error} />
        <button className="primary" type="submit" disabled={login.isPending || !username || !password}>
          {t("web.auth.signin")}
        </button>
        <select aria-label={t("web.language")} value={locale} onChange={(e) => setLocale(e.target.value)}>
          {locales.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
      </form>
    </div>
  );
}

/**
 * Shows the app only to someone who may use it: with sign-in switched off (single operator) it does nothing; in token mode
 * it asks for a sign-in first, and comes back to the sign-in page when the server says the session is over.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const mode = useAuthMode();
  const me = useMe();

  useEffect(() => {
    const again = () => void qc.invalidateQueries({ queryKey: ["auth", "me"] });
    window.addEventListener(UNAUTHORIZED_EVENT, again);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, again);
  }, [qc]);

  if (mode.isLoading || me.isLoading) return <p className="muted">{t("web.loading")}</p>;
  if (mode.data?.mode === "token" && !me.data?.user) return <LoginPage firstUser={mode.data.first_user} />;
  return <>{children}</>;
}
