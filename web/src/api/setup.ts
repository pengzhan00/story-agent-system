import { useMutation, useQuery } from "@tanstack/react-query";
import { api, post } from "./client";
import type { components } from "./schema";

type S = components["schemas"];
export type SetupState = S["SetupOut"];
export type SetupComponent = S["SetupComponentOut"];
export type SetupStep = S["SetupStepOut"];
export type SetupConfigWritten = S["SetupConfigWritten"];
export type SetupVerify = S["SetupVerifyOut"];

export interface SetupChoice {
  profile: string;
  root: string;
}

const query = (c: SetupChoice) => `profile=${encodeURIComponent(c.profile)}&root=${encodeURIComponent(c.root)}`;

export const useSetup = (c: SetupChoice) =>
  useQuery({ queryKey: ["setup", c.profile, c.root], queryFn: () => api<SetupState>(`/api/setup?${query(c)}`) });

export const useSetupConfig = (c: SetupChoice, llmModel: string) =>
  useQuery({
    queryKey: ["setup", "config", c.profile, c.root, llmModel],
    queryFn: () => api<S["SetupConfigOut"]>(`/api/setup/config?${query(c)}&llm_model=${encodeURIComponent(llmModel)}`),
  });

export const useWriteSetupConfig = () =>
  useMutation({
    mutationFn: (v: SetupChoice & { llm_model: string }) =>
      post<SetupConfigWritten>("/api/setup/config", { profile: v.profile, root: v.root, llm_model: v.llm_model, write: true }),
  });

export const useVerifySetup = () => useMutation({ mutationFn: (c: SetupChoice) => post<SetupVerify>(`/api/setup/verify?${query(c)}`) });
