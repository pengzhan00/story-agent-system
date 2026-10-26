import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, del, post } from "./client";
import type { Asset, LibraryItem } from "./types";

const LIVE_MS = 4000;

/** Everything ever made or uploaded, newest first (workshop results and every project). */
export const useAllAssets = () =>
  useQuery({
    queryKey: ["assets", "all"],
    queryFn: () => api<Asset[]>("/api/assets?newest_first=true&limit=1000"),
    refetchInterval: LIVE_MS,
  });

export const useLibraryItems = () =>
  useQuery({ queryKey: ["library"], queryFn: () => api<LibraryItem[]>("/api/library"), refetchInterval: LIVE_MS });

export const useLibraryRoles = () => useQuery({ queryKey: ["library", "roles"], queryFn: () => api<string[]>("/api/library/roles") });

export function useLibraryActions() {
  const qc = useQueryClient();
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["library"] });
    void qc.invalidateQueries({ queryKey: ["assets"] });
  };
  return {
    publish: useMutation({
      mutationFn: (b: { asset_id: string; role: string; name: string; description?: string; meta?: Record<string, unknown>; replace?: boolean }) =>
        post<LibraryItem>("/api/library", b),
      onSuccess: refresh,
    }),
    remove: useMutation({ mutationFn: (id: string) => del(`/api/library/${id}`), onSuccess: refresh }),
    trash: useMutation({ mutationFn: (id: string) => post<Asset>(`/api/assets/${id}/trash`), onSuccess: refresh }),
    purge: useMutation({ mutationFn: (id: string) => post<Asset>(`/api/assets/${id}/purge`), onSuccess: refresh }),
  };
}
