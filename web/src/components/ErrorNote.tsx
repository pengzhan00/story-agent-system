import { ApiError } from "../api/client";
import { useT } from "../i18n";
import { Findings, refusedFor } from "./Findings";

/**
 * The server already localises `message` and `remediation`: show what happened and what to do next. A run that was
 * refused because something it needs is missing (`not_ready`) lists each thing, wherever the run was started from.
 */
export function ErrorNote({ error }: { error: unknown }) {
  const t = useT();
  if (!error) return null;
  if (error instanceof ApiError) {
    const refused = refusedFor(error);
    return (
      <div className="error-box" role="alert">
        <strong>{error.message}</strong>
        {error.remediation ? <p>{error.remediation}</p> : null}
        {refused.length > 0 ? <Findings items={refused} /> : null}
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
