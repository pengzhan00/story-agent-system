import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, del, patch, post, put } from "./client";
import type { Asset, Job, ReviewItem, Spec, SpecDiffItem, SpecEdit } from "./types";

const LIVE_MS = 2000;
const base = (pid: number, s: Pick<Spec, "subject_type" | "subject_id" | "slot">) =>
  `/api/projects/${pid}/specs/${encodeURIComponent(s.subject_type)}/${encodeURIComponent(s.subject_id)}`;
const slotQ = (s: Pick<Spec, "slot">) => `slot=${encodeURIComponent(s.slot)}`;

export const useSpecs = (pid: number) =>
  useQuery({ queryKey: ["specs", pid], queryFn: () => api<Spec[]>(`/api/projects/${pid}/specs`) });
export const useReview = (pid: number) =>
  useQuery({ queryKey: ["spec-review", pid], queryFn: () => api<ReviewItem[]>(`/api/projects/${pid}/specs/review`) });

export const useVersions = (pid: number, s: Spec | null) =>
  useQuery({
    queryKey: ["spec-versions", pid, s?.id, s?.version],
    enabled: !!s,
    queryFn: () => api<Spec[]>(`${base(pid, s!)}/versions?${slotQ(s!)}`),
  });

export const useDiff = (pid: number, s: Spec | null, v1: number | null, v2: number | null) =>
  useQuery({
    queryKey: ["spec-diff", pid, s?.id, v1, v2],
    enabled: !!s && v1 !== null && v2 !== null && v1 !== v2,
    queryFn: () => api<SpecDiffItem[]>(`${base(pid, s!)}/diff?v1=${v1}&v2=${v2}&${slotQ(s!)}`),
  });

export const useSpecAssets = (pid: number, s: Spec | null) =>
  useQuery({
    queryKey: ["spec-assets", pid, s?.id],
    enabled: !!s,
    queryFn: () => api<Asset[]>(`/api/assets?project_id=${pid}&spec_id=${s!.id}&limit=100`),
    refetchInterval: LIVE_MS,
  });

export const useSpecJobs = (s: Spec | null) =>
  useQuery({
    queryKey: ["jobs", "spec", s?.id],
    enabled: !!s,
    queryFn: () => api<Job[]>(`/api/jobs?spec_id=${s!.id}&limit=50`),
    refetchInterval: LIVE_MS,
  });

export function useSpecActions(pid: number, s: Spec | null) {
  const qc = useQueryClient();
  const refresh = () => {
    for (const k of ["specs", "spec-review", "spec-versions", "spec-assets", "jobs", "stale"]) {
      qc.invalidateQueries({ queryKey: [k] });
    }
  };
  const target = s!;
  return {
    edit: useMutation({ mutationFn: (body: SpecEdit) => patch<Spec>(`${base(pid, target)}?${slotQ(target)}`, body), onSuccess: refresh }),
    revert: useMutation({
      mutationFn: (version: number) => post<Spec>(`${base(pid, target)}/revert?${slotQ(target)}`, { version }),
      onSuccess: refresh,
    }),
    lock: useMutation({ mutationFn: () => put<Spec>(`${base(pid, target)}/lock?${slotQ(target)}`, {}), onSuccess: refresh }),
    unlock: useMutation({ mutationFn: () => del(`${base(pid, target)}/lock?${slotQ(target)}`), onSuccess: refresh }),
    acknowledge: useMutation({ mutationFn: () => post<Spec>(`${base(pid, target)}/acknowledge?${slotQ(target)}`), onSuccess: refresh }),
    regenerate: useMutation({
      mutationFn: (body: { n_candidates: number; tier: string; vary_seed: boolean }) =>
        post<Job[]>(`${base(pid, target)}/regenerate?${slotQ(target)}`, body),
      onSuccess: refresh,
    }),
    adopt: useMutation({ mutationFn: (assetId: string) => post<Asset>(`/api/assets/${assetId}/adopt`), onSuccess: refresh }),
  };
}
