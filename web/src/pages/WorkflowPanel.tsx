import { useState } from "react";
import { useCancelRun, useProject, useStartRun, useUpdateProject, useWorkflow } from "../api/hooks";
import { ErrorNote } from "../components/ErrorNote";
import { ProgressBar } from "../components/ProgressBar";
import { RunError, RunProgress } from "../components/RunProgress";
import { StatusChip } from "../components/StatusChip";
import { duration } from "../lib/format";
import { useT } from "../i18n";

export function WorkflowPanel({ id }: { id: number }) {
  const t = useT();
  const project = useProject(id);
  const wf = useWorkflow(id);
  const start = useStartRun(id);
  const cancel = useCancelRun(id);
  const update = useUpdateProject(id);
  const [premise, setPremise] = useState<string | null>(null);
  const [force, setForce] = useState(false);

  const active = wf.data?.active_run ?? null;
  const last = wf.data?.recent_runs.find((r) => r.status !== "queued" && r.status !== "running") ?? null;
  const allDone = wf.data?.stages.every((s) => s.status === "completed") ?? false;
  const text = premise ?? project.data?.description ?? "";
  const dirty = premise !== null && premise !== (project.data?.description ?? "");

  return (
    <div className="stack">
      <section className="card stack">
        <label className="field">
          <span>{t("web.workflow.premise")}</span>
          <textarea value={text} onChange={(e) => setPremise(e.target.value)} rows={3} />
        </label>
        <div className="row">
          <button
            disabled={!dirty || update.isPending}
            onClick={() => update.mutate({ description: text }, { onSuccess: () => setPremise(null) })}
          >
            {t("web.save")}
          </button>
          <span className="spacer" />
          <label className="check">
            <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
            <span>{t("web.workflow.force")}</span>
          </label>
          {active ? (
            <button className="danger" disabled={cancel.isPending || active.cancel_requested} onClick={() => cancel.mutate(active.id)}>
              {active.cancel_requested ? t("web.workflow.cancelling") : t("web.workflow.cancel")}
            </button>
          ) : (
            <button
              className="primary"
              disabled={start.isPending || !text.trim() || dirty || (allDone && !force)}
              onClick={() =>
                start.mutate({
                  stages: force ? wf.data?.stages.map((s) => s.stage) : undefined,
                  params: force ? { force: true } : {},
                })
              }
            >
              {allDone ? t("web.workflow.rerun") : wf.data?.stages.some((s) => s.status !== "pending") ? t("web.workflow.resume") : t("web.workflow.start")}
            </button>
          )}
        </div>
        {dirty ? <p className="hint">{t("web.workflow.save_first")}</p> : null}
        <ErrorNote error={start.error ?? cancel.error ?? update.error ?? wf.error} />
      </section>

      {active ? <RunProgress run={active} /> : null}

      <section className="card stack">
        <h2>{t("web.workflow.stages")}</h2>
        {!active && last && last.status !== "completed" ? (
          <div>
            <StatusChip status={last.status} />
            {last.error_code ? <RunError code={last.error_code} message={last.error_message} /> : null}
          </div>
        ) : null}
        <ul className="stage-list">
          {wf.data?.stages.map((s) => (
            <li key={s.stage} className="stage-row">
              <span className="stage-name">{t(`workflow.stage.${s.stage}`)}</span>
              <ProgressBar percent={s.status === "completed" ? 100 : 0} tone={s.status} label={t(`workflow.stage.${s.stage}`)} />
              <span className="stage-info">
                <StatusChip status={s.status} />
                {s.status === "failed" && s.error_code ? <span className="muted small"> {s.error_code}</span> : null}
              </span>
              <button
                className="link"
                disabled={!!active || start.isPending || !text.trim()}
                onClick={() => start.mutate({ stages: [s.stage], params: force ? { force: true } : {} })}
              >
                {t("web.workflow.run_stage")}
              </button>
            </li>
          ))}
        </ul>
        {allDone && last?.progress ? (
          <p className="muted small">{t("web.workflow.last_run", { time: duration(last.progress.elapsed_sec) })}</p>
        ) : null}
      </section>
    </div>
  );
}
