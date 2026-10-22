import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, del, post, put } from "./client";
import type { Asset, Job, PipelineResult, WorkshopHistoryItem, WorkshopModule, WorkshopParams } from "./types";

const LIVE_MS = 2000;

export interface Step {
  kind: string;
  inputs: Record<string, unknown>;
  model_ref?: string;
  tier?: string;
  params?: Record<string, unknown>;
  seed?: number | null;
}

/** The modules; with a plan, they start from that plan's models and say which models are outside it (a trial only). */
export const useWorkshop = (pipeline = "") =>
  useQuery({ queryKey: ["workshop", "modules", pipeline], queryFn: () => api<WorkshopModule[]>(`/api/workshop${pipeline ? `?${new URLSearchParams({ pipeline })}` : ""}`) });

export const useWorkshopParams = (kind: string, model: string, tier: string) =>
  useQuery({
    queryKey: ["workshop", "params", kind, model, tier],
    enabled: model !== "",
    queryFn: () => api<WorkshopParams>(`/api/workshop/params?${new URLSearchParams({ kind, model_ref: model, tier })}`),
  });

export const useWorkshopHistory = (module: string) =>
  useQuery({
    queryKey: ["workshop", "history", module],
    queryFn: () => api<WorkshopHistoryItem[]>(`/api/workshop/history?${new URLSearchParams({ module, limit: "30" })}`),
    refetchInterval: LIVE_MS,
  });

export function useWorkshopActions() {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: ["workshop"] });
  return {
    run: useMutation({
      mutationFn: (b: Step & { count: number }) => post<Job[]>("/api/workshop/run", b),
      onSuccess: refresh,
    }),
    pipeline: useMutation({
      mutationFn: (steps: Step[]) => post<PipelineResult>("/api/workshop/pipelines", { steps }),
      onSuccess: refresh,
    }),
    save: useMutation({
      mutationFn: (b: { kind: string; model_ref: string; tier: string; params: Record<string, unknown> }) =>
        put(`/api/workshop/presets/${b.kind}`, { model_ref: b.model_ref, tier: b.tier, params: b.params }),
      onSuccess: refresh,
    }),
    reset: useMutation({ mutationFn: (kind: string) => del(`/api/workshop/presets/${kind}`), onSuccess: refresh }),
    upload: useMutation({
      mutationFn: async (file: File) => {
        const res = await fetch(`/api/workshop/upload?${new URLSearchParams({ filename: file.name })}`, { method: "POST", body: file });
        if (!res.ok) throw new Error(String(res.status));
        return (await res.json()) as Asset;
      },
    }),
  };
}
