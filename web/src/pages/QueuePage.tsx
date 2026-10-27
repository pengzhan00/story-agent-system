import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
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
import { progressText } from "../i18n";
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

function JobRow({ job, siblings, selected, onSelect, selectingDisabled }: {
  job: Job; siblings: string[]; selected: boolean; onSelect: () => void; selectingDisabled: boolean;
}) {
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
        <input type="checkbox" aria-label={t("web.jobs.select_job", { id: job.id.slice(0, 8) })}
          checked={selected} disabled={active || selectingDisabled} onChange={onSelect} />
      </td>
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
        <ProgressBar percent={job.status === "queued" ? 0 : job.status === "running" ? job.progress * 100 : job.status === "completed" ? 100 : job.progress * 100} tone={tone} label={progressText(t, job.progress_msg)} />
        <span className="muted small">{progressText(t, job.progress_msg)}</span>
      </td>
      <td>
        <StatusChip status={job.status} group="status" />
        {job.error_code ? <div className="muted small">{jobError(t, job).text}</div> : null}
        {job.error_code && job.spec_id && job.project_id != null ? (
          <div className="small">
            <Link to={`/projects/${job.project_id}/creative?spec=${encodeURIComponent(job.spec_id)}`}>{t("web.jobs.fix_spec")}</Link>
          </div>
        ) : null}
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
        <td colSpan={8}>
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
  const PAGE = 100;
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const { removeHistory } = useJobActions();
  const jobs = useJobs(projectId, [...FILTERS[filter]], PAGE, page * PAGE);
  const eligible = (jobs.data ?? []).filter((j) => !ACTIVE.has(j.status));
  const chosen = eligible.filter((j) => selected.has(j.id)).map((j) => j.id);
  const allSelected = eligible.length > 0 && chosen.length === eligible.length;
  const toggle = (id: string) => setSelected((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const changePage = (next: number) => { setPage(next); setSelected(new Set()); };
  const more = (jobs.data?.length ?? 0) >= PAGE;
  const queue = useQueueState(projectId);
  const count = (f: Filter) => (f === "running" ? queue.data?.running : f === "waiting" ? queue.data?.queued : undefined);
  return (
    <div className="stack">
      <nav className="tabs" aria-label={t("web.jobs.filter")}>
        {(Object.keys(FILTERS) as Filter[]).map((f) => (
          <button key={f} className={f === filter ? "tab active" : "tab"} aria-pressed={f === filter} disabled={removeHistory.isPending} onClick={() => { setFilter(f); changePage(0); }}>
            {t(`web.jobs.filter.${f}`)}
            {count(f) !== undefined ? ` (${count(f)})` : ""}
          </button>
        ))}
      </nav>
      <ErrorNote error={jobs.error} />
      <ErrorNote error={removeHistory.error} />
      {jobs.data && jobs.data.length > 0 ? (
        <div className="row small">
          <span>{t("web.jobs.selected_n", { n: chosen.length })}</span>
          <button disabled={!chosen.length || removeHistory.isPending} onClick={() => {
            if (window.confirm(t("web.jobs.remove_confirm", { n: chosen.length }))) {
              const emptyPage = chosen.length === jobs.data?.length;
              removeHistory.mutate(chosen, { onSuccess: () => {
                setSelected(new Set());
                if (emptyPage && page > 0) setPage((p) => p - 1);
              } });
            }
          }}>{t("web.jobs.remove_selected")}</button>
          <button disabled={!chosen.length || removeHistory.isPending} onClick={() => setSelected(new Set())}>
            {t("web.jobs.clear_selection")}
          </button>
          <span className="muted">{t("web.jobs.remove_hint")}</span>
        </div>
      ) : null}
      {jobs.data && jobs.data.length === 0 ? <p className="empty">{t("web.jobs.empty")}</p> : null}
      {jobs.data && jobs.data.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th><input type="checkbox" aria-label={t("web.jobs.select_page")} checked={allSelected}
                  ref={(el) => { if (el) el.indeterminate = chosen.length > 0 && !allSelected; }}
                  disabled={!eligible.length || removeHistory.isPending}
                  onChange={() => setSelected(allSelected ? new Set() : new Set(eligible.map((j) => j.id)))} /></th>
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
                  selected={chosen.includes(j.id)}
                  onSelect={() => toggle(j.id)}
                  selectingDisabled={removeHistory.isPending}
                  siblings={jobs.data.filter((o) => o.status === "queued" && o.kind === j.kind && o.project_id === j.project_id).map((o) => o.id)}
                />
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {more || page > 0 ? (
        <div className="row small">
          <button disabled={page === 0 || removeHistory.isPending} onClick={() => changePage(Math.max(0, page - 1))}>
            {t("web.jobs.prev_page")}
          </button>
          <span className="muted">{t("web.jobs.page_n", { n: page + 1 })}</span>
          <button disabled={!more || removeHistory.isPending} onClick={() => changePage(page + 1)}>
            {t("web.jobs.next_page")}
          </button>
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
