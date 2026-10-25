import { useState } from "react";
import { useStartRun } from "../api/hooks";
import { useT } from "../i18n";
import { ErrorNote } from "./ErrorNote";

export interface ReviewIssueRow {
  severity: string;
  where: string;
  problem: string;
  fix?: string | null;
}

/** "Act 2 / Scene 3 / Shot 1" (or the same in any language) -> "2.3"; null when the place is not a scene. */
export function sceneOf(where: string): string | null {
  const m = /(\d+)\D+?(\d+)/.exec(where);
  return m ? `${m[1]}.${m[2]}` : null;
}

const worth = (i: ReviewIssueRow) => i.severity === "high" || i.severity === "medium";

/**
 * Rewrite the shots of a script with feedback: the review's issues (tick the ones to apply) and/or the editor's own
 * words. The outline stays; only the scenes the issues point at are written again (every scene when none is named).
 */
export function ScriptRevise({ id, episode, issues }: { id: number; episode: number; issues: ReviewIssueRow[] }) {
  const t = useT();
  const start = useStartRun(id);
  const [ticked, setTicked] = useState<Set<number>>(() => new Set(issues.flatMap((i, n) => (worth(i) ? [n] : []))));
  const [own, setOwn] = useState("");
  const chosen = issues.filter((_, n) => ticked.has(n));
  const scenes = [...new Set(chosen.map((i) => sceneOf(i.where)).filter((x): x is string => x !== null))];
  const general = chosen.some((i) => sceneOf(i.where) === null);
  const feedback = [...chosen.map((i) => `[${i.where}] ${i.problem}${i.fix ? ` -> ${i.fix}` : ""}`), own.trim()].filter(Boolean).join("\n");
  const toggle = (n: number, on: boolean) =>
    setTicked((p) => {
      const next = new Set(p);
      if (on) next.add(n);
      else next.delete(n);
      return next;
    });
  const go = () =>
    start.mutate({ stages: ["script"], params: { episode, feedback, ...(scenes.length > 0 && !general && own.trim() === "" ? { revise_scenes: scenes } : {}) } });
  return (
    <div className="stack">
      <strong>{t("web.revise.title")}</strong>
      <p className="muted small">{t("web.revise.hint")}</p>
      {issues.length > 0 ? (
        <ul className="plain stack small">
          {issues.map((i, n) => (
            <li key={`${i.where}:${i.problem}`} className="row">
              <input type="checkbox" aria-label={i.problem} checked={ticked.has(n)} onChange={(e) => toggle(n, e.target.checked)} />
              <span className="muted">{`[${i.severity}] ${i.where}`}</span>
              <span>{i.problem}</span>
            </li>
          ))}
        </ul>
      ) : null}
      <label className="field">
        <span>{t("web.revise.own")}</span>
        <textarea rows={3} value={own} onChange={(e) => setOwn(e.target.value)} />
      </label>
      <div className="row">
        <button type="button" className="primary" disabled={feedback === "" || start.isPending} onClick={go}>
          {t("web.revise.run")}
        </button>
        <span className="muted small">{scenes.length > 0 && !general && own.trim() === "" ? t("web.revise.scenes", { n: scenes.length }) : t("web.revise.all")}</span>
      </div>
      <ErrorNote error={start.error} />
    </div>
  );
}
