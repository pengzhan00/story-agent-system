import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { patch } from "../api/client";
import { useProject, useStory } from "../api/hooks";
import type { Row } from "../api/types";
import { DataEditor } from "../components/DataEditor";
import { ErrorNote } from "../components/ErrorNote";
import { PromptAssist } from "../components/PromptAssist";
import { useT } from "../i18n";
import { RowEditor } from "./StoryPanel";

/** A text the author reads and rewrites: saved with one button, with the AI suggestion / translation beside it. */
function TextBox({ label, value, rows, onSave, pending, error, pid }: {
  label: string; value: string; rows: number; onSave: (v: string) => void; pending: boolean; error: unknown; pid: number;
}) {
  const t = useT();
  const [draft, setDraft] = useState<string | null>(null);
  const text = draft ?? value;
  return (
    <label className="field wide">
      <span>{label}</span>
      <textarea rows={rows} value={text} onChange={(e) => setDraft(e.target.value)} />
      <PromptAssist text={text} onApply={setDraft} purpose="text" projectId={pid} />
      <div className="row">
        <button type="button" className="primary" disabled={draft === null || draft === value || pending} onClick={() => onSave(text)}>
          {t("web.save")}
        </button>
        {draft !== null && draft !== value ? (
          <button type="button" onClick={() => setDraft(null)}>
            {t("web.cancel")}
          </button>
        ) : null}
      </div>
      <ErrorNote error={error} />
    </label>
  );
}

function Premise({ id }: { id: number }) {
  const t = useT();
  const project = useProject(id);
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: (description: string) => patch(`/api/projects/${id}`, { description }),
    onSuccess: () => qc.invalidateQueries(),
  });
  return (
    <section className="card stack">
      <h2>{t("web.bible.story")}</h2>
      <p className="muted small">{t("web.bible.story_hint")}</p>
      <TextBox label={t("web.bible.premise")} value={project.data?.description ?? ""} rows={6}
        onSave={(v) => save.mutate(v)} pending={save.isPending} error={save.error} pid={id} />
    </section>
  );
}

function SeasonRow({ pid, row }: { pid: number; row: Row }) {
  const t = useT();
  const qc = useQueryClient();
  const number = Number(row["number"]);
  const save = useMutation({
    mutationFn: (body: Row) => patch(`/api/projects/${pid}/seasons/${number}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["story", pid] }),
  });
  return (
    <div className="card stack">
      <strong>{t("web.bible.season", { n: number, title: String(row["title"] ?? "") })}</strong>
      <TextBox label={t("web.bible.season_summary")} value={String(row["summary"] ?? "")} rows={4}
        onSave={(summary) => save.mutate({ summary })} pending={save.isPending} error={save.error} pid={pid} />
    </div>
  );
}

function EpisodeRow({ pid, row }: { pid: number; row: Row }) {
  const t = useT();
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: (body: Row) => patch(`/api/projects/${pid}/episodes/${Number(row["id"])}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["story", pid] }),
  });
  return (
    <div className="card stack">
      <strong>{t("web.bible.episode", { n: Number(row["number"]), title: String(row["title"] ?? "") })}</strong>
      <TextBox label={t("web.bible.episode_summary")} value={String(row["summary"] ?? "")} rows={3}
        onSave={(summary) => save.mutate({ summary })} pending={save.isPending} error={save.error} pid={pid} />
    </div>
  );
}

function Outline({ id }: { id: number }) {
  const t = useT();
  const seasons = useStory(id, "seasons");
  const episodes = useStory(id, "episodes");
  return (
    <section className="card stack">
      <h2>{t("web.bible.outline")}</h2>
      <p className="muted small">{t("web.bible.outline_hint")}</p>
      <ErrorNote error={seasons.error ?? episodes.error} />
      {(seasons.data ?? []).map((s) => (
        <SeasonRow key={String(s["number"])} pid={id} row={s} />
      ))}
      {(episodes.data ?? []).map((e) => (
        <EpisodeRow key={String(e["id"])} pid={id} row={e} />
      ))}
      {seasons.data && episodes.data && seasons.data.length === 0 && episodes.data.length === 0 ? <p className="empty">{t("web.process.not_yet")}</p> : null}
    </section>
  );
}

interface Line {
  character?: string;
  line?: string;
  emotion?: string;
}

/** The script as it reads: scene by scene, the narration and what each person says; "edit" opens the shot's editor. */
function Script({ id }: { id: number }) {
  const t = useT();
  const episodes = useStory(id, "episodes");
  const shots = useStory(id, "shots");
  const [episode, setEpisode] = useState<number | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const current = episode ?? Number(episodes.data?.[0]?.["id"] ?? 0);
  const scenes = useMemo(() => {
    const groups = new Map<string, Row[]>();
    for (const s of (shots.data ?? []).filter((x) => Number(x["episode_id"]) === current)) {
      const key = `${s["act_number"] ?? 1}.${s["scene_number"] ?? 1}`;
      groups.set(key, [...(groups.get(key) ?? []), s]);
    }
    return [...groups.entries()];
  }, [shots.data, current]);
  return (
    <section className="card stack">
      <h2>{t("web.bible.script")}</h2>
      <p className="muted small">{t("web.bible.script_hint")}</p>
      <label className="field">
        <span>{t("web.bible.pick_episode")}</span>
        <select value={current} onChange={(e) => { setEpisode(Number(e.target.value)); setOpen(null); }}>
          {(episodes.data ?? []).map((e) => (
            <option key={String(e["id"])} value={Number(e["id"])}>
              {t("web.bible.episode", { n: Number(e["number"]), title: String(e["title"] ?? "") })}
            </option>
          ))}
        </select>
      </label>
      <ErrorNote error={shots.error} />
      {scenes.length === 0 ? <p className="empty">{t("web.bible.no_script")}</p> : null}
      {scenes.map(([key, list]) => (
        <div key={key} className="stack">
          <h3>{t("web.bible.scene", { scene: key, place: String(list[0]?.["location"] ?? "") })}</h3>
          {list.map((s) => {
            const sid = Number(s["id"]);
            const lines = (Array.isArray(s["dialogue"]) ? s["dialogue"] : []) as Line[];
            return (
              <div key={sid} className="stack">
                {String(s["narration"] ?? "") ? <p className="muted">{String(s["narration"])}</p> : null}
                {lines.map((l, i) => (
                  <p key={i}>
                    <strong>{l.character}</strong>
                    {l.emotion ? <span className="muted small">{` (${l.emotion})`}</span> : null}
                    {`: ${l.line ?? ""}`}
                  </p>
                ))}
                <div className="row small">
                  <button type="button" className="link" onClick={() => setOpen(open === sid ? null : sid)}>
                    {open === sid ? t("web.close") : t("web.edit")}
                  </button>
                </div>
                {open === sid ? <RowEditor pid={id} part="shots" row={s} onDone={() => setOpen(null)} /> : null}
              </div>
            );
          })}
        </div>
      ))}
    </section>
  );
}

/** Everything the story is made of, read and rewritten in one place: the premise, the bible (plan and style), the
 * outline per season and episode, and the script as it reads. Characters, places and music stay under "Story". */
export function BiblePanel({ id }: { id: number }) {
  const t = useT();
  return (
    <div className="stack">
      <p className="muted small">{t("web.bible.intro")}</p>
      <Premise id={id} />
      <section className="stack">
        <h2>{t("web.bible.bible")}</h2>
        <p className="muted small">{t("web.bible.bible_hint")}</p>
        <DataEditor id={id} name="plan" />
        <DataEditor id={id} name="style" />
      </section>
      <Outline id={id} />
      <Script id={id} />
    </div>
  );
}
