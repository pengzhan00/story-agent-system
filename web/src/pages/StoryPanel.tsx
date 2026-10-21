import { useMemo, useState } from "react";
import { STORY_PARTS, useStory, useStoryEdit, type StoryPart } from "../api/hooks";
import type { Row } from "../api/types";
import { ErrorNote } from "../components/ErrorNote";
import { ScriptOutline } from "../components/ScriptOutline";
import { VoiceUpload } from "../components/VoiceUpload";
import { useT } from "../i18n";

type FieldType = "text" | "area" | "number" | "select";
interface Field {
  key: string;
  type: FieldType;
  rows?: number;
  options?: string[];
}

/** Columns shown in the table and fields offered in the editor, per kind of row. */
const COLUMNS: Record<StoryPart, string[]> = {
  characters: ["name", "role", "appearance", "voice_profile"],
  locations: ["name", "description", "lighting"],
  music: ["name", "kind", "mood", "description"],
  sfx: ["name", "category", "description"],
  shots: ["act_number", "scene_number", "shot_number", "location", "narration", "treatment"],
  dialogue: ["character", "line", "emotion"],
  scripts: ["episode_no", "title", "synopsis"],
};
const EDITABLE: Partial<Record<StoryPart, Field[]>> = {
  characters: [
    { key: "name", type: "text" }, { key: "role", type: "text" }, { key: "age", type: "text" }, { key: "gender", type: "text" },
    { key: "appearance", type: "area", rows: 3 }, { key: "personality", type: "area" }, { key: "background", type: "area" },
    { key: "voice_profile", type: "area", rows: 2 }, { key: "prompt_template", type: "area" },
    { key: "voice_ref_audio", type: "text" }, { key: "voice_ref_text", type: "text" },
  ],
  locations: [
    { key: "name", type: "text" }, { key: "description", type: "area", rows: 3 }, { key: "lighting", type: "text" },
    { key: "color_palette", type: "text" }, { key: "atmosphere", type: "text" }, { key: "prompt_template", type: "area" },
  ],
  music: [
    { key: "name", type: "text" }, { key: "mood", type: "text" }, { key: "tempo", type: "text" }, { key: "instruments", type: "text" },
    { key: "description", type: "area" }, { key: "gen_prompt", type: "area" }, { key: "lyrics", type: "area", rows: 8 },
    { key: "duration_sec", type: "number" },
  ],
  sfx: [],
  shots: [
    { key: "location", type: "text" }, { key: "narration", type: "area", rows: 2 }, { key: "mood", type: "text" },
    { key: "time_of_day", type: "text" }, { key: "weather", type: "text" }, { key: "camera_notes", type: "area", rows: 2 },
    { key: "treatment", type: "select", options: ["auto", "video", "still"] },
  ],
};

function cell(v: unknown): string {
  if (v === null || v === undefined) return "";
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

export function StoryPanel({ id }: { id: number }) {
  const t = useT();
  const [part, setPart] = useState<StoryPart>("characters");
  const rows = useStory(id, part);
  const [editing, setEditing] = useState<number | null>(null);
  const columns = COLUMNS[part];
  const editable = (EDITABLE[part] ?? []).length > 0 || part === "dialogue" || part === "scripts";
  return (
    <div className="stack">
      <nav className="tabs" aria-label={t("web.story.parts")}>
        {STORY_PARTS.map((p) => (
          <button
            key={p}
            className={p === part ? "tab active" : "tab"}
            onClick={() => {
              setPart(p);
              setEditing(null);
            }}
          >
            {t(`web.story.${p}`)}
          </button>
        ))}
      </nav>
      <p className="muted small">{t("web.story.edit_hint")}</p>
      <ErrorNote error={rows.error} />
      {rows.data && rows.data.length === 0 ? <p className="empty">{t("web.story.empty")}</p> : null}
      {rows.data && rows.data.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {columns.map((f) => (
                  <th key={f}>{t(`web.field.${f}`)}</th>
                ))}
                {editable ? <th /> : null}
              </tr>
            </thead>
            <tbody>
              {rows.data.map((r: Row, i) => {
                const rid = Number(r["id"]);
                const key = part === "dialogue" ? cell(r["id"]) : cell(r["id"]) || String(i);
                const open = editing === (part === "dialogue" ? i : rid);
                return (
                  <RowView
                    key={key}
                    pid={id}
                    part={part}
                    row={r}
                    columns={columns}
                    editable={editable}
                    open={open}
                    onToggle={() => setEditing(open ? null : part === "dialogue" ? i : rid)}
                  />
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

function RowView(props: { pid: number; part: StoryPart; row: Row; columns: string[]; editable: boolean; open: boolean; onToggle: () => void }) {
  const t = useT();
  const { part, row, columns, editable, open, onToggle, pid } = props;
  const locked = part === "shots" && row["locked"] === true;
  return (
    <>
      <tr>
        {columns.map((f) => (
          <td key={f} className={f === "name" ? "strong" : ""}>
            {cell(row[f])}
          </td>
        ))}
        {editable ? (
          <td className="actions">
            {locked ? <span className="small muted">{t("web.creative.lock_mark")} </span> : null}
            <button className="link" onClick={onToggle}>
              {open ? t("web.close") : t("web.edit")}
            </button>
          </td>
        ) : null}
      </tr>
      {open ? (
        <tr className="edit-row">
          <td colSpan={columns.length + 1}>
            {part === "dialogue" ? (
              <DialogueEditor pid={pid} row={row} onDone={onToggle} />
            ) : part === "scripts" ? (
              <ScriptOutline pid={pid} row={row} />
            ) : (
              <RowEditor pid={pid} part={part} row={row} onDone={onToggle} />
            )}
          </td>
        </tr>
      ) : null}
    </>
  );
}

function RowEditor({ pid, part, row, onDone }: { pid: number; part: StoryPart; row: Row; onDone: () => void }) {
  const t = useT();
  const fields = EDITABLE[part] ?? [];
  const edit = useStoryEdit(pid, part);
  const [draft, setDraft] = useState<Record<string, string>>(() => Object.fromEntries(fields.map((f) => [f.key, cell(row[f.key])])));
  const changes = useMemo(() => {
    const out: Row = {};
    for (const f of fields) {
      const now = draft[f.key] ?? "";
      if (now === cell(row[f.key])) continue;
      out[f.key] = f.type === "number" ? (now.trim() === "" ? null : Number(now)) : now;
    }
    return out;
  }, [draft, fields, row]);
  const dirty = Object.keys(changes).length > 0;
  const locked = part === "shots" && row["locked"] === true;
  return (
    <div className="stack">
      {part === "shots" ? <ShotDialogue pid={pid} row={row} disabled={locked} /> : null}
      {part === "characters" ? <VoiceUpload pid={pid} row={row} /> : null}
      <div className="form-grid">
        {fields.map((f) => (
          <label key={f.key} className={f.type === "area" ? "field wide" : "field"}>
            <span>{t(`web.field.${f.key}`)}</span>
            {f.type === "select" ? (
              <select value={draft[f.key] ?? ""} disabled={locked} onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })}>
                {(f.options ?? []).map((o) => (
                  <option key={o} value={o}>
                    {t(`web.${f.key}.${o}`)}
                  </option>
                ))}
              </select>
            ) : f.type === "area" ? (
              <textarea rows={f.rows ?? 2} value={draft[f.key] ?? ""} disabled={locked} onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })} />
            ) : (
              <input value={draft[f.key] ?? ""} disabled={locked} inputMode={f.type === "number" ? "decimal" : undefined} onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })} />
            )}
          </label>
        ))}
      </div>
      <div className="row">
        <button className="primary" disabled={!dirty || edit.update.isPending} onClick={() => edit.update.mutate({ itemId: Number(row["id"]), body: changes }, { onSuccess: onDone })}>
          {t("web.save")}
        </button>
        <button onClick={onDone}>{t("web.cancel")}</button>
        <span className="spacer" />
        {part === "shots" ? (
          <button className="link" disabled={edit.lock.isPending} onClick={() => edit.lock.mutate({ itemId: Number(row["id"]), locked: !locked })}>
            {locked ? t("web.creative.unlock") : t("web.creative.lock")}
          </button>
        ) : null}
        {part === "characters" || part === "locations" || part === "music" ? (
          <button
            className="link bad"
            disabled={edit.remove.isPending}
            onClick={() => {
              if (window.confirm(t("web.story.confirm_delete"))) edit.remove.mutate(Number(row["id"]), { onSuccess: onDone });
            }}
          >
            {t("web.delete")}
          </button>
        ) : null}
      </div>
      <p className="muted small">{t("web.story.then_specs")}</p>
      <ErrorNote error={edit.update.error ?? edit.lock.error ?? edit.remove.error} />
    </div>
  );
}

interface Line {
  character: string;
  line: string;
  emotion: string;
}

function ShotDialogue({ pid, row, disabled }: { pid: number; row: Row; disabled: boolean }) {
  const t = useT();
  const edit = useStoryEdit(pid, "shots");
  const characters = useStory(pid, "characters");
  const initial = (Array.isArray(row["dialogue"]) ? row["dialogue"] : []) as Line[];
  const [lines, setLines] = useState<Line[]>(initial.map((l) => ({ character: l.character ?? "", line: l.line ?? "", emotion: l.emotion ?? "" })));
  const dirty = JSON.stringify(lines) !== JSON.stringify(initial.map((l) => ({ character: l.character ?? "", line: l.line ?? "", emotion: l.emotion ?? "" })));
  const set = (i: number, patch: Partial<Line>) => setLines(lines.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  return (
    <fieldset className="stack" disabled={disabled}>
      <strong className="small">{t("web.story.dialogue_of_shot")}</strong>
      {lines.map((l, i) => (
        <div key={i} className="row line-row">
          <select value={l.character} onChange={(e) => set(i, { character: e.target.value })} aria-label={t("web.field.character")}>
            {!(characters.data ?? []).some((c) => c["name"] === l.character) ? <option value={l.character}>{l.character}</option> : null}
            {(characters.data ?? []).map((c) => (
              <option key={String(c["id"])} value={String(c["name"])}>
                {String(c["name"])}
              </option>
            ))}
          </select>
          <input className="grow" value={l.line} onChange={(e) => set(i, { line: e.target.value })} aria-label={t("web.field.line")} />
          <input value={l.emotion} onChange={(e) => set(i, { emotion: e.target.value })} aria-label={t("web.field.emotion")} placeholder={t("web.field.emotion")} />
          <button className="link" onClick={() => setLines(lines.filter((_, j) => j !== i))}>
            {t("web.delete")}
          </button>
        </div>
      ))}
      <div className="row">
        <button className="link" onClick={() => setLines([...lines, { character: (characters.data ?? [])[0]?.["name"] as string ?? "", line: "", emotion: "" }])}>
          {t("web.story.add_line")}
        </button>
        <span className="spacer" />
        <button disabled={!dirty || edit.update.isPending} onClick={() => edit.update.mutate({ itemId: Number(row["id"]), body: { dialogue: lines } })}>
          {t("web.story.save_dialogue")}
        </button>
      </div>
      <ErrorNote error={edit.update.error} />
    </fieldset>
  );
}

function DialogueEditor({ pid, row, onDone }: { pid: number; row: Row; onDone: () => void }) {
  const t = useT();
  const characters = useStory(pid, "characters");
  const edit = useStoryEdit(pid, "shots");
  const shot = Number(row["shot_id"]);
  const idx = Number(row["index"]);
  const [who, setWho] = useState(cell(row["character"]));
  const [line, setLine] = useState(cell(row["line"]));
  const [emotion, setEmotion] = useState(cell(row["emotion"]));
  const unknown = row["character_id"] === null || row["character_id"] === undefined;
  const shots = useStory(pid, "shots");
  const source = (shots.data ?? []).find((s) => Number(s["id"]) === shot);
  const save = () => {
    const dialogue = ((source?.["dialogue"] ?? []) as Line[]).map((l, i) => (i === idx ? { ...l, character: who, line, emotion } : l));
    edit.update.mutate({ itemId: shot, body: { dialogue } }, { onSuccess: onDone });
  };
  return (
    <div className="stack">
      {unknown ? <p className="hint">{t("web.story.unknown_speaker", { name: cell(row["character"]) })}</p> : null}
      <div className="row line-row">
        <select value={who} onChange={(e) => setWho(e.target.value)} aria-label={t("web.field.character")}>
          {!(characters.data ?? []).some((c) => c["name"] === who) ? <option value={who}>{who}</option> : null}
          {(characters.data ?? []).map((c) => (
            <option key={String(c["id"])} value={String(c["name"])}>
              {String(c["name"])}
            </option>
          ))}
        </select>
        <input className="grow" value={line} onChange={(e) => setLine(e.target.value)} aria-label={t("web.field.line")} />
        <input value={emotion} onChange={(e) => setEmotion(e.target.value)} aria-label={t("web.field.emotion")} />
      </div>
      <div className="row">
        <button className="primary" disabled={edit.update.isPending || !source} onClick={save}>
          {t("web.save")}
        </button>
        <button onClick={onDone}>{t("web.cancel")}</button>
      </div>
      <ErrorNote error={edit.update.error} />
    </div>
  );
}
