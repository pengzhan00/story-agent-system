import { Fragment, useState } from "react";
import { ValueView } from "../components/ValueView";
import { formatValue, useLabel } from "../lib/labels";
import { useJobActions, useJobEvents, useJobs, useQueueState } from "../api/hooks";
import type { Job } from "../api/types";
import { ErrorNote } from "../components/ErrorNote";
import { JobId } from "../components/JobId";
import { ProgressBar } from "../components/ProgressBar";
import { StatusChip } from "../components/StatusChip";
import { duration } from "../lib/format";
import { jobError } from "../lib/jobError";
import { StoryRuns } from "../components/StoryRuns";
import { useT } from "../i18n";

const ACTIVE = new Set(["queued", "running"]);
/** What each filter chip asks the server for; the chip's count (where it has one) is the size of that list. */
const FILTERS = {
  all: [],
  running: ["running"],
  waiting: ["queued"],
  problems: ["workflow_error", "transport_error", "timeout", "failed_validation", "cancelled"],
} as const;
type Filter = keyof typeof FILTERS;

function JobDetail({ job }: { job: Job }) {
  const t = useT();
  const events = useJobEvents(job.id);
  const paramName = useLabel("param");
  const eventLabel = useLabel("jobevent");
  const eventName = (n: string) => eventLabel(n);
  return (
    <div className="stack">
      <div className="row small">
        <span>{`${t("web.jobs.d.model")}: ${job.model_ref} (${job.provider})`}</span>
        <span>{`${t("web.jobs.d.tier")}: ${t(`media.tier.${job.tier}`)}`}</span>
        <span>{`${t("web.jobs.d.seed")}: ${job.seed ?? "-"}`}</span>
        {job.spec_id ? <span>{`${t("web.jobs.d.spec")}: ${job.spec_id.slice(0, 8)} v${job.spec_version ?? ""}`}</span> : null}
      </div>
      {job.error_message ? (
        <div className="stack">
          <strong className="small bad">{t("web.jobs.d.error")}</strong>
          <pre className="transcript">{job.error_message}</pre>
        </div>
      ) : null}
      <strong className="small">{t("web.jobs.d.inputs")}</strong>
      <div className="transcript"><ValueView value={job.inputs} prefix="input" /></div>
      <strong className="small">{t("web.jobs.d.params")}</strong>
      <div className="transcript"><ValueView value={job.params} prefix="param" /></div>
      <strong className="small">{t("web.jobs.d.events")}</strong>
      <ul className="plain small">
        {events.data?.map((e) => (
          <li key={e.id} className="mono">{`${e.ts.slice(11, 19)} ${eventName(e.event)} ${Object.keys(e.data ?? {}).length ? formatValue(e.data, t, paramName).slice(0, 160) : ""}`}</li>
        ))}
      </ul>
    </div>
  );
}

function JobRow({ job, siblings }: { job: Job; siblings: string[] }) {
  const [open, setOpen] = useState(false);
  const t = useT();
  const { cancel, retry, front } = useJobActions();
  const active = ACTIVE.has(job.status);
  const retries = job.retries ?? 0;
  const tone = job.status === "completed" ? "completed" : job.status === "running" ? "running" : job.status === "queued" ? "queued" : "failed";
  return (
    <Fragment>
    <tr>
      <td>
        <JobId id={job.id} />
        {job.parent_job_id ? (
          <div className="muted small">
            {t("web.jobs.retry_of")} <JobId id={job.parent_job_id} />
          </div>
        ) : null}
      </td>
      <td className="strong">{t(`media.kind.${job.kind}`)}</td>
      <td>{job.model_ref}</td>
      <td className="progress-cell">
        <ProgressBar percent={job.status === "queued" ? 0 : job.status === "running" ? job.progress * 100 : job.status === "completed" ? 100 : job.progress * 100} tone={tone} label={job.progress_msg} />
        <span className="muted small">{job.progress_msg}</span>
      </td>
      <td>
        <StatusChip status={job.status} group="status" />
        {job.error_code ? <div className="muted small">{jobError(t, job).text}</div> : null}
        {retries > 0 && job.retried_by ? (
          <div className="small">
            <span className="chip chip-stale">{retries > 1 ? t("web.jobs.retried_n", { n: retries }) : t("web.jobs.retried")}</span>{" "}
            <JobId id={job.retried_by} /> {job.retried_status ? <StatusChip status={job.retried_status} group="status" /> : null}
          </div>
        ) : null}
      </td>
      <td className="muted small">{job.actual_seconds != null ? duration(job.actual_seconds) : ""}</td>
      <td className="actions">
        <button className="link" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? t("web.close") : t("web.jobs.inspect")}
        </button>
        {job.status === "queued" ? (
          <>
            <button className="link" title={t("web.jobs.top_hint")} disabled={front.isPending} onClick={() => front.mutate([job.id])}>
              {t("web.jobs.top")}
            </button>
            {siblings.length > 1 ? (
              <button className="link" title={t("web.jobs.top_kind_hint")} disabled={front.isPending} onClick={() => front.mutate(siblings)}>
                {t("web.jobs.top_kind")}
              </button>
            ) : null}
          </>
        ) : null}
        {active ? (
          <button className="link" disabled={cancel.isPending} onClick={() => cancel.mutate(job.id)}>
            {t("web.jobs.cancel")}
          </button>
        ) : job.status !== "completed" && retries === 0 ? (
          <button className="link" disabled={retry.isPending} onClick={() => retry.mutate(job.id)}>
            {t("web.jobs.retry")}
          </button>
        ) : null}
      </td>
    </tr>
    {open ? (
      <tr className="edit-row">
        <td colSpan={7}>
          <JobDetail job={job} />
        </td>
      </tr>
    ) : null}
    </Fragment>
  );
}

export function JobsPanel({ projectId }: { projectId?: number }) {
  const t = useT();
  const [filter, setFilter] = useState<Filter>("all");
  const jobs = useJobs(projectId, [...FILTERS[filter]]);
  const queue = useQueueState(projectId);
  const count = (f: Filter) => (f === "running" ? queue.data?.running : f === "waiting" ? queue.data?.queued : undefined);
  return (
    <div className="stack">
      <nav className="tabs" aria-label={t("web.jobs.filter")}>
        {(Object.keys(FILTERS) as Filter[]).map((f) => (
          <button key={f} className={f === filter ? "tab active" : "tab"} aria-pressed={f === filter} onClick={() => setFilter(f)}>
            {t(`web.jobs.filter.${f}`)}
            {count(f) !== undefined ? ` (${count(f)})` : ""}
          </button>
        ))}
      </nav>
      <ErrorNote error={jobs.error} />
      {jobs.data && jobs.data.length === 0 ? <p className="empty">{t("web.jobs.empty")}</p> : null}
      {jobs.data && jobs.data.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("web.jobs.id")}</th>
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
                <JobRow
                  key={j.id}
                  job={j}
                  siblings={jobs.data.filter((o) => o.status === "queued" && o.kind === j.kind && o.project_id === j.project_id).map((o) => o.id)}
                />
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
      <StoryRuns />
      <h2>{t("web.queue.media")}</h2>
      <JobsPanel />
    </div>
  );
}
