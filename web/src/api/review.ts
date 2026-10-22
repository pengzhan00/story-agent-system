import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, patch, post } from "./client";
import type { components } from "./schema";

type S = components["schemas"];
export type ReviewShot = S["ReviewShotOut"];
export type Note = S["NoteOut"];
export type NoteCompare = S["NoteCompareOut"];
export type RedoBody = S["NoteRedoBody"];

const LIVE_MS = 2500;

export const useReviewBoard = (pid: number) =>
  useQuery({ queryKey: ["review", "board", pid], queryFn: () => api<ReviewShot[]>(`/api/projects/${pid}/review/board`), refetchInterval: 5000 });

export const useNotes = (pid: number, shotId: number | null) =>
  useQuery({
    queryKey: ["review", "notes", pid, shotId],
    enabled: shotId !== null,
    queryFn: () => api<Note[]>(`/api/projects/${pid}/notes?shot_id=${shotId}`),
    refetchInterval: LIVE_MS,
  });

export const useCompare = (pid: number, noteId: number, enabled: boolean) =>
  useQuery({
    queryKey: ["review", "compare", pid, noteId],
    enabled,
    queryFn: () => api<NoteCompare>(`/api/projects/${pid}/notes/${noteId}/compare`),
    refetchInterval: LIVE_MS,
  });

export function useReviewActions(pid: number) {
  const qc = useQueryClient();
  const refresh = () => {
    for (const k of ["review", "specs", "assets", "spec-assets", "jobs", "stale"]) void qc.invalidateQueries({ queryKey: [k] });
  };
  return {
    add: useMutation({
      mutationFn: (body: { subject_type: string; subject_id: string; slot: string; shot_id: number; asset_id: string | null; body: string }) =>
        post<Note>(`/api/projects/${pid}/notes`, body),
      onSuccess: refresh,
    }),
    status: useMutation({
      mutationFn: ({ id, status, resolution }: { id: number; status: "open" | "resolved" | "dismissed"; resolution?: string }) =>
        patch<Note>(`/api/projects/${pid}/notes/${id}`, { status, resolution: resolution ?? "" }),
      onSuccess: refresh,
    }),
    redo: useMutation({
      mutationFn: ({ id, ...body }: Partial<RedoBody> & { id: number }) => post<S["NoteRedoOut"]>(`/api/projects/${pid}/notes/${id}/redo`, body),
      onSuccess: refresh,
    }),
    adopt: useMutation({ mutationFn: (assetId: string) => post<S["AssetOut"]>(`/api/assets/${assetId}/adopt`), onSuccess: refresh }),
  };
}
