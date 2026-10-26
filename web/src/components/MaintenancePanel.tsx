import { useState } from "react";
import { useProjects } from "../api/hooks";
import { useMaintainActions, type PruneHistoryResult, type PruneOrphansResult } from "../api/maintain";
import { useT } from "../i18n";
import { ErrorNote } from "./ErrorNote";

/** Maintenance card: prune orphaned shot specs/assets and old history logs, preview first, then delete. */
export function MaintenancePanel() {
  const t = useT();
  const projects = useProjects();
  const act = useMaintainActions();
  const [projectId, setProjectId] = useState<number | null>(null);
  const [days, setDays] = useState(30);
  const [orphans, setOrphans] = useState<PruneOrphansResult | null>(null);
  const [history, setHistory] = useState<PruneHistoryResult | null>(null);
  const busy = act.pruneOrphans.isPending || act.pruneHistory.isPending;
  const first = projects.data?.[0]?.id ?? null;
  const pid = projectId ?? first;
  const previewOrphans = () => {
    if (pid === null) return;
    act.pruneOrphans.mutate({ projectId: pid, dryRun: true }, { onSuccess: setOrphans });
  };
  const runOrphans = () => {
    if (pid === null) return;
    if (!window.confirm(t("web.maintain.confirm_orphans"))) return;
    act.pruneOrphans.mutate({ projectId: pid, dryRun: false }, { onSuccess: setOrphans });
  };
  const previewHistory = () => act.pruneHistory.mutate({ days, dryRun: true }, { onSuccess: setHistory });
  const runHistory = () => {
    if (!window.confirm(t("web.maintain.confirm_history", { days }))) return;
    act.pruneHistory.mutate({ days, dryRun: false }, { onSuccess: setHistory });
  };
  return (
    <section className="card stack">
      <h2>{t("web.maintain.title")}</h2>
      <p className="muted small">{t("web.maintain.hint")}</p>
      <ErrorNote error={act.pruneOrphans.error ?? act.pruneHistory.error} />

      <div className="stack">
        <strong>{t("web.maintain.orphans")}</strong>
        <label className="row small">
          <span>{t("web.maintain.project")}</span>
          <select value={pid ?? ""} onChange={(e) => setProjectId(Number(e.target.value))} disabled={busy}>
            {(projects.data ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <span className="spacer" />
          <button className="link" onClick={previewOrphans} disabled={busy || pid === null}>
            {t("web.maintain.preview")}
          </button>
          <button className="link" onClick={runOrphans} disabled={busy || pid === null}>
            {t("web.maintain.run")}
          </button>
        </label>
        {orphans ? (
          <p className="muted small">{t("web.maintain.orphans_result", { specs: orphans.specs, assets: orphans.assets })}</p>
        ) : null}
      </div>

      <div className="stack">
        <strong>{t("web.maintain.history")}</strong>
        <label className="row small">
          <span>{t("web.maintain.days")}</span>
          <input type="number" min={1} value={days} onChange={(e) => setDays(Math.max(1, Number(e.target.value) || 1))} disabled={busy} />
          <span className="spacer" />
          <button className="link" onClick={previewHistory} disabled={busy}>
            {t("web.maintain.preview")}
          </button>
          <button className="link" onClick={runHistory} disabled={busy}>
            {t("web.maintain.run")}
          </button>
        </label>
        {history ? (
          <p className="muted small">
            {t("web.maintain.history_result", {
              days: history.days,
              total: history.total,
              audit: history.deleted["audit_log"] ?? 0,
              gen: history.deleted["generation_logs"] ?? 0,
              events: history.deleted["job_events"] ?? 0,
            })}
          </p>
        ) : null}
      </div>
    </section>
  );
}
