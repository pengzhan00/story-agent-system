import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, del, patch, post, put } from "./client";
import type { Agent, AgentEdit, AiCallDetail, AutoVoices, AiCallRow, BriefAnswers, Estimate, FlowStep, Health, Launch, ProjectCard, ProducePlan, SeriesBoard, ProjectFromText, ScriptReview, Job, Meta, Project, QueueState, Row, UpdateState, WorkflowOverview, WorkflowRun, WorkflowRunRow } from "./types";
import { useI18n } from "../i18n";

/** Polling is the fallback; the SSE feed (``events.ts``) pushes the same data much sooner. */
const LIVE_MS = 2000;

export const useMeta = () => {
  const { locale } = useI18n();
  return useQuery({ queryKey: ["meta", locale], queryFn: () => api<Meta>("/api/meta") });
};
export const useProjects = (trashed = false) =>
  useQuery({ queryKey: ["projects", trashed], queryFn: () => api<Project[]>(`/api/projects?trashed=${trashed}`) });
export const useProjectCards = (trashed = false) =>
  useQuery({
    queryKey: ["projects", "cards", trashed],
    queryFn: () => api<ProjectCard[]>(`/api/projects/overview?trashed=${trashed}`),
    refetchInterval: 5000,
  });
export const useProject = (id: number) =>
  useQuery({ queryKey: ["project", id], queryFn: () => api<Project>(`/api/projects/${id}`) });

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; description: string; content_language: string; delivery_profile?: string; kind?: string; pipeline?: string; brief?: BriefAnswers; rating?: string }) =>
      post<Project>("/api/projects", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}

/** How big the planned work is (tasks, machine time) before the project exists; refreshed as the answers change. */
export const useEstimate = (body: { kind: string; pipeline: string; brief: BriefAnswers }, enabled = true) =>
  useQuery({
    queryKey: ["estimate", body],
    queryFn: () => post<Estimate>("/api/projects/estimate", body),
    enabled,
    placeholderData: keepPreviousData,
    retry: false,
  });

/** Change the plan made up front: more seasons/episodes or another episode length. */
export function useProjectBrief(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BriefAnswers) => patch<Project>(`/api/projects/${id}/brief`, body),
    onSuccess: (p) => {
      qc.setQueryData(["project", id], p);
      void qc.invalidateQueries({ queryKey: ["series", id] });
      void qc.invalidateQueries({ queryKey: ["seasons", id] });
    },
  });
}

/** Move a project to the recycle bin, or bring it back. Both lists change, so both are refreshed. */
export function useProjectBin() {
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries({ queryKey: ["projects"] });
  return {
    trash: useMutation({ mutationFn: (id: number) => api<Project>(`/api/projects/${id}`, { method: "DELETE" }), onSuccess: done }),
    restore: useMutation({ mutationFn: (id: number) => post<Project>(`/api/projects/${id}/restore`), onSuccess: done }),
  };
}

/** Per-project overrides; `target_sec: null` goes back to the delivery profile's length. */
export function useProjectSettings(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { target_sec?: number | null; default_voice?: string | null; video_share?: number | null; video_budget_hours?: number | null; video_with_sound?: boolean | null; agents?: Record<string, Record<string, unknown>> | null; edit?: Record<string, unknown> | null }) => patch<Project>(`/api/projects/${id}/settings`, body),
    onSuccess: (p) => qc.setQueryData(["project", id], p),
  });
}

export function useUpdateProject(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Partial<Pick<Project, "name" | "description" | "genre" | "delivery_profile" | "kind" | "pipeline">>) =>
      patch<Project>(`/api/projects/${id}`, body),
    onSuccess: (p) => qc.setQueryData(["project", id], p),
  });
}

export const useWorkflow = (id: number, episode = 1) =>
  useQuery({
    queryKey: ["workflow", id, episode],
    queryFn: () => api<WorkflowOverview>(`/api/projects/${id}/workflow?episode=${episode}`),
    refetchInterval: (q) => (q.state.data?.active_run ? LIVE_MS : false),
  });

/** The story runs (plan, characters, script, shots, ...) of every project: the part of the queue that is not media. */
export const useAllRuns = () =>
  useQuery({ queryKey: ["workflow", "runs"], queryFn: () => api<WorkflowRunRow[]>("/api/workflow/runs?limit=30"), refetchInterval: LIVE_MS });

export function useStartRun(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { stages?: string[]; params?: Record<string, unknown> }) =>
      post<WorkflowRun>(`/api/projects/${id}/workflow/runs`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workflow", id] }),
  });
}

export function useAutoVoices(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => post<AutoVoices>(`/api/projects/${id}/voices/auto`),
    onSuccess: () => {
      for (const k of ["produce-plan", "specs", "story"]) void qc.invalidateQueries({ queryKey: [k] });
    },
  });
}

export const useReviewScript = (id: number) =>
  useMutation({ mutationFn: () => post<ScriptReview>(`/api/projects/${id}/review`) });

export function useCancelAnyRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) => post<WorkflowRun>(`/api/workflow/runs/${runId}/cancel`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workflow"] }),
  });
}

export function useCancelRun(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) => post<WorkflowRun>(`/api/workflow/runs/${runId}/cancel`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workflow", id] }),
  });
}

export const STORY_PARTS = ["characters", "locations", "music", "sfx", "shots", "dialogue", "scripts"] as const;
export type StoryPart = (typeof STORY_PARTS)[number];
/** Rows the story API serves; looks and props have their own page (continuity), not a tab of the story editor. */
export type StoryRows = StoryPart | "looks" | "props" | "seasons" | "episodes";
export const useStory = (id: number, part: StoryRows) =>
  useQuery({ queryKey: ["story", id, part], queryFn: () => api<Row[]>(`/api/projects/${id}/${part}`) });

export function useStoryEdit(id: number, part: StoryRows) {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["story", id, part] });
  return {
    create: useMutation({
      mutationFn: (body: Row) => post<Row>(`/api/projects/${id}/${part}`, body),
      onSuccess: invalidate,
    }),
    update: useMutation({
      mutationFn: ({ itemId, body }: { itemId: number; body: Row }) =>
        patch<Row>(`/api/projects/${id}/${part}/${itemId}`, body),
      onSuccess: invalidate,
    }),
    lock: useMutation({
      mutationFn: ({ itemId, locked }: { itemId: number; locked: boolean }) =>
        put<Row>(`/api/projects/${id}/${part}/${itemId}/lock`, { locked }),
      onSuccess: invalidate,
    }),
    remove: useMutation({ mutationFn: (itemId: number) => del(`/api/projects/${id}/${part}/${itemId}`), onSuccess: invalidate }),
  };
}

/** ``statuses`` empty = every job. The same filter drives the list a count on the page opens. */
export const useJobs = (projectId?: number, statuses: string[] = [], limit = 100) =>
  useQuery({
    queryKey: ["jobs", projectId ?? "all", statuses.join(","), limit],
    queryFn: () => {
      const q = new URLSearchParams({ limit: String(limit) });
      if (projectId) q.set("project_id", String(projectId));
      for (const st of statuses) q.append("status", st);
      return api<Job[]>(`/api/jobs?${q.toString()}`);
    },
    refetchInterval: LIVE_MS,
  });
export const useJobEvents = (id: string | null) =>
  useQuery({
    queryKey: ["job-events", id],
    enabled: id !== null,
    queryFn: () => api<{ id: number; event: string; level: string; data: Record<string, unknown>; ts: string }[]>(`/api/jobs/${id}/events`),
  });
export const useQueueState = (projectId?: number) =>
  useQuery({
    queryKey: ["queue", projectId ?? "all"],
    queryFn: () => api<QueueState>(`/api/queue${projectId ? `?project_id=${projectId}` : ""}`),
    refetchInterval: LIVE_MS,
  });

export function useJobActions() {
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries({ queryKey: ["jobs"] });
  return {
    cancel: useMutation({ mutationFn: (id: string) => post(`/api/jobs/${id}/cancel`), onSuccess: done }),
    retry: useMutation({ mutationFn: (id: string) => post(`/api/jobs/${id}/retry`), onSuccess: done }),
    front: useMutation({ mutationFn: (ids: string[]) => post(`/api/jobs/priority`, { ids, move: "first" }), onSuccess: done }),
  };
}

export const useHealth = () => useQuery({ queryKey: ["health"], queryFn: () => api<Health>("/api/health") });

// ---- transparency: what the models were asked, the prompts, the plan/style, the chain ---------------------------
export const useFlow = (projectId?: number) =>
  useQuery({
    queryKey: ["flow", projectId ?? "all"],
    queryFn: () => api<{ steps: FlowStep[] }>(`/api/flow${projectId ? `?project_id=${projectId}` : ""}`),
    refetchInterval: projectId ? LIVE_MS : false,
  });
export const useAiCalls = (projectId: number, label = "") =>
  useQuery({
    queryKey: ["ai-calls", projectId, label],
    queryFn: () => api<AiCallRow[]>(`/api/projects/${projectId}/ai-calls?limit=200${label ? `&label=${label}` : ""}`),
    refetchInterval: LIVE_MS,
  });
export const useAiCall = (id: number | null) =>
  useQuery({ queryKey: ["ai-call", id], enabled: id !== null, queryFn: () => api<AiCallDetail>(`/api/ai-calls/${id}`) });
export const useAgents = () => useQuery({ queryKey: ["agents"], queryFn: () => api<Agent[]>("/api/agents") });
export function useAgentEdit() {
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries({ queryKey: ["agents"] });
  return {
    save: useMutation({
      mutationFn: ({ name, body }: { name: string; body: AgentEdit }) => put<Agent>(`/api/agents/${name}`, body),
      onSuccess: done,
    }),
    reset: useMutation({ mutationFn: (name: string) => api<Agent>(`/api/agents/${name}`, { method: "DELETE" }), onSuccess: done }),
  };
}
export const useProjectData = (projectId: number, key: "plan" | "style") =>
  useQuery({ queryKey: ["data", projectId, key], queryFn: () => api<Record<string, unknown>>(`/api/projects/${projectId}/data/${key}`) });
export function useSaveProjectData(projectId: number, key: "plan" | "style") {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => put<Record<string, unknown>>(`/api/projects/${projectId}/data/${key}`, body),
    onSuccess: (data) => qc.setQueryData(["data", projectId, key], data),
  });
}

/** The launch screen: re-checks every few seconds, faster while something is starting. */
export const useLaunch = () =>
  useQuery({
    queryKey: ["launch"],
    queryFn: () => api<Launch>("/api/launch"),
    refetchInterval: (q) => (q.state.data?.starting ? 2000 : 8000),
  });
export function useStartAll() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => post<Launch>("/api/launch/start-all"),
    onSuccess: (data) => qc.setQueryData(["launch"], data),
  });
}
/** End a service's process (only the ones the operator allowed with `stop = "port"`); `force` = SIGKILL. */
export function useStopService() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, force }: { name: string; force: boolean }) => post<Launch>(`/api/launch/${name}/stop`, { force }),
    onSuccess: (data) => qc.setQueryData(["launch"], data),
  });
}
export function useStartService() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => post<Launch>(`/api/launch/${name}/start`),
    onSuccess: (data) => qc.setQueryData(["launch"], data),
  });
}

/** What producing the media would make, task by task (nothing runs): the list a person confirms. */
export const useProducePlan = (id: number, tier: string, enabled: boolean, pollMs: number | false = false) =>
  useQuery({
    queryKey: ["produce-plan", id, tier],
    queryFn: () => api<ProducePlan>(`/api/projects/${id}/produce/plan?tier=${tier}`),
    enabled,
    refetchOnWindowFocus: false,
    refetchInterval: pollMs,
  });

/** A project from a written story/script: its text becomes the premise and the source the script stage adapts. */
export function useProjectFromText() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; asset_id: string; delivery_profile?: string; pipeline?: string }) => post<ProjectFromText>("/api/projects/from-text", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}

/** Seasons of a project (season 1 always exists) with how many episodes each holds. */
export const useSeasons = (id: number) =>
  useQuery({ queryKey: ["seasons", id], queryFn: () => api<Row[]>(`/api/projects/${id}/seasons`) });

export const useEpisodes = (id: number) =>
  useQuery({ queryKey: ["episodes", id], queryFn: () => api<Row[]>(`/api/projects/${id}/episodes`), refetchInterval: LIVE_MS });

export function useUpdateSeason(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { number: number; body: Record<string, unknown> }) => patch<Row>(`/api/projects/${id}/seasons/${v.number}`, v.body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["seasons", id] }),
  });
}

/** The dashboard: every season and episode with stage states and how much media is made. */
export const useSeries = (id: number) =>
  useQuery({ queryKey: ["series", id], queryFn: () => api<SeriesBoard>(`/api/projects/${id}/series`), refetchInterval: 5000 });

/** Update and restart: polls quickly while an update runs or a restart is under way (and while the service is away). */
export const useUpdate = () =>
  useQuery({
    queryKey: ["update"],
    queryFn: () => api<UpdateState>("/api/update"),
    retry: false,
    refetchInterval: (q) => {
      if (q.state.error instanceof ApiError) return false; // refused (not an admin, ...): asking again will not help
      const d = q.state.data;
      return q.state.error || (d && (d.busy || d.restart.phase !== "none")) ? 1500 : 15000;
    },
  });
export function useUpdateActions() {
  const qc = useQueryClient();
  const keep = (data: UpdateState) => qc.setQueryData(["update"], data);
  return {
    check: useMutation({ mutationFn: () => post<UpdateState>("/api/update/check"), onSuccess: keep }),
    run: useMutation({
      mutationFn: (body: { restart: boolean; interrupt: boolean }) => post<UpdateState>("/api/update/run", body),
      onSuccess: keep,
    }),
    restart: useMutation({
      mutationFn: (interrupt: boolean) => post<UpdateState>("/api/update/restart", { interrupt }),
      onSuccess: keep,
    }),
    cancel: useMutation({ mutationFn: () => api<UpdateState>("/api/update/restart", { method: "DELETE" }), onSuccess: keep }),
  };
}
