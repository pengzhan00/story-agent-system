import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, post } from "./client";
import type { components } from "./schema";

type S = components["schemas"];
export type ToolPreview = S["ToolImportPreview"];
export type ToolModel = S["ToolModel"];
export type ToolSaved = S["ToolSaved"];
export type ToolSave = S["ToolSaveRequest"];
export type ToolTemplate = S["ToolTemplate"];
export type ToolChecked = S["ToolChecked"];
export type ToolConnectorSaved = S["ToolConnectorSaved"];
export type ToolSnippet = S["ToolSnippet"];

export const useToolModels = () => useQuery({ queryKey: ["tools"], queryFn: () => api<ToolModel[]>("/api/tools") });

export const usePreviewComfy = () =>
  useMutation({
    mutationFn: (v: { workflow: Record<string, unknown>; kind: string }) => post<ToolPreview>("/api/tools/import/comfy", v),
  });

export function useSaveComfy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: ToolSave) => post<ToolSaved>("/api/tools/comfy", v),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tools"] }),
  });
}

export const useToolTemplates = () =>
  useQuery({ queryKey: ["tools", "templates"], queryFn: () => api<ToolTemplate[]>("/api/tools/templates"), staleTime: Infinity });

export const useCheckConnector = () =>
  useMutation({ mutationFn: (v: ToolSnippet) => post<ToolChecked>("/api/tools/connector/check", v) });

export function useSaveConnector() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: ToolSnippet) => post<ToolConnectorSaved>("/api/tools/connector", v),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tools"] }),
  });
}
