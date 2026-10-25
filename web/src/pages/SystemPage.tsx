import { useHealth } from "../api/hooks";
import { ErrorNote } from "../components/ErrorNote";
import { UpdatePanel } from "../components/UpdatePanel";
import { useT } from "../i18n";

export function SystemPage() {
  const t = useT();
  const health = useHealth();
  return (
    <div className="stack">
      <h1>{t("web.nav.system")}</h1>
      <ErrorNote error={health.error} />
      {health.isLoading ? <p className="muted">{t("web.loading")}</p> : null}
      {health.data ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("web.system.component")}</th>
                <th>{t("web.system.state")}</th>
                <th>{t("web.system.detail")}</th>
              </tr>
            </thead>
            <tbody>
              {health.data.entries.map((e) => (
                <tr key={e.name}>
                  <td className="strong">{e.name}</td>
                  <td>
                    <span className={`chip chip-${e.ok ? "completed" : e.required ? "failed" : "stale"}`}>{e.label}</span>
                  </td>
                  <td className="muted small">{e.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <button onClick={() => health.refetch()} disabled={health.isFetching}>
        {t("web.system.recheck")}
      </button>
      <UpdatePanel />
    </div>
  );
}
