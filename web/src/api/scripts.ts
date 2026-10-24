import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, post } from "./client";
import type { LibraryItem, ScriptBrief, ScriptDoc } from "./types";

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

/** File a script under a name (`library:script/<name>`): a snapshot other projects and the workshop can call. */
export function usePublishScript(projectId: number, episode: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; description: string; replace: boolean }) => post<LibraryItem>(`/api/scripts/${projectId}/${episode}/publish`, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["library"] });
      void qc.invalidateQueries({ queryKey: ["assets"] });
    },
  });
}
