import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api, post } from "../api/client";
import { useStartRun } from "../api/hooks";
import type { Revision, RevisionDecided, Revisions as RevisionList } from "../api/types";
import { useT } from "../i18n";
import { ErrorNote } from "./ErrorNote";

/** The lines of `text` with a mark on those the other side does not have: what a rewrite drops or adds. */
function Side({ text, other, mark }: { text: string; other: string; mark: "del" | "add" }) {
  const known = useMemo(() => new Set(other.split("\n").map((l) => l.trim())), [other]);
  return (
    <div className="revision-text">
      {text.split("\n").map((line, n) => (
        <div key={n} className={line.trim() && !known.has(line.trim()) ? mark : undefined}>
          {line || " "}
        </div>
      ))}
    </div>
  );
}

function Pair({ item }: { item: Revision }) {
  const t = useT();
  return (
    <div className="revision-pair diff">
      <div className="stack small">
        <span className="muted">{t("web.revisions.before")}</span>
        <Side text={item.before_text} other={item.after_text} mark="del" />
      </div>
      <div className="stack small">
        <span className="muted">{t("web.revisions.after")}</span>
        <Side text={item.after_text} other={item.before_text} mark="add" />
      </div>
    </div>
  );
}

function Head({ item }: { item: Revision }) {
  const t = useT();
  return (
    <>
      <strong>{t("web.revisions.scene", { scene: item.scene })}</strong>
      <span className="chip">{t(`web.revisions.source.${item.source}`)}</span>
    </>
  );
}

/**
 * Every rewrite of a written scene on one screen, each next to the shots it replaces: the ones that wait are ticked
 * and taken (or left) together; the ones a run to the film applied by itself can be taken back. Nothing reaches the
 * script before it was confirmed here, and after a change the stages built on the script are done again on request.
 */
export function Revisions({ id, episode, busy, refreshKey }: { id: number; episode: number; busy: boolean; refreshKey: string }) {
  const t = useT();
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["revisions", id, episode, refreshKey],
    queryFn: () => api<RevisionList>(`/api/projects/${id}/revisions?episode=${episode}`),
  });
  const start = useStartRun(id);
  const decide = useMutation({
    mutationFn: (body: { accept?: string[]; discard?: string[]; revert?: string[] }) =>
      post<RevisionDecided>(`/api/projects/${id}/revisions/decide`, { episode, ...body }),
    onSuccess: () => {
      for (const k of ["revisions", "workflow", "story"]) void qc.invalidateQueries({ queryKey: [k] });
    },
  });
  const items = useMemo(() => list.data?.items ?? [], [list.data]);
  const waiting = items.filter((i) => i.status === "pending");
  const applied = items.filter((i) => i.status === "applied");
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const ids = waiting.map((i) => i.id).join(",");
  useEffect(() => setTicked(new Set(ids ? ids.split(",") : [])), [ids]);       // a new proposal starts ticked
  const chosen = waiting.filter((i) => ticked.has(i.id)).map((i) => i.id);
  const toggle = (rid: string, on: boolean) =>
    setTicked((p) => {
      const next = new Set(p);
      if (on) next.add(rid);
      else next.delete(rid);
      return next;
    });
  const done = decide.data;
  const changed = done ? done.applied.length + done.reverted.length : 0;
  if (waiting.length === 0 && applied.length === 0 && !done) return null;
  return (
    <section className="card stack">
      <h2>{t("web.revisions.title")}</h2>
      <p className="muted small">{t("web.revisions.hint")}</p>
      {waiting.length > 0 ? (
        <>
          <strong>{t("web.revisions.waiting", { n: waiting.length })}</strong>
          <ul className="plain stack">
            {waiting.map((item) => (
              <li key={item.id} className="stack small">
                <label className="row">
                  <input type="checkbox" aria-label={t("web.revisions.scene", { scene: item.scene })} checked={ticked.has(item.id)} onChange={(e) => toggle(item.id, e.target.checked)} />
                  <Head item={item} />
                </label>
                {item.notes ? <p className="muted prewrap">{item.notes}</p> : null}
                <Pair item={item} />
              </li>
            ))}
          </ul>
          <div className="row">
            <button type="button" className="primary" disabled={busy || decide.isPending || chosen.length === 0} onClick={() => decide.mutate({ accept: chosen })}>
              {t("web.revisions.accept", { n: chosen.length })}
            </button>
            <button type="button" disabled={busy || decide.isPending || chosen.length === 0} onClick={() => decide.mutate({ discard: chosen })}>
              {t("web.revisions.discard", { n: chosen.length })}
            </button>
            {busy ? <span className="muted small">{t("web.revisions.busy")}</span> : null}
          </div>
        </>
      ) : null}
      {applied.length > 0 ? (
        <details>
          <summary>{t("web.revisions.applied", { n: applied.length })}</summary>
          <ul className="plain stack">
            {applied.map((item) => (
              <li key={item.id} className="stack small">
                <div className="row">
                  <Head item={item} />
                  <span className="spacer" />
                  <button type="button" className="link" disabled={busy || decide.isPending} onClick={() => decide.mutate({ revert: [item.id] })}>
                    {t("web.revisions.revert")}
                  </button>
                </div>
                {item.notes ? <p className="muted prewrap">{item.notes}</p> : null}
                <Pair item={item} />
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {done ? (
        <div className="stack small" role="status">
          <span>{t("web.revisions.done", { applied: done.applied.length, discarded: done.discarded.length, reverted: done.reverted.length })}</span>
          {done.conflicts.length > 0 ? <span className="bad">{t("web.revisions.conflicts", { scenes: done.conflicts.join(", ") })}</span> : null}
          {changed > 0 ? (
            <div className="row">
              <button type="button" className="primary" disabled={busy || start.isPending} onClick={() => start.mutate({ params: { episode } })}>
                {t("web.revisions.redo")}
              </button>
              <span className="muted small">{t("web.revisions.redo_hint")}</span>
            </div>
          ) : null}
        </div>
      ) : null}
      <ErrorNote error={list.error ?? decide.error ?? start.error} />
    </section>
  );
}
