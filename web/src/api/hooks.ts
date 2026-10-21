import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, del, patch, post, put } from "./client";
import type { Agent, AgentEdit, AiCallDetail, AiCallRow, FlowStep, Health, Launch, ProjectCard, ScriptReview, Job, Meta, Project, QueueState, Row, WorkflowOverview, WorkflowRun } from "./types";
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
    mutationFn: (body: { name: string; description: string; content_language: string }) =>
      post<Project>("/api/projects", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
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
    mutationFn: (body: { target_sec: number | null }) => patch<Project>(`/api/projects/${id}/settings`, body),
    onSuccess: (p) => qc.setQueryData(["project", id], p),
  });
}

export function useUpdateProject(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Partial<Pick<Project, "name" | "description" | "genre" | "delivery_profile">>) =>
      patch<Project>(`/api/projects/${id}`, body),
    onSuccess: (p) => qc.setQueryData(["project", id], p),
  });
}

export const useWorkflow = (id: number) =>
  useQuery({
    queryKey: ["workflow", id],
    queryFn: () => api<WorkflowOverview>(`/api/projects/${id}/workflow`),
    refetchInterval: (q) => (q.state.data?.active_run ? LIVE_MS : false),
  });

export function useStartRun(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { stages?: string[]; params?: Record<string, unknown> }) =>
      post<WorkflowRun>(`/api/projects/${id}/workflow/runs`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workflow", id] }),
  });
}

export const useReviewScript = (id: number) =>
  useMutation({ mutationFn: () => post<ScriptReview>(`/api/projects/${id}/review`) });

export function useCancelRun(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) => post<WorkflowRun>(`/api/workflow/runs/${runId}/cancel`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workflow", id] }),
  });
}

export const STORY_PARTS = ["characters", "locations", "music", "sfx", "shots", "dialogue", "scripts"] as const;
export type StoryPart = (typeof STORY_PARTS)[number];
export const useStory = (id: number, part: StoryPart) =>
  useQuery({ queryKey: ["story", id, part], queryFn: () => api<Row[]>(`/api/projects/${id}/${part}`) });

export function useStoryEdit(id: number, part: StoryPart) {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["story", id, part] });
  return {
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
export const useJobs = (projectId?: number, statuses: string[] = []) =>
  useQuery({
    queryKey: ["jobs", projectId ?? "all", statuses.join(",")],
    queryFn: () => {
      const q = new URLSearchParams({ limit: "100" });
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
export function useStartService() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => post<Launch>(`/api/launch/${name}/start`),
    onSuccess: (data) => qc.setQueryData(["launch"], data),
  });
}
