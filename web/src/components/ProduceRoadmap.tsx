import { useProducePlan } from "../api/hooks";
import type { PlannedTask } from "../api/types";
import { useT } from "../i18n";
import { duration } from "../lib/format";
import { ProgressBar } from "./ProgressBar";

const PHASES = [1, 2, 3];
const todo = (x: PlannedTask) => x.state === "new" || x.state === "stale";

/**
 * The whole production at a glance while it runs: every phase (portraits / scenes / music / voices, shot keyframes,
 * shot videos) with how much is done, what is left, what is blocked or left as a still, and the time that remains -
 * the phases that have not started yet are listed too, with the reason they wait.
 */
export function ProduceRoadmap({ id, tier }: { id: number; tier: string }) {
  const t = useT();
  const plan = useProducePlan(id, tier, true, 5000).data;
  if (!plan || plan.tasks.length === 0) return null;
  const rows = PHASES.map((phase) => {
    const all = plan.tasks.filter((x) => x.phase === phase);
    const counted = all.filter((x) => x.state !== "skip");
    const left = all.filter(todo);
    return {
      phase,
      total: counted.length,
      done: counted.filter((x) => x.state === "done").length,
      left: left.length,
      blocked: all.filter((x) => x.state === "blocked").length,
      stills: all.length - counted.length,
      seconds: left.reduce((n, x) => n + x.est_seconds, 0),
    };
  }).filter((r) => r.total + r.stills > 0);
  const current = rows.find((r) => r.left > 0)?.phase;
  const seconds = rows.reduce((n, r) => n + r.seconds, 0);
  const total = rows.reduce((n, r) => n + r.total, 0);
  const done = rows.reduce((n, r) => n + r.done, 0);
  return (
    <section className="card stack" aria-label={t("web.roadmap.title")}>
      <div className="row">
        <strong>{t("web.roadmap.title")}</strong>
        <span className="spacer" />
        <span className="small">{t("web.roadmap.summary", { done, total, eta: duration(seconds) })}</span>
      </div>
      <ol className="stages">
        {rows.map((r) => {
          const state = r.left === 0 ? "completed" : r.phase === current ? "running" : "queued";
          return (
            <li key={r.phase} className="stage-row">
              <span className="stage-name">{t(`web.plan.phase.${r.phase}`)}</span>
              <ProgressBar percent={r.total ? (r.done / r.total) * 100 : 100} tone={state} label={t(`web.plan.phase.${r.phase}`)} />
              <span className="stage-info small">
                {`${r.done}/${r.total}`}
                <span className="muted">
                  {r.left > 0 ? ` · ${r.phase === current ? t("web.roadmap.now") : t("web.roadmap.waiting")} · ~${duration(r.seconds)}` : ` · ${t("web.roadmap.done")}`}
                  {r.blocked > 0 ? ` · ${t("web.roadmap.blocked", { n: r.blocked })}` : ""}
                  {r.stills > 0 ? ` · ${t("web.roadmap.stills", { n: r.stills })}` : ""}
                </span>
              </span>
            </li>
          );
        })}
      </ol>
      <p className="muted small">{t("web.roadmap.hint")}</p>
    </section>
  );
}
