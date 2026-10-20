import { ApiError } from "../api/client";
import { useT } from "../i18n";

/** The server already localises `message` and `remediation`: show what happened and what to do next. */
export function ErrorNote({ error }: { error: unknown }) {
  const t = useT();
  if (!error) return null;
  if (error instanceof ApiError) {
    return (
      <div className="error-box" role="alert">
        <strong>{error.message}</strong>
        {error.remediation ? <p>{error.remediation}</p> : null}
      </div>
    );
  }
  return (
    <div className="error-box" role="alert">
      <strong>{t("web.error.network")}</strong>
      <p>{t("web.error.network_hint")}</p>
    </div>
  );
}
