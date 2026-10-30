import { api } from "./client";
import type { components } from "./schema";

export type Capability = {
  id: string; label_key: string; outputs: Record<string, string>;
  inputs: { name: string; type: string; media: string; required: boolean }[];
  bindings: { id: string; provider: string; verification: string; memory_gib: number }[];
};
export type PlanNode = {
  id: string; kind: string; model_ref: string; inputs: Record<string, unknown>;
  params: Record<string, unknown>; review: boolean; depends_on?: string[];
};
export type Plan = { name: string; project_id?: number; nodes: PlanNode[] };
export type ControlRun = components["schemas"]["ControlRunOut"];
export type Target = components["schemas"]["TargetOut"];
export type Category = components["schemas"]["CategoryOut"];
export type Publication = components["schemas"]["PublicationOut"];
export const controlGet = <T>(path: string) => api<T>(path);
