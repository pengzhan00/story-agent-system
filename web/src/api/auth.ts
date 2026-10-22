import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, patch, post } from "./client";
import type { components } from "./schema";

type S = components["schemas"];
export type AuthMode = S["AuthModeOut"];
export type Me = S["MeOut"];
export type AccountRow = S["UserOut"];
export type AuditRow = S["AuditOut"];
export type UserPatch = S["UserPatchBody"];

export const UNAUTHORIZED_EVENT = "storyagent:unauthorized";

/** Public: tells the client whether sign-in is on at all, and which languages exist before anyone is signed in. */
export const useAuthMode = () => useQuery({ queryKey: ["auth", "mode"], queryFn: () => api<AuthMode>("/api/auth/mode") });
export const useMe = () => useQuery({ queryKey: ["auth", "me"], queryFn: () => api<Me>("/api/auth/me"), retry: false });

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { username: string; password: string }) => post<S["LoginOut"]>("/api/auth/login", body),
    onSuccess: () => qc.invalidateQueries(),
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => post<void>("/api/auth/logout"),
    onSuccess: () => {
      qc.clear();
      void qc.invalidateQueries({ queryKey: ["auth"] });
    },
  });
}

export const useChangePassword = () =>
  useMutation({
    mutationFn: (body: { old_password: string; new_password: string }) => post<void>("/api/auth/password", body),
  });

export const useAccounts = () => useQuery({ queryKey: ["users"], queryFn: () => api<AccountRow[]>("/api/users") });

export function useCreateAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { username: string; password: string; role: string; display_name: string }) =>
      post<AccountRow>("/api/users", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });
}

export function usePatchAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: UserPatch & { id: number }) => patch<AccountRow>(`/api/users/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });
}

export const useAudit = (filter: { user: string; action: string; project_id: string }) => {
  const q = new URLSearchParams({ limit: "200" });
  if (filter.user) q.set("user", filter.user);
  if (filter.action) q.set("action", filter.action);
  if (filter.project_id) q.set("project_id", filter.project_id);
  return useQuery({ queryKey: ["audit", filter], queryFn: () => api<AuditRow[]>(`/api/audit?${q.toString()}`), refetchInterval: 10000 });
};
