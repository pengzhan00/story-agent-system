import { useState } from "react";
import { useContentLock, useContentStatus } from "../api/content";
import { useT } from "../i18n";
import { ErrorNote } from "./ErrorNote";

/** The state of restricted content (locked / unlocked until ...) and the explicit act that unlocks it. */
export function ContentBar() {
  const t = useT();
  const status = useContentStatus().data;
  const { unlock, lock } = useContentLock();
  const [adult, setAdult] = useState(false);
  const [password, setPassword] = useState("");
  if (!status) return null;
  const until = status.expires_at ? new Date(status.expires_at).toLocaleTimeString() : "";
  return (
    <section className="card stack" aria-label={t("web.content.title")}>
      <div className="row">
        <strong>{t("web.content.title")}</strong>
        <span className={status.unlocked ? "chip chip-completed" : "chip"}>{status.unlocked ? t("web.content.unlocked", { time: until }) : t("web.content.locked")}</span>
        <span className="spacer" />
        {status.unlocked ? (
          <button disabled={lock.isPending} onClick={() => lock.mutate()}>
            {t("web.content.lock")}
          </button>
        ) : null}
      </div>
      {status.unlocked ? null : (
        <div className="stack">
          <label className="check">
            <input type="checkbox" checked={adult} onChange={(e) => setAdult(e.target.checked)} />
            <span>{t("web.content.confirm_adult")}</span>
          </label>
          {status.needs_password ? (
            <label className="field">
              <span>{t("web.content.password")}</span>
              <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </label>
          ) : null}
          <div>
            <button className="primary" disabled={!adult || (status.needs_password && !password) || unlock.isPending} onClick={() => unlock.mutate({ confirm_adult: adult, password }, { onSuccess: () => setPassword("") })}>
              {t("web.content.unlock")}
            </button>
          </div>
        </div>
      )}
      {status.vault_ready ? null : <p className="error-box small">{t("web.content.vault_missing")}</p>}
      <ErrorNote error={unlock.error ?? lock.error} />
      <p className="muted small">{t("web.content.hint")}</p>
    </section>
  );
}
