import { useStartRun } from "../api/hooks";
import { useT } from "../i18n";
import { ErrorNote } from "./ErrorNote";
import { ReadyResult, useReadyCheck } from "./Findings";

type Row = Record<string, unknown>;
const num = (v: unknown) => (typeof v === "number" ? v : 0);
const rows = (v: unknown): Row[] => (Array.isArray(v) ? (v as Row[]) : []);
const names = (v: unknown): string => (Array.isArray(v) ? v.map(String).join(", ") : "");

/**
 * One click from where the project stands to the film: the story (with its coherence checks), every asset, the cut.
 * What the run needs is looked at first; a run that cannot start says exactly what to start or install.
 */
export function FullRun({ id, episode, episodes, disabled }: { id: number; episode: number; episodes: string; disabled: boolean }) {
  const t = useT();
  const start = useStartRun(id);
  const which = episodes.trim() ? `episodes=${encodeURIComponent(episodes.trim())}` : `episode=${episode}`;
  const check = useReadyCheck(id, which);
  const go = () => {
    check.reset();
    start.mutate({ params: { full: true, ...(episodes.trim() ? { episodes: episodes.trim() } : { episode }) } });
  };
  return (
    <div className="stack">
      <div className="row">
        <button type="button" className="primary" disabled={disabled || start.isPending} onClick={go}>
          {t("web.workflow.make_everything")}
        </button>
        <button type="button" disabled={disabled || check.isPending} onClick={() => check.mutate()}>
          {check.isPending ? t("web.preflight.checking") : t("web.preflight.check")}
        </button>
      </div>
      <p className="muted small">
        {t("web.workflow.make_everything_hint")} {t("web.workflow.needs_worker")}
      </p>
      {start.error ? null : <ReadyResult data={check.data} />}
      <ErrorNote error={start.error ?? check.error} />
    </div>
  );
}

function CheckPart({ label, part }: { label: string; part: Row }) {
  const t = useT();
  const open = rows(part["open"]);
  const rewritten = names(part["rewritten"]);
  const proposed = names(part["proposed"]);
  return (
    <div className="stack small">
      <span>{t("web.storycheck.line", { part: label, score: num(part["score"]).toFixed(1), rounds: num(part["rounds"]) })}</span>
      {rewritten ? <span className="muted">{t("web.storycheck.rewritten", { scenes: rewritten })}</span> : null}
      {proposed ? <span>{t("web.storycheck.proposed", { scenes: proposed })}</span> : null}
      {open.length === 0 ? (
        <span className="muted">{t("web.storycheck.clean")}</span>
      ) : (
        <>
          <span>{t("web.storycheck.open", { n: open.length, high: num(part["open_high"]) })}</span>
          <ul className="plain">
            {open.map((i, n) => (
              <li key={n}>
                <span className="muted">{`[${String(i["severity"] ?? "")}] ${String(i["where"] ?? "")}`}</span> {String(i["problem"] ?? "")}
                {i["fix"] ? <span className="muted">{` → ${String(i["fix"])}`}</span> : null}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

/** What the story checks of the script stage found and did: the score, what went back to the writer, what is open. */
export function StoryCheck({ summary }: { summary: Row | undefined }) {
  const t = useT();
  const check = summary?.["story_check"];
  if (!check || typeof check !== "object") return null;
  const parts = check as Record<string, Row | undefined>;
  if (!parts["outline"] && !parts["script"]) return null;
  return (
    <section className="card stack">
      <h2>{t("web.storycheck.title")}</h2>
      {parts["outline"] ? <CheckPart label={t("web.storycheck.outline")} part={parts["outline"]} /> : null}
      {parts["script"] ? <CheckPart label={t("web.storycheck.script")} part={parts["script"]} /> : null}
    </section>
  );
}

/** What the last produce stage made: counts, the assets that failed (with the reason) and the voices it gave out. */
export function ProduceResult({ summary }: { summary: Row | undefined }) {
  const t = useT();
  if (!summary || summary["made"] === undefined) return null;
  const failures = rows(summary["failures"]);
  const given = names(summary["voices_given"]);
  const missing = names(summary["voices_missing"]);
  return (
    <div className="stack small">
      <span>{t("web.produce.result", { made: num(summary["made"]), reused: num(summary["reused"]), failed: num(summary["failed"]) })}</span>
      {given ? <span className="muted">{t("web.produce.voices_given", { names: given })}</span> : null}
      {missing ? <span>{t("web.produce.voices_missing", { names: missing })}</span> : null}
      {failures.length > 0 ? (
        <>
          <span>{t("web.produce.failures")}</span>
          <ul className="plain">
            {failures.map((f, n) => (
              <li key={n}>
                <span className="chip">{t(`spec.subject.${String(f["subject"] ?? "")}`)}</span> <span className="muted">{String(f["id"] ?? "")}</span>{" "}
                {String(f["code"] ?? "")} {String(f["message"] ?? "")}
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </div>
  );
}
