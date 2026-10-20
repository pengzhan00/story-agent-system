import { NavLink, Navigate, Route, Routes, useParams } from "react-router-dom";
import { useProject, useWorkflow } from "../api/hooks";
import { ErrorNote } from "../components/ErrorNote";
import { ProgressBar } from "../components/ProgressBar";
import { useT } from "../i18n";
import { JobsPanel } from "./QueuePage";
import { StoryPanel } from "./StoryPanel";
import { WorkflowPanel } from "./WorkflowPanel";

export function ProjectPage() {
  const t = useT();
  const id = Number(useParams().id);
  const project = useProject(id);
  const wf = useWorkflow(id);
  const run = wf.data?.active_run;
  return (
    <div className="stack">
      <header className="row">
        <h1>{project.data?.name ?? t("web.loading")}</h1>
        {run ? (
          <div className="mini-progress" title={t("web.workflow.overall")}>
            <ProgressBar percent={run.status === "queued" ? 0 : run.progress.percent} label={t("web.workflow.overall")} />
            <span className="small">{run.status === "queued" ? "0%" : `${run.progress.percent.toFixed(0)}%`}</span>
          </div>
        ) : null}
      </header>
      <ErrorNote error={project.error} />
      <nav className="tabs">
        <NavLink to="workflow" className={({ isActive }) => (isActive ? "tab active" : "tab")}>
          {t("web.tab.workflow")}
        </NavLink>
        <NavLink to="story" className={({ isActive }) => (isActive ? "tab active" : "tab")}>
          {t("web.tab.story")}
        </NavLink>
        <NavLink to="jobs" className={({ isActive }) => (isActive ? "tab active" : "tab")}>
          {t("web.tab.jobs")}
        </NavLink>
      </nav>
      <Routes>
        <Route index element={<Navigate to="workflow" replace />} />
        <Route path="workflow" element={<WorkflowPanel id={id} />} />
        <Route path="story" element={<StoryPanel id={id} />} />
        <Route path="jobs" element={<JobsPanel projectId={id} />} />
      </Routes>
    </div>
  );
}
