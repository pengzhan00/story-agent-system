import { useState } from "react";
import { useLibraryItems } from "../api/library";
import { useCancelRun, useMeta, useProject, useProjectSettings, useReviewScript, useStartRun, useUpdateProject, useWorkflow } from "../api/hooks";
import { ErrorNote } from "../components/ErrorNote";
import { ProgressBar } from "../components/ProgressBar";
import { ProducePlan } from "../components/ProducePlan";
import { SeasonCard } from "../components/SeasonCard";
import { EpisodePlayer } from "../components/EpisodePlayer";
import { RunError, RunProgress } from "../components/RunProgress";
import { ProjectPlan } from "../components/ProjectPlan";
import { StatusChip } from "../components/StatusChip";
import { duration } from "../lib/format";
import { useT } from "../i18n";

const TARGET_PRESETS = [30, 90, 120, 180];

/** How long the episode should be and in which delivery format: one number steers the story size and the quality check. */
function Spec({ id }: { id: number }) {
  const t = useT();
  const project = useProject(id);
  const meta = useMeta();
  const update = useUpdateProject(id);
  const settings = useProjectSettings(id);
  const saved = project.data?.settings?.["target_sec"];
  const [draft, setDraft] = useState<string | null>(null);
  const voices = (useLibraryItems().data ?? []).filter((i) => i.role === "voice");
  const voice = project.data?.settings?.["default_voice"];
  const share = project.data?.settings?.["video_share"];
  const budget = project.data?.settings?.["video_budget_hours"];
  const editRaw = project.data?.settings?.["edit"];
  const edit: Record<string, unknown> = editRaw && typeof editRaw === "object" ? (editRaw as Record<string, unknown>) : {};
  const setEdit = (key: string, value: unknown) => settings.mutate({ edit: { ...edit, [key]: value } });
  const [shareDraft, setShareDraft] = useState<string | null>(null);
  const [budgetDraft, setBudgetDraft] = useState<string | null>(null);
  const profiles = meta.data?.delivery_profiles ?? [];
  const current = profiles.find((p) => p.name === project.data?.delivery_profile);
  const shown = draft ?? (typeof saved === "number" ? String(saved) : "");
  const save = (value: number | null) => settings.mutate({ target_sec: value }, { onSuccess: () => setDraft(null) });
  return (
    <section className="card stack">
      <div className="form-grid">
        <label className="field">
          <span>{t("web.workflow.profile")}</span>
          <select value={project.data?.delivery_profile ?? ""} onChange={(e) => update.mutate({ delivery_profile: e.target.value })}>
            {profiles.map((p) => (
              <option key={p.name} value={p.name}>
                {`${p.label} · ${p.max_episode_sec ? t("web.workflow.profile_range", { lo: p.min_episode_sec, hi: p.max_episode_sec }) : t("web.workflow.profile_open")}`}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>{t("web.workflow.target")}</span>
          <span className="row">
            <input
              type="number"
              min={1}
              value={shown}
              placeholder={current?.target_sec ? String(current.target_sec) : ""}
              onChange={(e) => setDraft(e.target.value)}
            />
            <span className="muted small">{t("web.workflow.target_unit")}</span>
          </span>
        </label>
        <label className="field">
          <span>{t("web.workflow.voice")}</span>
          <select
            value={typeof voice === "string" ? voice : ""}
            onChange={(e) => settings.mutate({ default_voice: e.target.value || null })}
          >
            <option value="">{t("web.workflow.voice_none")}</option>
            {voices.map((v) => (
              <option key={v.id} value={v.ref}>
                {v.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="form-grid">
        <label className="field">
          <span>{t("web.workflow.video_share")}</span>
          <input
            type="number"
            min={0}
            max={100}
            value={shareDraft ?? (typeof share === "number" ? String(Math.round(share * 100)) : "")}
            placeholder={t("web.workflow.default")}
            onChange={(e) => setShareDraft(e.target.value)}
            onBlur={() => {
              if (shareDraft !== null) settings.mutate({ video_share: shareDraft.trim() === "" ? null : Number(shareDraft) / 100 }, { onSuccess: () => setShareDraft(null) });
            }}
          />
        </label>
        <label className="field">
          <span>{t("web.workflow.video_budget")}</span>
          <input
            type="number"
            min={0}
            value={budgetDraft ?? (typeof budget === "number" ? String(budget) : "")}
            placeholder={t("web.workflow.default")}
            onChange={(e) => setBudgetDraft(e.target.value)}
            onBlur={() => {
              if (budgetDraft !== null) settings.mutate({ video_budget_hours: budgetDraft.trim() === "" ? null : Number(budgetDraft) }, { onSuccess: () => setBudgetDraft(null) });
            }}
          />
        </label>
      </div>
      <p className="muted small">{t("web.workflow.video_hint")}</p>
      <div className="row">
        {(["recap_card", "title_card", "next_card"] as const).map((key) => (
          <label key={key} className="check">
            <input type="checkbox" checked={edit[key] === true} onChange={(e) => setEdit(key, e.target.checked)} />
            <span>{t(`web.edit.${key}`)}</span>
          </label>
        ))}
        {(["scene_fade_sec", "dissolve_sec", "j_cut_sec", "l_cut_sec"] as const).map((key) => (
          <label key={key} className="check">
            <span>{t(`web.edit.${key}`)}</span>
            <input
              type="number"
              min={0}
              max={2}
              step={0.1}
              value={typeof edit[key] === "number" ? String(edit[key]) : ""}
              placeholder="0"
              onChange={(e) => setEdit(key, e.target.value === "" ? 0 : Number(e.target.value))}
            />
          </label>
        ))}
      </div>
      <p className="muted small">{t("web.edit.hint")}</p>
      <p className="muted small">{t("web.workflow.voice_hint")}</p>
      <p className="muted small">{t("web.workflow.target_hint")}</p>
      <div className="row">
        {TARGET_PRESETS.map((n) => (
          <button key={n} type="button" className="chip chip-button" onClick={() => setDraft(String(n))}>
            {n === 30 ? t("web.workflow.target_pilot") : `${n} ${t("web.workflow.target_unit")}`}
          </button>
        ))}
        <span className="spacer" />
        <button type="button" disabled={settings.isPending || saved === undefined} onClick={() => save(null)}>
          {t("web.workflow.target_clear")}
        </button>
        <button type="button" className="primary" disabled={settings.isPending || draft === null || Number(draft) <= 0} onClick={() => save(Number(draft))}>
          {t("web.save")}
        </button>
      </div>
      <ErrorNote error={settings.error ?? update.error} />
    </section>
  );
}

export function WorkflowPanel({ id }: { id: number }) {
  const t = useT();
  const project = useProject(id);
  const [episode, setEpisode] = useState(1);
  const [batch, setBatch] = useState("");
  const wf = useWorkflow(id, episode);
  const start = useStartRun(id);
  const cancel = useCancelRun(id);
  const update = useUpdateProject(id);
  const review = useReviewScript(id);
  const [premise, setPremise] = useState<string | null>(null);
  const [force, setForce] = useState(false);
  const [planning, setPlanning] = useState(false);

  const active = wf.data?.active_run ?? null;
  const last = wf.data?.recent_runs.find((r) => r.status !== "queued" && r.status !== "running") ?? null;
  const MEDIA_STAGES = ["produce", "compose"];
  // the season outline is optional: it only counts once it has been run
  const storyStages = (wf.data?.stages ?? []).filter((s) => !MEDIA_STAGES.includes(s.stage) && (s.stage !== "outline" || s.status !== "pending"));
  const storyDone = storyStages.length > 0 && storyStages.every((s) => s.status === "completed");
  const allDone = storyDone;
  const stage = (name: string) => wf.data?.stages.find((s) => s.stage === name);
  const compose = stage("compose");
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
          <label className="field">
            <span>{t("web.workflow.episode")}</span>
            <input type="number" min={1} value={episode} onChange={(e) => setEpisode(Math.max(1, Number(e.target.value) || 1))} />
          </label>
          <label className="field">
            <span>{t("web.workflow.episodes")}</span>
            <input value={batch} placeholder="1-5" onChange={(e) => setBatch(e.target.value)} />
          </label>
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
                  params: { ...(force ? { force: true } : {}), ...(batch.trim() ? { episodes: batch.trim() } : { episode }) },
                })
              }
            >
              {allDone ? t("web.workflow.rerun") : wf.data?.stages.some((s) => s.status !== "pending") ? t("web.workflow.resume") : t("web.workflow.start")}
            </button>
          )}
        </div>
        <p className="muted small">{t("web.workflow.episodes_hint")}</p>
        {dirty ? <p className="hint">{t("web.workflow.save_first")}</p> : null}
        <ErrorNote error={start.error ?? cancel.error ?? update.error ?? wf.error} />
      </section>

      <ProjectPlan id={id} />

      <Spec id={id} />

      <SeasonCard id={id} busy={active !== null} />

      {active ? <RunProgress run={active} /> : null}

      {storyDone && !active ? (
        <section className="card stack">
          <div className="row">
            <button className="primary" disabled={start.isPending} onClick={() => setPlanning(!planning)}>
              {t("web.workflow.make_media")}
            </button>
            <button disabled={start.isPending} onClick={() => start.mutate({ stages: ["compose"] })}>
              {t("web.workflow.make_episode")}
            </button>
          </div>
          <p className="muted small">{t("web.workflow.make_media_hint")} {t("web.workflow.needs_worker")}</p>
          {planning ? <ProducePlan id={id} onClose={() => setPlanning(false)} /> : null}
          <div className="row">
            <button disabled={review.isPending} onClick={() => review.mutate()}>
              {review.isPending ? t("web.review.running") : t("web.review.run")}
            </button>
          </div>
          <ErrorNote error={review.error} />
          {review.data ? (
            <div className="stack">
              <strong>
                {t("web.review.title")}: {t("web.review.score", { score: review.data.score })}
              </strong>
              <p>{review.data.summary}</p>
              {review.data.issues.length === 0 ? <p className="muted">{t("web.review.none")}</p> : null}
              <ul className="plain">
                {review.data.issues.map((i, n) => (
                  <li key={n}>
                    <span className="muted">{`[${i.severity}] ${i.where}`}</span> {i.problem}
                    {i.fix ? <span className="muted">{` → ${i.fix}`}</span> : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {stage("produce")?.summary && Number(stage("produce")?.summary["skipped"] ?? 0) > 0 ? (
            <p className="muted small">
              {t("web.produce.skipped", { n: Number(stage("produce")?.summary["skipped"]) })}
              {" · "}
              {[...new Set(((stage("produce")?.summary["skipped_detail"] ?? []) as { reason: string }[]).map((d) => t(`web.produce.reason.${d.reason}`)))].join(", ")}
            </p>
          ) : null}
        </section>
      ) : null}

      {compose?.status === "completed" && compose.summary ? <EpisodePlayer projectId={id} summary={compose.summary} /> : null}

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
