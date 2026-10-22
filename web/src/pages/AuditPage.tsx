import { useState } from "react";
import { useAudit } from "../api/auth";
import { ErrorNote } from "../components/ErrorNote";
import { useT } from "../i18n";

/** Who did what, when (administrators only): every write is recorded with the user, the path, the project and the result. */
export function AuditPage() {
  const t = useT();
  const [filter, setFilter] = useState({ user: "", action: "", project_id: "" });
  const rows = useAudit(filter);
  return (
    <div className="stack">
      <h1>{t("web.nav.audit")}</h1>
      <div className="form-grid">
        <label className="field">
          <span>{t("web.audit.user")}</span>
          <input value={filter.user} onChange={(e) => setFilter({ ...filter, user: e.target.value })} />
        </label>
        <label className="field">
          <span>{t("web.audit.action")}</span>
          <input value={filter.action} onChange={(e) => setFilter({ ...filter, action: e.target.value })} placeholder="http.POST" />
        </label>
        <label className="field">
          <span>{t("web.audit.project")}</span>
          <input value={filter.project_id} onChange={(e) => setFilter({ ...filter, project_id: e.target.value.replace(/\D/g, "") })} />
        </label>
      </div>
      <ErrorNote error={rows.error} />
      {rows.isLoading ? <p className="muted">{t("web.loading")}</p> : null}
      {rows.data && rows.data.length === 0 ? <p className="empty">{t("web.audit.empty")}</p> : null}
      {rows.data && rows.data.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("web.audit.at")}</th>
                <th>{t("web.audit.user")}</th>
                <th>{t("web.audit.action")}</th>
                <th>{t("web.audit.path")}</th>
                <th>{t("web.audit.project")}</th>
                <th>{t("web.audit.status")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.data.map((r) => (
                <tr key={r.id}>
                  <td className="muted small">{r.at}</td>
                  <td className="strong">{r.actor || "-"}</td>
                  <td>{r.action}</td>
                  <td className="small">{r.path || "-"}</td>
                  <td>{r.project_id ?? "-"}</td>
                  <td>
                    <span className={`chip chip-${r.status && r.status < 400 ? "completed" : "failed"}`}>{r.status ?? "-"}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
