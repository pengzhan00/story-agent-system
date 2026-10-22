import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { UNAUTHORIZED_EVENT, useAuthMode, useLogin, useMe, useRegister } from "../api/auth";
import { useI18n } from "../i18n";
import { ErrorNote } from "./ErrorNote";

function LoginPage({ firstUser, canRegister }: { firstUser: boolean; canRegister: boolean }) {
  const { t, locale, locales, setLocale } = useI18n();
  const login = useLogin();
  const register = useRegister();
  const [registering, setRegistering] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [birthDate, setBirthDate] = useState("");
  const [displayName, setDisplayName] = useState("");
  const action = registering ? register : login;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (registering) register.mutate({ username, password, birth_date: birthDate, display_name: displayName });
    else login.mutate({ username, password });
  };
  return (
    <div className="login">
      <form className="login-card stack" onSubmit={submit}>
        <h1>{t("web.app.name")}</h1>
        <p className="muted">{t(registering ? "web.auth.register_hint" : "web.auth.signin_hint")}</p>
        {firstUser ? <p className="note">{t("web.auth.first_user")}</p> : null}
        <label className="field">
          <span>{t("web.auth.username")}</span>
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus required />
        </label>
        {registering ? (
          <label className="field">
            <span>{t("web.users.display_name")}</span>
            <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} autoComplete="nickname" />
          </label>
        ) : null}
        <label className="field">
          <span>{t("web.auth.password")}</span>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={registering ? "new-password" : "current-password"} required />
        </label>
        {registering ? (
          <label className="field">
            <span>{t("web.auth.birth_date")}</span>
            <input type="date" value={birthDate} onChange={(e) => setBirthDate(e.target.value)} required />
            <span className="muted small">{t("web.auth.birth_hint")}</span>
          </label>
        ) : null}
        <ErrorNote error={action.error} />
        <button className="primary" type="submit" disabled={action.isPending || !username || !password || (registering && !birthDate)}>
          {t(registering ? "web.auth.register" : "web.auth.signin")}
        </button>
        {canRegister ? (
          <button type="button" className="link" onClick={() => setRegistering((v) => !v)}>
            {t(registering ? "web.auth.to_signin" : "web.auth.to_register")}
          </button>
        ) : null}
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
  if (mode.data?.mode === "token" && !me.data?.user) return <LoginPage firstUser={mode.data.first_user} canRegister={mode.data.allow_registration} />;
  return <>{children}</>;
}
