import { useState } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { useProject, useProjectBin, useUpdateProject, useWorkflow } from "../api/hooks";
import { ContentBar } from "../components/ContentBar";
import { ErrorNote } from "../components/ErrorNote";
import { ProgressBar } from "../components/ProgressBar";
import { useT } from "../i18n";
import { FlowChain } from "../components/FlowChain";
import { CreativePanel } from "./CreativePanel";
import { ContinuityPanel } from "./ContinuityPanel";
import { GrammarPanel } from "./GrammarPanel";
import { DeliveryPanel } from "./DeliveryPanel";
import { PackPanel } from "./PackPanel";
import { ProcessPanel } from "./ProcessPanel";
import { ReviewPanel } from "./ReviewPanel";
import { JobsPanel } from "./QueuePage";
import { SeriesPanel } from "./SeriesPanel";
import { BiblePanel } from "./BiblePanel";
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
  const restricted = !!project.data && project.data.rating !== "general";
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
        {project.data && project.data.rating !== "general" ? <span className="chip">{t("web.content.badge")}</span> : null}
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
      {restricted ? <ContentBar /> : null}
      <nav className="tabs">
        <NavLink to="workflow" className={({ isActive }) => (isActive ? "tab active" : "tab")}>
          {t("web.tab.workflow")}
        </NavLink>
        <NavLink to="series" className={({ isActive }) => (isActive ? "tab active" : "tab")}>
          {t("web.tab.series")}
        </NavLink>
        <NavLink to="bible" className={({ isActive }) => (isActive ? "tab active" : "tab")}>
          {t("web.tab.bible")}
        </NavLink>
        <NavLink to="story" className={({ isActive }) => (isActive ? "tab active" : "tab")}>
          {t("web.tab.story")}
        </NavLink>
        <NavLink to="creative" className={({ isActive }) => (isActive ? "tab active" : "tab")}>
          {t("web.tab.creative")}
        </NavLink>
        <NavLink to="continuity" className={({ isActive }) => (isActive ? "tab active" : "tab")}>
          {t("web.tab.continuity")}
        </NavLink>
        <NavLink to="grammar" className={({ isActive }) => (isActive ? "tab active" : "tab")}>
          {t("web.tab.grammar")}
        </NavLink>
        <NavLink to="review" className={({ isActive }) => (isActive ? "tab active" : "tab")}>
          {t("web.tab.review")}
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
        <NavLink to="delivery" className={({ isActive }) => (isActive ? "tab active" : "tab")}>
          {t("web.tab.delivery")}
        </NavLink>
        <NavLink to="pack" className={({ isActive }) => (isActive ? "tab active" : "tab")}>
          {t("web.tab.pack")}
        </NavLink>
      </nav>
      <Routes>
        <Route index element={<Navigate to="workflow" replace />} />
        <Route path="workflow" element={<WorkflowPanel id={id} />} />
        <Route path="series" element={<SeriesPanel id={id} />} />
        <Route path="bible" element={<BiblePanel id={id} />} />
        <Route path="story" element={<StoryPanel id={id} />} />
        <Route path="creative" element={<CreativePanel id={id} />} />
        <Route path="continuity" element={<ContinuityPanel id={id} />} />
        <Route path="grammar" element={<GrammarPanel id={id} />} />
        <Route path="review" element={<ReviewPanel id={id} />} />
        <Route path="flow" element={<FlowChain projectId={id} />} />
        <Route path="process" element={<ProcessPanel id={id} />} />
        <Route path="jobs" element={<JobsPanel projectId={id} />} />
        <Route path="delivery" element={<DeliveryPanel id={id} />} />
        <Route path="pack" element={<PackPanel id={id} />} />
      </Routes>
    </div>
  );
}
