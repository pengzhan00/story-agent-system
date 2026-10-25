import { useEffect, useMemo, useRef, useState } from "react";
import { useAutoVoices, useProducePlan, useStartRun } from "../api/hooks";
import { useSpecs } from "../api/specs";
import type { PlannedTask } from "../api/types";
import { useT } from "../i18n";
import { duration } from "../lib/format";
import { ErrorNote } from "./ErrorNote";
import { PlanTaskEditor } from "./PlanTaskEditor";

const PHASES = [1, 2, 3];
const VIDEO = "shot.video";
const runnable = (t: PlannedTask) => t.state === "new" || t.state === "stale";
const optional = (t: PlannedTask) => t.state === "skip";       // a shot the policy leaves as a still: tick it for a clip

/**
 * The production plan: every image, voice, song and video that producing would make, with its model and an estimate.
 * Nothing runs until it is confirmed; the expensive videos start unticked.
 */
export function ProducePlan({ id, onClose, defaultTier = "draft" }: { id: number; onClose: () => void; defaultTier?: string }) {
  const t = useT();
  const [tier, setTier] = useState(defaultTier);
  const plan = useProducePlan(id, tier, true);
  const start = useStartRun(id);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<string | null>(null);
  const voices = useAutoVoices(id);
  const specs = useSpecs(id);
  const specOf = useMemo(() => new Map((specs.data ?? []).map((x) => [x.id, x])), [specs.data]);
  const tasks = useMemo(() => plan.data?.tasks ?? [], [plan.data]);
  // the first plan of a tier starts with everything but the videos ticked; a plan reloaded after an edit keeps the choice
  const seeded = useRef("");
  useEffect(() => {
    if (tasks.length === 0) return;
    if (seeded.current !== tier) {
      seeded.current = tier;
      setPicked(new Set(tasks.filter((x) => runnable(x) && x.subject_type !== VIDEO).map((x) => x.spec_id)));
    } else {
      setPicked((p) => new Set(tasks.filter((x) => (runnable(x) || optional(x)) && p.has(x.spec_id)).map((x) => x.spec_id)));
    }
  }, [tasks, tier]);
  const gates = plan.data?.gates ?? [];
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
      {gates.length > 0 ? (
        <div role="alert" className="stack">
          <strong className="bad">{t("web.plan.gates_title")}</strong>
          <ul className="plain stack small">
            {gates.map((g) => (
              <li key={`${g.reason}:${g.name}:${g.detail}`}>{t(`web.plan.gate.${g.reason}`, { name: g.name, detail: g.detail })}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {tasks.some((x) => x.reason === "no_voice") || voices.data ? (
        <div className="stack">
          <div className="row">
            <button type="button" disabled={voices.isPending} onClick={() => voices.mutate()}>
              {t("web.plan.auto_voices")}
            </button>
            <span className="muted small">{t("web.plan.auto_voices_hint")}</span>
          </div>
          {voices.data ? (
            <ul className="plain small">
              {voices.data.assigned.map((a) => (
                <li key={a.character}>{t("web.plan.auto_voices_done", { name: a.character, voice: a.voice, n: a.lines })}</li>
              ))}
              {voices.data.unresolved.map((a) => (
                <li key={a.character} className="bad">
                  {t(`web.plan.auto_voices_${a.reason}`, { name: a.character })}
                </li>
              ))}
            </ul>
          ) : null}
          <ErrorNote error={voices.error} />
        </div>
      ) : null}
      {tasks.length === 0 && !plan.isLoading ? <p className="empty">{t("web.plan.empty")}</p> : null}
      <div className="row small">
        <button type="button" className="chip chip-button" onClick={() => setWhere(() => true)}>
          {t("web.plan.pick_all")}
        </button>
        <button type="button" className="chip chip-button" onClick={() => setWhere((x) => x.subject_type !== VIDEO)}>
          {t("web.plan.pick_no_video")}
        </button>
        <button type="button" className="chip chip-button" onClick={() => setPicked(new Set(tasks.filter((x) => runnable(x) || optional(x)).map((x) => x.spec_id)))}>
          {t("web.plan.pick_every_clip")}
        </button>
        <button type="button" className="chip chip-button" onClick={() => setPicked(new Set(tasks.filter((x) => x.subject_type === VIDEO && (runnable(x) || optional(x))).map((x) => x.spec_id)))}>
          {t("web.plan.pick_only_video")}
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
              {rows.map((x) => {
                const spec = specOf.get(x.spec_id);
                const shown = spec ? (spec.prompt || spec.text_payload).replace(/\s+/g, " ").trim() : "";
                return (
                  <li key={x.spec_id} className="stack">
                    <div className="row small">
                      <input
                        type="checkbox"
                        aria-label={x.label}
                        disabled={!runnable(x) && !optional(x)}
                        checked={picked.has(x.spec_id)}
                        onChange={(e) => toggle(x.spec_id, e.target.checked)}
                      />
                      <span className="chip">{t(`spec.subject.${x.subject_type}`)}</span>
                      <span>{x.label}</span>
                      <span className="muted">{`${t(`media.kind.${x.kind}`)} · ${x.model_ref || "-"}`}</span>
                      <span className="spacer" />
                      {x.state === "blocked" ? (
                        <button type="button" className="chip chip-failed chip-button" disabled={!spec} onClick={() => setOpen(open === x.spec_id ? null : x.spec_id)}>
                          {`${t(`web.plan.reason.${x.reason}`, { detail: x.detail })} · ${t("web.plan.fix")}`}
                        </button>
                      ) : (
                        <span className={x.state === "done" ? "chip chip-completed" : "chip"}>{t(`web.plan.state.${x.state}`)}</span>
                      )}
                      {runnable(x) || optional(x) ? <span className="muted">{`~${duration(x.est_seconds)}`}</span> : null}
                      <button type="button" className="link" disabled={!spec} onClick={() => setOpen(open === x.spec_id ? null : x.spec_id)}>
                        {open === x.spec_id ? t("web.plan.hide") : t("web.plan.details")}
                      </button>
                    </div>
                    {open !== x.spec_id && shown ? <p className="muted small">{shown.length > 160 ? `${shown.slice(0, 160)}…` : shown}</p> : null}
                    {open === x.spec_id && spec ? (
                      <PlanTaskEditor
                        pid={id} spec={spec} task={x} tier={tier} picked={picked.has(x.spec_id)}
                        onConfirm={() => toggle(x.spec_id, true)} onClose={() => setOpen(null)}
                      />
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
      <div className="row">
        <span>{t("web.plan.selected", { n: chosen.length, eta: duration(eta) })}</span>
        <span className="spacer" />
        <button type="button" className="primary" disabled={chosen.length === 0 || gates.length > 0 || start.isPending} onClick={confirm}>
          {t("web.plan.confirm")}
        </button>
      </div>
      {plan.data && plan.data.video.shots > 0 ? (
        <p className="small">
          {t("web.plan.clips", { chosen: plan.data.video.chosen, shots: plan.data.video.shots, eta: duration(plan.data.video.est_seconds) })}
          {plan.data.video.budget_seconds !== null ? ` · ${t("web.plan.budget", { time: duration(plan.data.video.budget_seconds) })}` : ""}
        </p>
      ) : null}
      <p className="muted small">{t("web.plan.video_hint")}</p>
    </section>
  );
}
