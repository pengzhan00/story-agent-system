import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, post } from "./client";
import type { WikiList, WikiPageData, WikiSync } from "./types";

export interface WikiFilter {
  q: string;
  source: string;
  tag: string;
}

export const useWikiList = (f: WikiFilter) => {
  const query = new URLSearchParams();
  if (f.q.trim()) query.set("q", f.q.trim());
  if (f.source) query.set("source", f.source);
  if (f.tag) query.set("tag", f.tag);
  return useQuery({ queryKey: ["wiki", "list", f], queryFn: () => api<WikiList>(`/api/wiki/pages?${query.toString()}`) });
};

export const useWikiPage = (slug: string) =>
  useQuery({
    queryKey: ["wiki", "page", slug],
    queryFn: () => api<WikiPageData>(`/api/wiki/pages/${slug.split("/").map(encodeURIComponent).join("/")}`),
    enabled: slug !== "",
    retry: false,
  });

export function useWikiSync() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => post<WikiSync>("/api/wiki/sync"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["wiki"] }),
  });
}
