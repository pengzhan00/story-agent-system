import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useCreateProject, useProjectBin, useProjects } from "../api/hooks";
import { useI18n } from "../i18n";
import { ErrorNote } from "../components/ErrorNote";

export function ProjectsPage() {
  const { t, locale } = useI18n();
  const [bin, setBin] = useState(false);
  const projects = useProjects(bin);
  const binActions = useProjectBin();
  const create = useCreateProject();
  const nav = useNavigate();
  const [name, setName] = useState("");
  const [premise, setPremise] = useState("");

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate(
      { name: name.trim(), description: premise.trim(), content_language: locale },
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
          <li key={p.id} className="card">
            <h3>
              <Link to={`/projects/${p.id}`}>{p.name}</Link>
            </h3>
            <p className="muted clamp">{p.description || t("web.projects.no_premise")}</p>
            <p className="muted small">{p.genre}</p>
            {bin ? (
              <button type="button" onClick={() => binActions.restore.mutate(p.id)} disabled={binActions.restore.isPending}>
                {t("web.projects.restore")}
              </button>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
