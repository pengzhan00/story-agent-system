import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, post } from "./client";
import type { components } from "./schema";

type S = components["schemas"];
export type GrammarReport = S["GrammarReportOut"];
export type GrammarViolation = S["GrammarViolationOut"];

export const useGrammar = (pid: number, episode: number | null) =>
  useQuery({
    queryKey: ["grammar", pid, episode],
    queryFn: () => api<GrammarReport>(`/api/projects/${pid}/grammar${episode === null ? "" : `?episode=${episode}`}`),
  });

export function useGrammarFix(pid: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { episode: number | null; rules?: string[]; shot_ids?: number[] }) => post<S["GrammarFixOut"]>(`/api/projects/${pid}/grammar/fix`, body),
    onSuccess: () => {
      for (const k of ["grammar", "story", "review"]) void qc.invalidateQueries({ queryKey: [k] });
    },
  });
}
