import { useMutation, useQuery } from "@tanstack/react-query";
import { api, post } from "./client";
import type { components } from "./schema";

type S = components["schemas"];
export type Platform = S["PlatformOut"];
export type Delivery = S["DeliveryOut"];

export function usePlatforms() {
  return useQuery({ queryKey: ["platforms"], queryFn: () => api<Platform[]>("/api/platforms") });
}

export function useDelivery(pid: number, episode: number) {
  return useMutation({
    mutationFn: ({ platform, languages }: { platform: string; languages: string[] }) =>
      post<Delivery>(`/api/projects/${pid}/episodes/${episode}/delivery`, { platform, languages }),
  });
}
