import { useQuery } from "@tanstack/react-query";
import { api } from "./client";
import type { ScriptBrief, ScriptDoc } from "./types";

/** Every project's scripts (title, counts); a locked restricted project comes without its words. */
export const useScripts = (q: string, projectId: number | null) => {
  const query = new URLSearchParams();
  if (q.trim()) query.set("q", q.trim());
  if (projectId !== null) query.set("project_id", String(projectId));
  return useQuery({ queryKey: ["scripts", q.trim(), projectId], queryFn: () => api<ScriptBrief[]>(`/api/scripts?${query.toString()}`) });
};

/** One script as Markdown text, fetched only when it is opened. */
export const useScriptDoc = (projectId: number, episode: number, enabled: boolean) =>
  useQuery({ queryKey: ["scripts", "doc", projectId, episode], queryFn: () => api<ScriptDoc>(`/api/scripts/${projectId}/${episode}`), enabled, retry: false });
