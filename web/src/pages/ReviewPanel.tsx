import { PromptAssist } from "../components/PromptAssist";
import { purposeOf } from "../lib/purpose";
import { useState, type FormEvent } from "react";
import { useMe } from "../api/auth";
import { useNotes, useReviewActions, useCompare, useReviewBoard, type Note, type ReviewShot } from "../api/review";
import { useSpecs } from "../api/specs";
import type { Asset, Spec } from "../api/types";
import { AssetPreview } from "../components/Candidates";
import { ErrorNote } from "../components/ErrorNote";
import { useT } from "../i18n";
import { labeler } from "../lib/labels";

const TIERS = ["draft", "preview", "final"] as const;

function shotLabel(s: ReviewShot, t: (k: string) => string): string {
  return `${t("web.review.episode_short")}${s.episode} · ${t("web.review.act_short")}${s.act}.${s.scene}.${s.shot}${s.location ? ` · ${s.location}` : ""}`;
}

function NoteCompare({ pid, note, actions }: { pid: number; note: Note; actions: ReturnType<typeof useReviewActions> }) {
  const t = useT();
  const cmp = useCompare(pid, note.id, true);
  if (!cmp.data) return null;
  const running = cmp.data.jobs.filter((j) => j.status === "queued" || j.status === "running");
  return (
    <div className="compare">
      <div>
        <h4>{t("web.review.before")}</h4>
        {cmp.data.before ? <AssetPreview asset={cmp.data.before as Asset} /> : <p className="muted small">{t("web.review.no_before")}</p>}
      </div>
      <div className="stack">
        <h4>{t("web.review.after")}</h4>
        {running.map((j) => (
          <p key={j.id} className="muted small">
            {t("web.review.making")} {j.status === "running" ? `${Math.round(j.progress * 100)}%` : t("web.review.waiting")}
          </p>
        ))}
        {cmp.data.after.map((a) => (
          <div key={a.id} className="stack">
            <AssetPreview asset={a as Asset} />
            <button disabled={actions.adopt.isPending || a.is_current} onClick={() => actions.adopt.mutate(a.id)}>
              {t(a.is_current ? "web.review.in_use" : "web.review.adopt")}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function RedoForm({ note, spec, actions, onDone }: { note: Note; spec: Spec | undefined; actions: ReturnType<typeof useReviewActions>; onDone: () => void }) {
  const t = useT();
  const [prompt, setPrompt] = useState(spec?.prompt ?? "");
  const [count, setCount] = useState(2);
  const [tier, setTier] = useState<(typeof TIERS)[number]>("draft");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const changed = spec && prompt.trim() !== spec.prompt.trim() ? { prompt } : {};
    actions.redo.mutate({ id: note.id, ...changed, n_candidates: count, tier }, { onSuccess: onDone });
  };
  return (
    <form className="stack" onSubmit={submit}>
      <label className="field">
        <span>{t("web.review.redo_prompt")}</span>
        <textarea rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
        <PromptAssist text={prompt} onApply={setPrompt} purpose={purposeOf(spec?.kind ?? "image.generate")} projectId={spec?.project_id} />
      </label>
      <div className="row">
        <label className="field">
          <span>{t("web.review.candidates")}</span>
          <select value={count} onChange={(e) => setCount(Number(e.target.value))}>
            {[1, 2, 3, 4].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>{t("web.review.tier")}</span>
          <select value={tier} onChange={(e) => setTier(e.target.value as (typeof TIERS)[number])}>
            {TIERS.map((x) => (
              <option key={x} value={x}>
                {t(`media.tier.${x}`)}
              </option>
            ))}
          </select>
        </label>
        <button className="primary" type="submit" disabled={actions.redo.isPending}>
          {t("web.review.redo")}
        </button>
      </div>
      <ErrorNote error={actions.redo.error} />
    </form>
  );
}

function NoteCard({ pid, note, spec, actions, canWrite }: { pid: number; note: Note; spec: Spec | undefined; actions: ReturnType<typeof useReviewActions>; canWrite: boolean }) {
  const t = useT();
  const [redoing, setRedoing] = useState(false);
  const chip = note.status === "resolved" ? "completed" : note.status === "dismissed" ? "stale" : note.status === "redoing" ? "running" : "queued";
  return (
    <div className="card stack">
      <div className="row">
        <span className={`chip chip-${chip}`}>{t(`web.review.status.${note.status}`)}</span>
        <span className="small muted">
          {note.author} · {note.created_at}
        </span>
        <span className="spacer" />
        <span className="small muted">{spec ? `${spec.subject_type}` : note.subject_type}</span>
      </div>
      <p>{note.body}</p>
      {note.resolution ? <p className="small muted">{note.resolution.startsWith("adopted:") ? t("web.review.resolved_by_adopt") : note.resolution}</p> : null}
      {canWrite ? (
        <div className="row-actions">
          {note.status === "open" || note.status === "redoing" ? (
            <button onClick={() => setRedoing((v) => !v)}>{t("web.review.redo")}</button>
          ) : null}
          {note.status === "open" || note.status === "redoing" ? (
            <>
              <button onClick={() => actions.status.mutate({ id: note.id, status: "resolved" })}>{t("web.review.resolve")}</button>
              <button onClick={() => actions.status.mutate({ id: note.id, status: "dismissed" })}>{t("web.review.dismiss")}</button>
            </>
          ) : (
            <button onClick={() => actions.status.mutate({ id: note.id, status: "open" })}>{t("web.review.reopen")}</button>
          )}
        </div>
      ) : null}
      {redoing ? <RedoForm note={note} spec={spec} actions={actions} onDone={() => setRedoing(false)} /> : null}
      {note.redo_job_ids.length > 0 ? <NoteCompare pid={pid} note={note} actions={actions} /> : null}
    </div>
  );
}

function ShotReview({ pid, shot }: { pid: number; shot: ReviewShot }) {
  const t = useT();
  const me = useMe();
  const canWrite = me.data?.can_write ?? true;
  const notes = useNotes(pid, shot.shot_id);
  const specs = useSpecs(pid);
  const actions = useReviewActions(pid);
  const [takeId, setTakeId] = useState("");
  const [body, setBody] = useState("");
  const kindLabel = labeler(t, "media.kind");
  const specOf = (id: string | null | undefined): Spec | undefined => specs.data?.find((s) => s.id === id);
  const take = shot.assets.find((a) => a.id === (takeId || shot.assets[0]?.id));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const spec = specOf(take?.spec_id);
    if (!take || !spec) return;
    actions.add.mutate(
      { subject_type: spec.subject_type, subject_id: spec.subject_id, slot: spec.slot, shot_id: shot.shot_id, asset_id: take.id, body },
      { onSuccess: () => setBody("") },
    );
  };
  return (
    <div className="stack">
      <h2>{shotLabel(shot, t)}</h2>
      <div className="takes">
        {shot.assets.length === 0 ? <p className="empty">{t("web.review.no_takes")}</p> : null}
        {shot.assets.map((a) => (
          <div key={a.id} className="stack">
            <span className="small muted">{kindLabel(a.kind)}</span>
            <AssetPreview asset={a as Asset} />
          </div>
        ))}
      </div>
      {canWrite && shot.assets.length > 0 ? (
        <form className="stack" onSubmit={submit}>
          <label className="field">
            <span>{t("web.review.about")}</span>
            <select value={take?.id ?? ""} onChange={(e) => setTakeId(e.target.value)}>
              {shot.assets.map((a) => (
                <option key={a.id} value={a.id}>
                  {kindLabel(a.kind)}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>{t("web.review.note")}</span>
            <textarea rows={3} value={body} onChange={(e) => setBody(e.target.value)} placeholder={t("web.review.note_hint")} />
          </label>
          <ErrorNote error={actions.add.error} />
          <button className="primary" type="submit" disabled={actions.add.isPending || !body.trim()}>
            {t("web.review.add_note")}
          </button>
        </form>
      ) : null}
      <ErrorNote error={notes.error ?? actions.status.error ?? actions.adopt.error} />
      {notes.data?.length === 0 ? <p className="empty">{t("web.review.no_notes")}</p> : null}
      {notes.data?.map((n) => <NoteCard key={n.id} pid={pid} note={n} spec={specOf(specs.data?.find((s) => s.subject_type === n.subject_type && s.subject_id === n.subject_id && s.slot === n.slot)?.id)} actions={actions} canWrite={canWrite} />)}
    </div>
  );
}

/** Review: look at every shot's takes, leave notes, redo from a note, compare before and after, adopt the better take. */
export function ReviewPanel({ id }: { id: number }) {
  const t = useT();
  const board = useReviewBoard(id);
  const [shotId, setShotId] = useState<number | null>(null);
  const shots = board.data ?? [];
  const shot = shots.find((s) => s.shot_id === shotId) ?? shots[0];
  return (
    <div className="review-layout">
      <aside className="stack">
        <ErrorNote error={board.error} />
        {board.isLoading ? <p className="muted">{t("web.loading")}</p> : null}
        {shots.length === 0 && !board.isLoading ? <p className="empty">{t("web.review.no_shots")}</p> : null}
        <ul className="plain-list">
          {shots.map((s) => (
            <li key={s.shot_id}>
              <button className={s.shot_id === shot?.shot_id ? "list-item active" : "list-item"} onClick={() => setShotId(s.shot_id)}>
                <span>{shotLabel(s, t)}</span>
                {s.open_notes > 0 ? <span className="chip chip-failed">{s.open_notes}</span> : null}
                {s.redoing_notes > 0 ? <span className="chip chip-running">{s.redoing_notes}</span> : null}
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <section>{shot ? <ShotReview pid={id} shot={shot} /> : null}</section>
    </div>
  );
}
