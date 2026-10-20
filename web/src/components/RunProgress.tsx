import type { WorkflowRun } from "../api/types";
import { duration } from "../lib/format";
import { useT } from "../i18n";
import { ProgressBar } from "./ProgressBar";
import { StatusChip } from "./StatusChip";

/**
 * Everything about a run's progress in one place: the overall bar with its percentage, the step and what is
 * being done now, elapsed time and the estimate, and one bar per stage.
 */
export function RunProgress({ run }: { run: WorkflowRun }) {
  const t = useT();
  const p = run.progress;
  const live = run.status === "running";
  const queued = run.status === "queued";
  return (
    <section className="run" aria-live="polite">
      <header className="run-head">
        <StatusChip status={run.status} />
        <span className="run-percent">{queued ? "0%" : `${p.percent.toFixed(1)}%`}</span>
      </header>
      <ProgressBar
        size="lg"
        percent={queued ? 0 : p.percent}
        tone={run.status === "completed" ? "completed" : run.status === "failed" ? "failed" : "running"}
        label={t("web.workflow.overall")}
      />
      <p className="run-now">
        {queued ? (
          t("workflow.progress.queued")
        ) : (
          <>
            {p.stage ? t("workflow.progress.stage_of", { index: p.stage_index, count: p.stage_count }) : t("workflow.progress.working")}
            {p.stage ? ` · ${t(`workflow.stage.${p.stage}`)}` : ""}
            {p.total > 0 ? ` · ${t("workflow.progress.items", { done: p.done, total: p.total })}` : ""}
            {p.message ? ` · ${p.message}` : ""}
          </>
        )}
      </p>
      {!queued && (
        <p className="run-time">
          {t("workflow.progress.elapsed", { time: duration(p.elapsed_sec) })}
          {live ? ` · ${p.eta_sec == null ? t("workflow.progress.eta_unknown") : t("workflow.progress.eta", { time: duration(p.eta_sec) })}` : ""}
        </p>
      )}
      <ol className="stages">
        {p.stages.map((s) => (
          <li key={s.stage} className="stage-row">
            <span className="stage-name">{t(`workflow.stage.${s.stage}`)}</span>
            <ProgressBar
              percent={s.status === "running" ? s.percent : s.status === "completed" ? 100 : s.percent ?? 0}
              tone={s.status}
              label={t(`workflow.stage.${s.stage}`)}
            />
            <span className="stage-info">
              <StatusChip status={s.status} />
              {s.seconds != null ? <span className="muted"> {duration(s.seconds)}</span> : null}
            </span>
          </li>
        ))}
      </ol>
      {run.status === "failed" && run.error_code ? <RunError code={run.error_code} message={run.error_message} /> : null}
    </section>
  );
}

/** ``llm.unreachable`` -> ``llm.error.unreachable`` (+ ``.remediation``): what happened and what to do next. */
export function RunError({ code, message }: { code: string; message?: string | null }) {
  const t = useT();
  const key = code.includes(".") ? code.replace(".", ".error.") : `error.${code}`;
  const text = t(key);
  const known = text !== key;
  return (
    <div className="error-box" role="alert">
      <strong>{known ? text : t("web.error.generic")}</strong>
      {known ? <p>{t(`${key}.remediation`)}</p> : null}
      {message ? <p className="muted">{message}</p> : null}
    </div>
  );
}
