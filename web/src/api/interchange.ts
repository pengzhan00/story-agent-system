import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { components } from "./schema";

type S = components["schemas"];
export type EditDiff = S["EditDiffOut"];

const upload = <T,>(path: string, file: File, type: string) => api<T>(path, { method: "POST", body: file, headers: { "Content-Type": type } });

export function useInterchange(pid: number, episode: number) {
  const qc = useQueryClient();
  const base = `/api/projects/${pid}/episodes/${episode}/fcp`;
  return {
    downloadUrl: base,
    diff: useMutation({ mutationFn: (file: File) => upload<EditDiff>(`${base}/diff?filename=${encodeURIComponent(file.name)}`, file, "application/xml") }),
    final: useMutation({
      mutationFn: ({ file, note }: { file: File; note: string }) =>
        upload<S["AssetOut"]>(`${base}/final?filename=${encodeURIComponent(file.name)}&note=${encodeURIComponent(note)}`, file, "video/mp4"),
      onSuccess: () => {
        for (const k of ["assets", "stale", "review"]) void qc.invalidateQueries({ queryKey: [k] });
      },
    }),
  };
}
