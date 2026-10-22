import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, del, post, put } from "./client";
import type { components } from "./schema";

type S = components["schemas"];
export type LlmState = S["LlmOut"];
export type LlmRole = S["LlmRoleOut"];
export type LlmBackend = S["LlmBackendOut"];
export type PromptAssistResult = S["PromptAssistOut"];

const key = (projectId: number | null) => ["llm", projectId] as const;
const query = (projectId: number | null) => (projectId == null ? "" : `?project_id=${projectId}`);

/** The backends with their installed models, and the model of every role (of a project, when one is given). */
export const useLlm = (projectId: number | null) =>
  useQuery({ queryKey: key(projectId), queryFn: () => api<LlmState>(`/api/llm${query(projectId)}`) });

export function useSetLlmRole(projectId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { role: string; backend: string; model: string }) =>
      put<LlmState>(`/api/llm/roles/${v.role}`, { backend: v.backend, model: v.model, project_id: projectId }),
    onSuccess: (data) => qc.setQueryData(key(projectId), data),
  });
}

export function useResetLlmRole(projectId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (role: string) => {
      await del(`/api/llm/roles/${role}${query(projectId)}`);
      return api<LlmState>(`/api/llm${query(projectId)}`);
    },
    onSuccess: (data) => qc.setQueryData(key(projectId), data),
  });
}

export const useTestLlm = () =>
  useMutation({ mutationFn: (v: { backend: string; model: string }) => post<S["LlmTestOut"]>("/api/llm/test", v) });

/** The AI buttons beside a prompt: a suggestion, or a translation. Nothing is saved by the call. */
export const usePromptAssist = () =>
  useMutation({
    mutationFn: (v: {
      action: "suggest" | "translate";
      text: string;
      purpose: string;
      instruction?: string;
      source?: string;
      target?: string;
      project_id?: number | null;
    }) => post<PromptAssistResult>("/api/ai/prompt", v),
  });
