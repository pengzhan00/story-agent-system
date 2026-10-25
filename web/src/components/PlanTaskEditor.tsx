import { useSpecActions } from "../api/specs";
import type { PlannedTask, Spec } from "../api/types";
import { useT } from "../i18n";
import { ErrorNote } from "./ErrorNote";
import { SpecEditor } from "./SpecEditor";
import { VoicePicker } from "./VoicePicker";

/**
 * One task of the production plan opened up: its prompt, text, model and parameters, editable (each save is a new version
 * of the spec), the voice for a spoken line, and two ways on: confirm it as it is now, or make just this one at once.
 */
export function PlanTaskEditor({
  pid, spec, task, tier, picked, onConfirm, onClose,
}: {
  pid: number; spec: Spec; task: PlannedTask; tier: string; picked: boolean; onConfirm: () => void; onClose: () => void;
}) {
  const t = useT();
  const act = useSpecActions(pid, spec);
  const voice = String(spec.params?.["voice_ref"] ?? "");
  const speech = spec.subject_type === "dialogue.line";
  return (
    <div className="card stack">
      {speech ? (
        <VoicePicker
          value={voice}
          disabled={spec.locked}
          busy={act.edit.isPending}
          onApply={(ref) => act.edit.mutate({ params_patch: { voice_ref: ref }, note: t("web.plan.voice") })}
        />
      ) : null}
      <SpecEditor pid={pid} spec={spec} review={false} onAcknowledge={() => undefined} />
      <div className="row">
        <button type="button" className="primary" disabled={picked || task.state === "blocked"} onClick={onConfirm}>
          {picked ? t("web.plan.confirmed") : t("web.plan.confirm_task")}
        </button>
        <button
          type="button"
          disabled={act.regenerate.isPending}
          onClick={() => act.regenerate.mutate({ n_candidates: 1, tier, vary_seed: false }, { onSuccess: onClose })}
        >
          {t("web.plan.make_only")}
        </button>
        <span className="spacer" />
        <button type="button" className="link" onClick={onClose}>
          {t("web.plan.hide")}
        </button>
      </div>
      <ErrorNote error={act.regenerate.error} />
    </div>
  );
}
