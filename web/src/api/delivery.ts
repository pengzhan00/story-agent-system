import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, post } from "./client";
import type { components } from "./schema";

type S = components["schemas"];
export type Platform = S["PlatformOut"];
export type Delivery = S["DeliveryOut"];
export type DeliveryJob = S["DeliveryJobOut"];

export function usePlatforms() {
  return useQuery({ queryKey: ["platforms"], queryFn: () => api<Platform[]>("/api/platforms") });
}

/** The finished deliveries of a project; refreshed while a delivery job is still running. */
export function useDeliveries(pid: number, live: boolean) {
  return useQuery({ queryKey: ["deliveries", pid], queryFn: () => api<Delivery[]>(`/api/projects/${pid}/deliveries`), refetchInterval: live ? 2000 : false });
}

export function useDelivery(pid: number, episode: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ platform, languages, confirm }: { platform: string; languages: string[]; confirm?: boolean }) =>
      post<DeliveryJob>(`/api/projects/${pid}/episodes/${episode}/delivery`, { platform, languages, ...(confirm ? { confirm_restricted: true } : {}) }),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: ["jobs"] }), qc.invalidateQueries({ queryKey: ["deliveries", pid] })]),
  });
}
