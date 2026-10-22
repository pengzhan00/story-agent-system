import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, del, put } from "./client";
import type { components } from "./schema";

type S = components["schemas"];
export type PipelineView = S["PipelineView"];
export type PipelineRow = S["PipelineRow"];

export const usePipeline = (projectId: number) =>
  useQuery({ queryKey: ["pipeline", projectId], queryFn: () => api<PipelineView>(`/api/projects/${projectId}/pipeline`) });

/** Hand-picked models (kind -> model; empty = the plan's recommendation) and the way back to the defaults. */
export function usePipelineChoices(projectId: number) {
  const qc = useQueryClient();
  const done = () => Promise.all([qc.invalidateQueries({ queryKey: ["pipeline", projectId] }), qc.invalidateQueries({ queryKey: ["project", projectId] }), qc.invalidateQueries({ queryKey: ["produce-plan"] })]);
  return {
    set: useMutation({ mutationFn: (models: Record<string, string>) => put<PipelineView>(`/api/projects/${projectId}/pipeline`, { models }), onSuccess: done }),
    reset: useMutation({ mutationFn: () => del(`/api/projects/${projectId}/pipeline`), onSuccess: done }),
  };
}
