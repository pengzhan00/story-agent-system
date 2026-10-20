import type { components } from "./schema";

type S = components["schemas"];
export type Project = S["ProjectOut"];
export type Job = S["JobOut"];
export type Meta = S["Meta"];
export type WorkflowRun = S["WorkflowRunOut"];
export type ScriptReview = S["ScriptReviewOut"];
export type RunProgress = S["RunProgressOut"];
export type StageProgress = S["StageProgressOut"];
export type WorkflowOverview = S["WorkflowOut"];
export type WorkflowStage = S["WorkflowStageOut"];
export type Health = S["HealthOut"];
export type QueueState = S["QueueState"];
export type Row = Record<string, unknown>;
export type Spec = S["SpecOut"];
export type SpecEdit = S["SpecEdit"];
export type SpecDiffItem = S["SpecDiffItem"];
export type ReviewItem = S["ReviewItemOut"];
export type Asset = S["AssetOut"];
export type StaleAsset = S["StaleAsset"];
export type ProjectCard = S["ProjectCard"];
export type Agent = S["AgentOut"];
export type AgentEdit = S["AgentEdit"];
export type AiCallRow = S["AiCallRow"];
export type AiCallDetail = S["AiCallDetail"];

export interface FlowModel {
  ref: string;
  known: boolean;
  provider?: string;
  kind?: string;
  heavy?: boolean;
  license_tag?: string;
  commercial_ok?: boolean;
  params?: Record<string, Record<string, unknown>>;
}
export interface FlowMedia {
  subject: string;
  kind: string;
  phase: number;
  counts: { specs: number; made: number; stale: number; models: Record<string, number> } | null;
  models: FlowModel[];
}
export interface FlowAgent {
  name: string;
  optional: boolean;
  role: string;
  backend: string;
  model: string;
  description: string;
}
export interface FlowStep {
  stage: string;
  how: "llm" | "rules" | "media" | "compose";
  reads: string[];
  writes: string[];
  agents: FlowAgent[];
  media: FlowMedia[];
  status: string | null;
  summary: Record<string, unknown> | null;
}
