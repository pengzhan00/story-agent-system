import { useJobActions, useJobs, useQueueState } from "../api/hooks";
import type { Job } from "../api/types";
import { ErrorNote } from "../components/ErrorNote";
import { ProgressBar } from "../components/ProgressBar";
import { StatusChip } from "../components/StatusChip";
import { duration } from "../lib/format";
import { useT } from "../i18n";

const ACTIVE = new Set(["queued", "running"]);

function JobRow({ job }: { job: Job }) {
  const t = useT();
  const { cancel, retry } = useJobActions();
  const active = ACTIVE.has(job.status);
  const tone = job.status === "completed" ? "completed" : job.status === "running" ? "running" : job.status === "queued" ? "queued" : "failed";
  return (
    <tr>
      <td className="strong">{t(`media.kind.${job.kind}`)}</td>
      <td>{job.model_ref}</td>
      <td className="progress-cell">
        <ProgressBar percent={job.status === "queued" ? 0 : job.status === "running" ? job.progress * 100 : job.status === "completed" ? 100 : job.progress * 100} tone={tone} label={job.progress_msg} />
        <span className="muted small">{job.progress_msg}</span>
      </td>
      <td>
        <StatusChip status={job.status} group="status" />
        {job.error_code ? <div className="muted small">{t(`error.${job.error_code}`)}</div> : null}
      </td>
      <td className="muted small">{job.actual_seconds != null ? duration(job.actual_seconds) : ""}</td>
      <td className="actions">
        {active ? (
          <button className="link" disabled={cancel.isPending} onClick={() => cancel.mutate(job.id)}>
            {t("web.jobs.cancel")}
          </button>
        ) : job.status !== "completed" ? (
          <button className="link" disabled={retry.isPending} onClick={() => retry.mutate(job.id)}>
            {t("web.jobs.retry")}
          </button>
        ) : null}
      </td>
    </tr>
  );
}

export function JobsPanel({ projectId }: { projectId?: number }) {
  const t = useT();
  const jobs = useJobs(projectId);
  const queue = useQueueState();
  return (
    <div className="stack">
      {queue.data ? (
        <p className="muted">{t("web.jobs.summary", { running: queue.data.running, queued: queue.data.queued })}</p>
      ) : null}
      <ErrorNote error={jobs.error} />
      {jobs.data && jobs.data.length === 0 ? <p className="empty">{t("web.jobs.empty")}</p> : null}
      {jobs.data && jobs.data.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("web.jobs.kind")}</th>
                <th>{t("web.jobs.model")}</th>
                <th>{t("web.jobs.progress")}</th>
                <th>{t("web.jobs.status")}</th>
                <th>{t("web.jobs.took")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {jobs.data.map((j) => (
                <JobRow key={j.id} job={j} />
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

export function QueuePage() {
  const t = useT();
  return (
    <div className="stack">
      <h1>{t("web.nav.queue")}</h1>
      <JobsPanel />
    </div>
  );
}
