import { useState } from "react";
import { Link } from "react-router-dom";
import { useMeta, useProjectBin, useProjectCards } from "../api/hooks";
import type { ProjectCard } from "../api/types";
import { NewProject } from "../components/NewProject";
import { PreviewDialog } from "../components/PreviewDialog";
import { ProgressBar } from "../components/ProgressBar";
import { duration } from "../lib/format";
import { useI18n } from "../i18n";
import { ErrorNote } from "../components/ErrorNote";

export function ProjectsPage() {
  const { t } = useI18n();
  const [bin, setBin] = useState(false);
  const projects = useProjectCards(bin);
  const [preview, setPreview] = useState<ProjectCard | null>(null);
  const binActions = useProjectBin();
  const meta = useMeta().data;

  return (
    <div className="stack">
      <div className="row">
        <h1>{bin ? t("web.projects.bin") : t("web.projects.title")}</h1>
        <span className="spacer" />
        <button type="button" onClick={() => setBin(!bin)}>
          {bin ? t("web.projects.live") : t("web.projects.bin")}
        </button>
      </div>
      {bin ? null : <NewProject />}

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
            <p className="badges">
              {p.kind ? <span className="chip">{meta?.work_kinds.find((k) => k.name === p.kind)?.label ?? p.kind}</span> : null}
              {p.pipeline ? <span className="chip">{meta?.pipelines.find((x) => x.name === p.pipeline)?.label ?? p.pipeline}</span> : null}
            </p>
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
