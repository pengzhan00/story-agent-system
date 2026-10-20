import type { components } from "./schema";

type S = components["schemas"];
export type Project = S["ProjectOut"];
export type Job = S["JobOut"];
export type Meta = S["Meta"];
export type WorkflowRun = S["WorkflowRunOut"];
export type RunProgress = S["RunProgressOut"];
export type StageProgress = S["StageProgressOut"];
export type WorkflowOverview = S["WorkflowOut"];
export type WorkflowStage = S["WorkflowStageOut"];
export type Health = S["HealthOut"];
export type QueueState = S["QueueState"];
export type Row = Record<string, unknown>;
