import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useCreateProject, useMeta, useProjectBin, useProjectCards } from "../api/hooks";
import type { ProjectCard } from "../api/types";
import { PreviewDialog } from "../components/PreviewDialog";
import { ProgressBar } from "../components/ProgressBar";
import { duration } from "../lib/format";
import { useI18n } from "../i18n";
import { ErrorNote } from "../components/ErrorNote";

export function ProjectsPage() {
  const { t, locale } = useI18n();
  const [bin, setBin] = useState(false);
  const projects = useProjectCards(bin);
  const [preview, setPreview] = useState<ProjectCard | null>(null);
  const binActions = useProjectBin();
  const create = useCreateProject();
  const nav = useNavigate();
  const [name, setName] = useState("");
  const [premise, setPremise] = useState("");
  const profiles = useMeta().data?.delivery_profiles ?? [];
  const [frame, setFrame] = useState("");

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate(
      { name: name.trim(), description: premise.trim(), content_language: locale, delivery_profile: frame },
      { onSuccess: (p) => nav(`/projects/${p.id}`) },
    );
  }

  return (
    <div className="stack">
      <div className="row">
        <h1>{bin ? t("web.projects.bin") : t("web.projects.title")}</h1>
        <span className="spacer" />
        <button type="button" onClick={() => setBin(!bin)}>
          {bin ? t("web.projects.live") : t("web.projects.bin")}
        </button>
      </div>
      {bin ? null : (
      <form className="card stack" onSubmit={submit}>
        <h2>{t("web.projects.new")}</h2>
        <label className="field">
          <span>{t("web.projects.name")}</span>
          <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={200} />
        </label>
        <label className="field">
          <span>{t("web.projects.premise")}</span>
          <textarea value={premise} onChange={(e) => setPremise(e.target.value)} rows={3} placeholder={t("web.projects.premise_hint")} />
        </label>
        <label className="field">
          <span>{t("web.projects.frame")}</span>
          <select value={frame} onChange={(e) => setFrame(e.target.value)}>
            <option value="">{t("web.projects.frame_default")}</option>
            {profiles.map((p) => (
              <option key={p.name} value={p.name}>
                {`${p.label} · ${p.width}×${p.height}`}
              </option>
            ))}
          </select>
          <span className="muted small">{t("web.projects.frame_hint")}</span>
        </label>
        <div className="row">
          <button className="primary" type="submit" disabled={!name.trim() || create.isPending}>
            {t("web.projects.create")}
          </button>
        </div>
        <ErrorNote error={create.error} />
      </form>
      )}

      {projects.isLoading ? <p className="muted">{t("web.loading")}</p> : null}
      <ErrorNote error={projects.error} />
      {projects.data && projects.data.length === 0 ? (
        <p className="empty">{bin ? t("web.projects.bin_empty") : t("web.projects.empty")}</p>
      ) : null}
      <ErrorNote error={binActions.restore.error} />
      <ul className="grid">
        {projects.data?.map((p) => (
          <li key={p.id} className="card project-card">
            {p.episode_asset_id ? (
              <button className="poster" onClick={() => setPreview(p)} aria-label={t("web.projects.play", { name: p.name })}>
                <video src={`/api/assets/${p.episode_asset_id}/file#t=0.5`} preload="metadata" muted playsInline />
                <span className="play" aria-hidden="true" />
                {p.episode_seconds ? <span className="badge">{duration(p.episode_seconds)}</span> : null}
              </button>
            ) : null}
            <h3>
              <Link to={`/projects/${p.id}`}>{p.name}</Link>
            </h3>
            <p className="muted clamp">{p.description || t("web.projects.no_premise")}</p>
            <div className="row small">
              <ProgressBar percent={(100 * p.stages_done) / p.stages_total} tone={p.running ? "running" : p.stages_done === p.stages_total ? "completed" : "pending"} label={t("web.projects.progress")} />
              <span className="muted">{t("web.projects.steps", { done: p.stages_done, total: p.stages_total })}</span>
            </div>
            <div className="row">
              {p.episode_asset_id ? (
                <button className="primary" onClick={() => setPreview(p)}>
                  {t("web.projects.preview")}
                </button>
              ) : null}
              {!bin ? (
                <Link className="button" to={`/projects/${p.id}`}>
                  {t("web.projects.open")}
                </Link>
              ) : null}
              {bin ? (
                <button type="button" onClick={() => binActions.restore.mutate(p.id)} disabled={binActions.restore.isPending}>
                  {t("web.projects.restore")}
                </button>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
      {preview?.episode_asset_id ? (
        <PreviewDialog assetId={preview.episode_asset_id} title={preview.name} seconds={preview.episode_seconds} projectId={preview.id} onClose={() => setPreview(null)} />
      ) : null}
    </div>
  );
}
