import { Link } from "react-router-dom";
import { useAllRuns, useCancelAnyRun } from "../api/hooks";
import type { WorkflowRunRow } from "../api/types";
import { useT } from "../i18n";
import { duration } from "../lib/format";
import { ErrorNote } from "./ErrorNote";
import { ProgressBar } from "./ProgressBar";
import { stageMessage } from "./RunProgress";
import { StatusChip } from "./StatusChip";

function RunLine({ run }: { run: WorkflowRunRow }) {
  const t = useT();
  const cancel = useCancelAnyRun();
  const p = run.progress;
  const active = run.status === "queued" || run.status === "running";
  const now = run.status === "queued" ? t("workflow.progress.queued") : p.stage ? `${t(`workflow.stage.${p.stage}`)}${p.total > 0 ? ` · ${t("workflow.progress.items", { done: p.done, total: p.total })}` : ""}${p.message ? ` · ${stageMessage(t, p.message)}` : ""}` : "";
  return (
    <tr>
      <td className="strong">
        <Link to={`/projects/${run.project_id}/workflow`}>{run.project_name}</Link>
      </td>
      <td className="small">{run.stages.map((s) => t(`workflow.stage.${s}`)).join(" → ")}</td>
      <td className="progress-cell">
        <ProgressBar percent={run.status === "queued" ? 0 : p.percent} tone={run.status === "completed" ? "completed" : run.status === "failed" ? "failed" : run.status === "queued" ? "queued" : "running"} label={now} />
        <span className="muted small">{now}</span>
      </td>
      <td>
        <StatusChip status={run.status} />
        {run.status === "failed" && run.error_code ? <div className="muted small">{run.error_message ?? run.error_code}</div> : null}
      </td>
      <td className="muted small">
        {run.status === "running" ? `${duration(p.elapsed_sec)}${p.eta_sec != null ? ` · ${t("workflow.progress.eta", { time: duration(p.eta_sec) })}` : ""}` : run.status === "queued" ? "" : duration(p.elapsed_sec)}
      </td>
      <td className="actions">
        {active ? (
          <button type="button" className="link" disabled={cancel.isPending} onClick={() => cancel.mutate(run.id)}>
            {t("web.jobs.cancel")}
          </button>
        ) : null}
        <ErrorNote error={cancel.error} />
      </td>
    </tr>
  );
}

/** The story runs of every project, next to the media jobs: a run is not a media job, so it has its own list. */
export function StoryRuns() {
  const t = useT();
  const runs = useAllRuns();
  return (
    <section className="stack" aria-label={t("web.queue.story")}>
      <h2>{t("web.queue.story")}</h2>
      <p className="muted small">{t("web.queue.story_hint")}</p>
      <ErrorNote error={runs.error} />
      {runs.data && runs.data.length === 0 ? <p className="empty">{t("web.queue.story_empty")}</p> : null}
      {runs.data && runs.data.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("web.queue.project")}</th>
                <th>{t("web.queue.steps")}</th>
                <th>{t("web.jobs.progress")}</th>
                <th>{t("web.jobs.status")}</th>
                <th>{t("web.jobs.took")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {runs.data.map((r) => (
                <RunLine key={r.id} run={r} />
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
