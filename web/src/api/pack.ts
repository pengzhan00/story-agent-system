import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, post } from "./client";
import type { components } from "./schema";

type S = components["schemas"];
export type PackExport = S["PackExportOut"];
export type PackReport = S["PackReportOut"];
export type PackScope = "all" | "assets" | "story";
export type PackPolicy = "skip" | "new_version";

export function usePack(pid: number) {
  const qc = useQueryClient();
  const refresh = () => {
    for (const k of ["assets", "specs", "story", "review", "stale", "spec-assets"]) void qc.invalidateQueries({ queryKey: [k] });
  };
  return {
    exportPack: useMutation({
      mutationFn: ({ scope, history, confirm }: { scope: PackScope; history: boolean; confirm?: boolean }) =>
        post<PackExport>(`/api/projects/${pid}/pack?scope=${scope}&include_history=${history}${confirm ? "&confirm_restricted=true" : ""}`),
    }),
    importPack: useMutation({
      mutationFn: ({ file, policy, dryRun }: { file: File; policy: PackPolicy; dryRun: boolean }) =>
        api<PackReport>(`/api/projects/import-pack?filename=${encodeURIComponent(file.name)}&into=${pid}&on_conflict=${policy}&dry_run=${dryRun}`, {
          method: "POST",
          body: file,
          headers: { "Content-Type": "application/zip" },
        }),
      onSuccess: (_report, vars) => {
        if (!vars.dryRun) refresh();
      },
    }),
  };
}
