import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import { useUpdate, useUpdateActions } from "../api/hooks";
import type { UpdateCommit } from "../api/types";
import { useT } from "../i18n";
import { ErrorNote } from "./ErrorNote";

const STEP_TONE: Record<string, string> = { ok: "completed", failed: "failed", running: "running", pending: "queued", skipped: "stale" };
const UNANSWERED_SEC = 20;

const when = (sec: number | null | undefined) => (sec ? new Date(sec * 1000).toLocaleString() : "");
const label = (c: UpdateCommit | null | undefined) => (c ? `${c.short} ${c.subject}` : "");

/** Update this checkout and hot-restart worker and server: version, what waits, what a restart interrupts, the steps. */
export function UpdatePanel() {
  const t = useT();
  const q = useUpdate();
  const act = useUpdateActions();
  const [interrupt, setInterrupt] = useState(false);
  const [leaving, setLeaving] = useState<string | null>(null); // the boot that is going away
  const d = q.data;
  useEffect(() => {
    if (d && d.restart.phase !== "none") setLeaving(d.boot_id);
  }, [d]);
  const refused = q.error instanceof ApiError && q.error.status === 403;
  const restarted = leaving !== null && d !== undefined && d.boot_id !== leaving;
  const reconnecting = leaving !== null && !restarted && q.isError && !(q.error instanceof ApiError);
  const mutationError = act.check.error ?? act.run.error ?? act.restart.error ?? act.cancel.error;
  const busy = act.check.isPending || act.run.isPending || act.restart.isPending || act.cancel.isPending;

  if (refused) return <p className="muted">{t("web.update.admin_only")}</p>;
  if (!d) return <ErrorNote error={q.error} />;

  const running = d.work.jobs.length + d.work.runs.length;
  const newest = d.behind[0];
  const run = d.run;
  const phase = d.restart.phase;
  const inFlight = d.busy || phase !== "none";
  const canRun = d.can_update && !inFlight && d.behind_count > 0;
  const confirmInterrupt = () => !interrupt || running === 0 || window.confirm(t("web.update.confirm_interrupt", { count: running }));
  const start = () => {
    const restart = d.restart.supervised;
    if (!window.confirm(t("web.update.confirm_run", { commit: label(newest) }))) return;
    if (restart && !confirmInterrupt()) return;
    act.run.mutate({ restart, interrupt: restart && interrupt });
  };
  const restartOnly = () => {
    if (window.confirm(t("web.update.confirm_restart")) && confirmInterrupt()) act.restart.mutate(interrupt);
  };
  const interruptNow = () => {
    if (window.confirm(t("web.update.confirm_interrupt", { count: running }))) act.restart.mutate(true);
  };
  const unanswered = phase === "requested" && d.restart.requested_at !== null && d.now - d.restart.requested_at > UNANSWERED_SEC;

  return (
    <section className="stack" aria-label={t("web.update.title")}>
      <h2>{t("web.update.title")}</h2>
      <p className="muted">{t("web.update.subtitle")}</p>
      <ErrorNote error={mutationError} />
      {reconnecting ? (
        <div className="banner banner-warn" role="status">
          {t("web.update.reconnecting")}
        </div>
      ) : null}
      {restarted ? (
        <div className="banner banner-ok" role="status">
          <strong>{t("web.update.restarted", { commit: label(d.current) })}</strong>
          <p className="muted small">{t("web.update.reload_note")}</p>
          <div className="row">
            <button className="primary" onClick={() => window.location.reload()}>
              {t("web.update.reload")}
            </button>
            <button onClick={() => setLeaving(null)}>{t("web.close")}</button>
          </div>
        </div>
      ) : null}
      {!d.enabled ? (
        <div className="banner banner-warn" role="status">
          {t("web.update.disabled", { reason: d.reason_label })}
        </div>
      ) : null}

      <dl className="value-row">
        <dt>{t("web.update.current")}</dt>
        <dd>
          <span className="strong">{label(d.current)}</span> <span className="muted small">{t("web.update.branch", { branch: d.branch })}</span>
        </dd>
      </dl>
      <div className="row">
        <span className={`chip chip-${d.behind_count > 0 ? "running" : "completed"}`}>
          {d.behind_count > 0 ? t("web.update.behind", { count: d.behind_count }) : t("web.update.up_to_date")}
        </span>
        <span className="muted small">{d.checked_at ? t("web.update.checked_at", { time: when(d.checked_at) }) : t("web.update.never_checked")}</span>
        <button onClick={() => act.check.mutate()} disabled={busy || inFlight || !d.enabled}>
          {act.check.isPending ? t("web.update.checking") : t("web.update.check")}
        </button>
      </div>
      {d.behind.length > 0 ? (
        <>
          <h3>{t("web.update.pending_commits")}</h3>
          <ul className="issue-list">
            {d.behind.map((c) => (
              <li key={c.sha}>
                <span className="strong">{c.short}</span> <span className="muted small">{c.subject}</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {!d.clean ? (
        <p className="error-box" role="alert">
          {t("web.update.dirty")}
          <br />
          <span className="small">{d.dirty.join(" ")}</span>
        </p>
      ) : null}
      {d.ahead_count > 0 && d.behind_count > 0 ? <p className="error-box">{t("web.update.diverged", { count: d.ahead_count })}</p> : null}
      {d.tools_missing.length > 0 ? <p className="error-box">{t("web.update.tools_missing", { tools: d.tools_missing.join(", ") })}</p> : null}
      <p className={d.restart.supervised ? "muted small" : "banner banner-warn"}>
        {d.restart.supervised ? t("web.update.supervised") : t("web.update.unsupervised")}
      </p>

      <h3>{t("web.update.work.title")}</h3>
      {running === 0 ? (
        <p className="muted">{t("web.update.work.none")}</p>
      ) : (
        <>
          <p>{t("web.update.work.summary", { jobs: d.work.jobs.length, runs: d.work.runs.length, heavy: d.work.heavy, queued: d.work.queued })}</p>
          <ul className="issue-list">
            {d.work.jobs.map((j) => (
              <li key={j.id}>
                <span className="muted small">{t("web.update.work.job", { kind: j.kind, model: j.model })}</span>
              </li>
            ))}
            {d.work.runs.map((r) => (
              <li key={r.id}>
                <span className="muted small">{t("web.update.work.run", { project: r.project_id })}</span>
              </li>
            ))}
          </ul>
          <p className="muted small">{t("web.update.work.wait_note")}</p>
        </>
      )}
      {d.restart.supervised && running > 0 && phase === "none" ? (
        <label className="row">
          <input type="checkbox" checked={interrupt} onChange={(e) => setInterrupt(e.target.checked)} />
          <span>{t("web.update.interrupt")}</span>
        </label>
      ) : null}
      {interrupt && running > 0 ? <p className="muted small">{t("web.update.interrupt_note")}</p> : null}

      {phase !== "none" ? (
        <div className="banner banner-warn" role="status">
          <strong>{unanswered ? t("web.update.phase.unanswered") : t(`web.update.phase.${phase}`, { count: d.restart.running })}</strong>
          {phase !== "restarting" ? (
            <div className="row">
              {phase === "draining" && d.restart.mode === "wait" ? (
                <button onClick={interruptNow} disabled={busy}>
                  {t("web.update.interrupt_now")}
                </button>
              ) : null}
              <button onClick={() => act.cancel.mutate()} disabled={busy}>
                {t("web.update.cancel_restart")}
              </button>
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="row">
        <button className="primary" onClick={start} disabled={busy || !canRun}>
          {d.busy ? t("web.update.running") : d.restart.supervised ? t("web.update.run") : t("web.update.run_only")}
        </button>
        {d.restart.supervised ? (
          <button onClick={restartOnly} disabled={busy || inFlight || !d.enabled}>
            {t("web.update.restart_only")}
          </button>
        ) : null}
      </div>

      {run ? (
        <>
          <h3>{t("web.update.steps")}</h3>
          <div className="row">
            <span className={`chip chip-${run.status === "succeeded" || run.status === "current" ? "completed" : run.status === "running" ? "running" : "failed"}`}>
              {run.label}
            </span>
            <span className="muted small">{t("web.update.run_by", { by: run.by || "-", time: when(run.started_at) })}</span>
            {run.before && run.after ? <span className="muted small">{t("web.update.from_to", { from: run.before.short, to: run.after.short })}</span> : null}
          </div>
          <div className="table-wrap">
            <table>
              <tbody>
                {run.steps.map((s) => (
                  <tr key={s.id}>
                    <td className="strong">{s.label}</td>
                    <td>
                      <span className={`chip chip-${STEP_TONE[s.status] ?? "stale"}`}>{s.status_label}</span>
                    </td>
                    <td className="muted small">
                      {s.note}
                      {s.detail ? <pre className="transcript">{s.detail}</pre> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {run.status === "failed" ? (
            <p className="error-box" role="alert">
              {t("web.update.failed", { step: run.steps.find((s) => s.id === run.failed_step)?.label ?? run.failed_step })}
            </p>
          ) : null}
          {run.status === "current" ? <p className="muted">{t("web.update.current_done")}</p> : null}
          {run.status === "succeeded" && run.restart === "unsupervised" ? <p className="banner banner-warn">{t("web.update.succeeded_manual")}</p> : null}
        </>
      ) : null}
      {d.log_tail.length > 0 ? (
        <>
          <h3>{t("web.update.log")}</h3>
          <pre className="transcript">{d.log_tail.join("\n")}</pre>
          {run?.log_file ? <p className="muted small">{t("web.update.log_file", { path: run.log_file })}</p> : null}
        </>
      ) : null}
    </section>
  );
}
