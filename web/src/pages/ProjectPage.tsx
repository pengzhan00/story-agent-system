import { useState } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { useProject, useProjectBin, useUpdateProject, useWorkflow } from "../api/hooks";
import { ErrorNote } from "../components/ErrorNote";
import { ProgressBar } from "../components/ProgressBar";
import { useT } from "../i18n";
import { FlowChain } from "../components/FlowChain";
import { CreativePanel } from "./CreativePanel";
import { ProcessPanel } from "./ProcessPanel";
import { JobsPanel } from "./QueuePage";
import { StoryPanel } from "./StoryPanel";
import { WorkflowPanel } from "./WorkflowPanel";

export function ProjectPage() {
  const t = useT();
  const id = Number(useParams().id);
  const project = useProject(id);
  const wf = useWorkflow(id);
  const run = wf.data?.active_run;
  const rename = useUpdateProject(id);
  const bin = useProjectBin();
  const nav = useNavigate();
  const [draft, setDraft] = useState<string | null>(null);
  function saveName() {
    if (draft && draft.trim() && draft.trim() !== project.data?.name) rename.mutate({ name: draft.trim() });
    setDraft(null);
  }
  return (
    <div className="stack">
      <header className="row">
        {draft === null ? (
          <h1>{project.data?.name ?? t("web.loading")}</h1>
        ) : (
          <input
            className="grow"
            autoFocus
            value={draft}
            maxLength={200}
            aria-label={t("web.projects.rename")}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={saveName}
            onKeyDown={(e) => {
              if (e.key === "Enter") saveName();
              if (e.key === "Escape") setDraft(null);
            }}
          />
        )}
        {draft === null && project.data ? (
          <button type="button" onClick={() => setDraft(project.data?.name ?? "")}>
            {t("web.projects.rename")}
          </button>
        ) : null}
        {run ? (
          <div className="mini-progress" title={t("web.workflow.overall")}>
            <ProgressBar percent={run.status === "queued" ? 0 : run.progress.percent} label={t("web.workflow.overall")} />
            <span className="small">{run.status === "queued" ? "0%" : `${run.progress.percent.toFixed(0)}%`}</span>
          </div>
        ) : null}
        <span className="spacer" />
        <button
          type="button"
          disabled={!!run || bin.trash.isPending}
          onClick={() => {
            if (window.confirm(t("web.projects.trash_confirm"))) bin.trash.mutate(id, { onSuccess: () => nav("/") });
          }}
        >
          {t("web.projects.trash")}
        </button>
      </header>
      <ErrorNote error={project.error} />
      <ErrorNote error={rename.error ?? bin.trash.error} />
      <nav className="tabs">
        <NavLink to="workflow" className={({ isActive }) => (isActive ? "tab active" : "tab")}>
          {t("web.tab.workflow")}
        </NavLink>
        <NavLink to="story" className={({ isActive }) => (isActive ? "tab active" : "tab")}>
          {t("web.tab.story")}
        </NavLink>
        <NavLink to="creative" className={({ isActive }) => (isActive ? "tab active" : "tab")}>
          {t("web.tab.creative")}
        </NavLink>
        <NavLink to="flow" className={({ isActive }) => (isActive ? "tab active" : "tab")}>
          {t("web.tab.flow")}
        </NavLink>
        <NavLink to="process" className={({ isActive }) => (isActive ? "tab active" : "tab")}>
          {t("web.tab.process")}
        </NavLink>
        <NavLink to="jobs" className={({ isActive }) => (isActive ? "tab active" : "tab")}>
          {t("web.tab.jobs")}
        </NavLink>
      </nav>
      <Routes>
        <Route index element={<Navigate to="workflow" replace />} />
        <Route path="workflow" element={<WorkflowPanel id={id} />} />
        <Route path="story" element={<StoryPanel id={id} />} />
        <Route path="creative" element={<CreativePanel id={id} />} />
        <Route path="flow" element={<FlowChain projectId={id} />} />
        <Route path="process" element={<ProcessPanel id={id} />} />
        <Route path="jobs" element={<JobsPanel projectId={id} />} />
      </Routes>
    </div>
  );
}
