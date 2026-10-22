import { useState } from "react";
import { useEstimate, useMeta, useProject, useProjectBrief, useUpdateProject } from "../api/hooks";
import type { BriefAnswers } from "../api/types";
import { useT } from "../i18n";
import { ErrorNote } from "./ErrorNote";
import { EstimateView } from "./EstimateView";

const field = (brief: Record<string, unknown>, key: string): string => (typeof brief[key] === "number" ? String(brief[key]) : "");

/** What the project is and how big it was planned: kind, pipeline plan (one complete set of models), seasons, episodes and
 *  episode length. Changing the plan changes all its models; assets made by the others show as outdated. */
export function ProjectPlan({ id }: { id: number }) {
  const t = useT();
  const project = useProject(id).data;
  const meta = useMeta().data;
  const update = useUpdateProject(id);
  const setBrief = useProjectBrief(id);
  const [draft, setDraft] = useState<Record<string, string | undefined>>({});
  const kind = meta?.work_kinds.find((k) => k.name === project?.kind);
  const plans = meta?.pipelines ?? [];
  const planInfo = plans.find((p) => p.name === project?.pipeline);
  const brief = (project?.brief ?? {}) as Record<string, unknown>;
  const hasBrief = typeof brief["episodes_total"] === "number";
  const estimate = useEstimate(
    { kind: project?.kind ?? "", pipeline: project?.pipeline ?? "", brief: { seasons: Number(brief["seasons"]) || undefined, episodes_per_season: Number(brief["episodes_per_season"]) || undefined, episode_sec: Number(brief["episode_sec"]) || undefined } },
    !!project && !!kind && hasBrief,
  );
  if (!project || !meta) return null;
  const val = (key: string) => draft[key] ?? field(brief, key);
  function save(key: keyof BriefAnswers) {
    const raw = draft[key];
    const clear = () => setDraft((d) => ({ ...d, [key]: undefined }));
    if (raw === undefined || raw.trim() === "" || Number(raw) === brief[key]) return clear();
    setBrief.mutate({ [key]: Number(raw) }, { onSuccess: clear });
  }
  const asks = (key: string) => kind?.ask.includes(key) ?? false;
  return (
    <section className="card stack">
      <h2>{t("web.plan.title")}</h2>
      <div className="form-grid">
        <p className="field">
          <span>{t("web.projects.kind")}</span>
          <strong>{kind?.label ?? t("web.plan.no_kind")}</strong>
        </p>
        <label className="field">
          <span>{t("web.plan.pipeline")}</span>
          <select value={project.pipeline} onChange={(e) => update.mutate({ pipeline: e.target.value })} disabled={update.isPending}>
            {project.pipeline ? null : <option value="">{t("web.plan.legacy_option")}</option>}
            {plans.map((p) => (
              <option key={p.name} value={p.name}>
                {p.name === kind?.pipeline ? `${p.label} · ${t("web.plan.recommended")}` : p.label}
              </option>
            ))}
          </select>
          <span className="muted small">
            {planInfo ? t("web.plan.frame", { w: planInfo.width, h: planInfo.height, tier: t(`media.tier.${planInfo.tier}`) }) : t("web.plan.legacy")}
          </span>
        </label>
        {hasBrief && asks("seasons") ? (
          <label className="field">
            <span>{t("web.plan.seasons")}</span>
            <input type="number" min={1} value={val("seasons")} onChange={(e) => setDraft({ ...draft, seasons: e.target.value })} onBlur={() => save("seasons")} />
          </label>
        ) : null}
        {hasBrief && asks("episodes") ? (
          <label className="field">
            <span>{t("web.plan.episodes")}</span>
            <input type="number" min={1} value={val("episodes_per_season")} onChange={(e) => setDraft({ ...draft, episodes_per_season: e.target.value })} onBlur={() => save("episodes_per_season")} />
          </label>
        ) : null}
        {hasBrief && asks("episode_sec") ? (
          <label className="field">
            <span>{t("web.plan.episode_sec")}</span>
            <input type="number" min={1} value={val("episode_sec")} onChange={(e) => setDraft({ ...draft, episode_sec: e.target.value })} onBlur={() => save("episode_sec")} />
          </label>
        ) : null}
      </div>
      <p className="muted small">{t("web.plan.pipeline_hint")}</p>
      <ErrorNote error={update.error ?? setBrief.error} />
      {hasBrief ? <EstimateView estimate={estimate.data} busy={estimate.isFetching} /> : null}
    </section>
  );
}
