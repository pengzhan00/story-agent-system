import { useEffect, useMemo, useState } from "react";
import { useProducePlan, useStartRun } from "../api/hooks";
import type { PlannedTask } from "../api/types";
import { useT } from "../i18n";
import { duration } from "../lib/format";
import { ErrorNote } from "./ErrorNote";

const PHASES = [1, 2, 3];
const VIDEO = "shot.video";
const runnable = (t: PlannedTask) => t.state === "new" || t.state === "stale";

/**
 * The production plan: every image, voice, song and video that producing would make, with its model and an estimate.
 * Nothing runs until it is confirmed; the expensive videos start unticked.
 */
export function ProducePlan({ id, onClose }: { id: number; onClose: () => void }) {
  const t = useT();
  const [tier, setTier] = useState("draft");
  const plan = useProducePlan(id, tier, true);
  const start = useStartRun(id);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const tasks = useMemo(() => plan.data?.tasks ?? [], [plan.data]);
  useEffect(() => setPicked(new Set(tasks.filter((x) => runnable(x) && x.subject_type !== VIDEO).map((x) => x.spec_id))), [tasks]);
  const chosen = tasks.filter((x) => picked.has(x.spec_id));
  const eta = chosen.reduce((n, x) => n + x.est_seconds, 0);
  const toggle = (specId: string, on: boolean) =>
    setPicked((p) => {
      const next = new Set(p);
      if (on) next.add(specId);
      else next.delete(specId);
      return next;
    });
  const setWhere = (test: (x: PlannedTask) => boolean) => setPicked(new Set(tasks.filter((x) => runnable(x) && test(x)).map((x) => x.spec_id)));
  const confirm = () => start.mutate({ stages: ["produce"], params: { tier, only: [...picked] } }, { onSuccess: onClose });
  return (
    <section className="card stack">
      <div className="row">
        <strong>{t("web.plan.title")}</strong>
        <span className="spacer" />
        <label className="row small">
          <span>{t("web.workshop.tier")}</span>
          <select value={tier} onChange={(e) => setTier(e.target.value)}>
            {["draft", "preview", "final"].map((x) => (
              <option key={x} value={x}>
                {t(`media.tier.${x}`)}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="link" onClick={onClose}>
          {t("web.close")}
        </button>
      </div>
      <p className="muted small">{t("web.plan.intro")}</p>
      <ErrorNote error={plan.error ?? start.error} />
      {tasks.length === 0 && !plan.isLoading ? <p className="empty">{t("web.plan.empty")}</p> : null}
      <div className="row small">
        <button type="button" className="chip chip-button" onClick={() => setWhere(() => true)}>
          {t("web.plan.pick_all")}
        </button>
        <button type="button" className="chip chip-button" onClick={() => setWhere((x) => x.subject_type !== VIDEO)}>
          {t("web.plan.pick_no_video")}
        </button>
        <button type="button" className="chip chip-button" onClick={() => setPicked(new Set())}>
          {t("web.plan.pick_none")}
        </button>
      </div>
      {PHASES.map((phase) => {
        const rows = tasks.filter((x) => x.phase === phase);
        if (rows.length === 0) return null;
        return (
          <div key={phase} className="stack">
            <strong className="small">{`${t(`web.plan.phase.${phase}`)} (${rows.filter(runnable).length}/${rows.length})`}</strong>
            <ul className="plain stack">
              {rows.map((x) => (
                <li key={x.spec_id} className="row small">
                  <input
                    type="checkbox"
                    aria-label={x.label}
                    disabled={!runnable(x)}
                    checked={picked.has(x.spec_id)}
                    onChange={(e) => toggle(x.spec_id, e.target.checked)}
                  />
                  <span className="chip">{t(`spec.subject.${x.subject_type}`)}</span>
                  <span>{x.label}</span>
                  <span className="muted">{`${t(`media.kind.${x.kind}`)} · ${x.model_ref || "-"}`}</span>
                  <span className="spacer" />
                  <span className={x.state === "blocked" ? "chip chip-failed" : x.state === "done" ? "chip chip-completed" : "chip"}>
                    {x.state === "blocked" ? t(`web.plan.reason.${x.reason}`) : t(`web.plan.state.${x.state}`)}
                  </span>
                  {runnable(x) ? <span className="muted">{`~${duration(x.est_seconds)}`}</span> : null}
                </li>
              ))}
            </ul>
          </div>
        );
      })}
      <div className="row">
        <span>{t("web.plan.selected", { n: chosen.length, eta: duration(eta) })}</span>
        <span className="spacer" />
        <button type="button" className="primary" disabled={chosen.length === 0 || start.isPending} onClick={confirm}>
          {t("web.plan.confirm")}
        </button>
      </div>
      <p className="muted small">{t("web.plan.video_hint")}</p>
    </section>
  );
}
