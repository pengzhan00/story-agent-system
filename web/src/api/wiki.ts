import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, post } from "./client";
import type { WikiGraphData, WikiList, WikiPageData, WikiSync } from "./types";

export interface WikiFilter {
  q: string;
  source: string;
  tag: string;
}

export interface GraphFilter extends WikiFilter {
  focus: string;
  depth: 1 | 2;
  tags: boolean;
  shared: boolean;
  models: boolean;
  limit: number;
}

export const useWikiGraph = (f: GraphFilter, enabled = true) => {
  const query = new URLSearchParams();
  if (f.q.trim()) query.set("q", f.q.trim());
  if (f.source) query.set("source", f.source);
  if (f.tag) query.set("tag", f.tag);
  if (f.focus) {
    query.set("focus", f.focus);
    query.set("depth", String(f.depth));
  }
  if (f.tags) query.set("include_tags", "true");
  if (f.shared) query.set("shared_tags", "true");
  if (f.models) query.set("include_models", "true");
  query.set("limit", String(f.limit));
  return useQuery({ queryKey: ["wiki", "graph", f], queryFn: () => api<WikiGraphData>(`/api/wiki/graph?${query.toString()}`), enabled, retry: false, placeholderData: keepPreviousData });
};

export const useWikiList = (f: WikiFilter, enabled = true) => {
  const query = new URLSearchParams();
  if (f.q.trim()) query.set("q", f.q.trim());
  if (f.source) query.set("source", f.source);
  if (f.tag) query.set("tag", f.tag);
  return useQuery({ queryKey: ["wiki", "list", f], queryFn: () => api<WikiList>(`/api/wiki/pages?${query.toString()}`), enabled });
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
