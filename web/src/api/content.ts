import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, post } from "./client";
import type { components } from "./schema";

type S = components["schemas"];
export type ContentStatus = S["ContentStatusOut"];

export const useContentStatus = () => useQuery({ queryKey: ["content-status"], queryFn: () => api<ContentStatus>("/api/content/status"), staleTime: 15_000 });

/** Unlocking or locking changes what every list may show, so everything is fetched again. */
export function useContentLock() {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries();
  return {
    unlock: useMutation({
      mutationFn: (body: { confirm_adult: boolean; password: string }) => post<ContentStatus>("/api/content/unlock", body),
      onSuccess: refresh,
    }),
    lock: useMutation({ mutationFn: () => api<ContentStatus>("/api/content/unlock", { method: "DELETE" }), onSuccess: refresh }),
  };
}
