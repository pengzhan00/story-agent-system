import { useMutation } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";
import type { Preflight, PreflightFinding } from "../api/types";
import { useT, type T } from "../i18n";

const ABOUT_REFERENCES = /^(too_many_refs|refs_dropped)/;

/** One finding of the readiness check as a sentence: what is missing, for what, and what would take it. */
export function findingText(t: T, f: PreflightFinding): string {
  const values = { ...(f.values ?? {}) };
  if (values["what"]) values["what"] = t(`spec.subject.${values["what"]}`);
  const text = t(`preflight.${f.code}`, { subject: f.subject, detail: f.detail, ...values });
  return ABOUT_REFERENCES.test(f.code) && f.detail ? `${text} ${t("preflight.people", { names: f.detail })}` : text;
}

/** The findings a refused run carries in its error (`not_ready`), or none when the error is something else. */
export function refusedFor(error: unknown): PreflightFinding[] {
  if (!(error instanceof ApiError) || error.code !== "not_ready" || !Array.isArray(error.detail)) return [];
  return error.detail as PreflightFinding[];
}

export function Findings({ items }: { items: PreflightFinding[] }) {
  const t = useT();
  return (
    <ul className="plain stack small">
      {items.map((f) => (
        <li key={`${f.code}:${f.subject}:${f.values?.["what"] ?? ""}`}>{findingText(t, f)}</li>
      ))}
    </ul>
  );
}

/** Asks the readiness check for one kind of run (`query`: `episode=1`, or `stages=produce&tier=draft`). */
export function useReadyCheck(id: number, query: string) {
  return useMutation({ mutationFn: () => api<Preflight>(`/api/projects/${id}/preflight?${query}`) });
}

/** What the readiness check said: ready (and what the run will do), or what stops it; notes either way. */
export function ReadyResult({ data }: { data: Preflight | undefined }) {
  const t = useT();
  if (!data) return null;
  const blocks = data.findings.filter((f) => f.level === "block");
  const notes = data.findings.filter((f) => f.level !== "block");
  return (
    <div className="stack small" role="status">
      {data.ready ? (
        <p>{t("web.preflight.ready", { stages: data.stages.map((s) => t(`workflow.stage.${s}`)).join(" → ") })}</p>
      ) : (
        <>
          <strong>{t("web.preflight.blocked")}</strong>
          <Findings items={blocks} />
        </>
      )}
      {notes.length > 0 ? (
        <>
          <span className="muted">{t("web.preflight.notes")}</span>
          <Findings items={notes} />
        </>
      ) : null}
    </div>
  );
}

/** The check as a button with its answer under it: for the places that start a run of single stages. */
export function ReadyCheck({ id, query, disabled = false }: { id: number; query: string; disabled?: boolean }) {
  const t = useT();
  const check = useReadyCheck(id, query);
  return (
    <div className="stack small">
      <div className="row">
        <button type="button" disabled={disabled || check.isPending} onClick={() => check.mutate()}>
          {check.isPending ? t("web.preflight.checking") : t("web.preflight.check")}
        </button>
      </div>
      <ReadyResult data={check.data} />
    </div>
  );
}
