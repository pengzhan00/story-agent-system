import { useMutation } from "@tanstack/react-query";
import { post } from "./client";

export interface PruneOrphansResult {
  specs: number;
  assets: number;
}

export interface PruneHistoryResult {
  days: number;
  total: number;
  deleted: Record<string, number>;
}

/** Maintenance buttons on the system page: dry-run previews and the real cleanup. */
export function useMaintainActions() {
  return {
    pruneOrphans: useMutation({
      mutationFn: (p: { projectId: number; dryRun: boolean }) =>
        post<PruneOrphansResult>(`/api/maintain/prune-orphans?project_id=${p.projectId}&dry_run=${p.dryRun}`),
    }),
    pruneHistory: useMutation({
      mutationFn: (p: { days: number; projectId?: number; dryRun: boolean }) =>
        post<PruneHistoryResult>(
          `/api/maintain/prune-history?days=${p.days}&dry_run=${p.dryRun}` +
            (p.projectId !== undefined ? `&project_id=${p.projectId}` : ""),
        ),
    }),
  };
}
