import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { patch } from "../api/client";
import type { Row } from "../api/types";
import { useT } from "../i18n";
import { ErrorNote } from "./ErrorNote";

const FIELDS = ["location", "time_of_day", "weather", "mood", "beat", "narration"] as const;
type SceneRow = Record<string, unknown> & { number: number; shots?: unknown[]; characters?: string[] };
type ActRow = { number: number; title?: string; summary?: string; scenes?: SceneRow[] };

function Scene({ pid, episode, act, scene }: { pid: number; episode: number; act: number; scene: SceneRow }) {
  const t = useT();
  const qc = useQueryClient();
  const [d, setD] = useState<Record<string, string>>(() => Object.fromEntries(FIELDS.map((f) => [f, String(scene[f] ?? "")])));
  const dirty = FIELDS.some((f) => d[f] !== String(scene[f] ?? ""));
  const save = useMutation({
    mutationFn: () => patch<Row>(`/api/projects/${pid}/scripts/${episode}/acts/${act}/scenes/${scene.number}`, d),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["story", pid, "scripts"] }),
  });
  return (
    <div className="card stack">
      <div className="row">
        <strong>{t("web.outline.scene", { act, scene: scene.number })}</strong>
        <span className="muted small">{t("web.outline.shots", { n: scene.shots?.length ?? 0 })}</span>
        {scene.characters?.length ? <span className="muted small">{scene.characters.join(" · ")}</span> : null}
      </div>
      <div className="form-grid">
        {FIELDS.map((f) => (
          <label key={f} className={f === "beat" || f === "narration" ? "field wide" : "field"}>
            <span>{t(`web.field.${f}`)}</span>
            {f === "beat" || f === "narration" ? (
              <textarea rows={2} value={d[f] ?? ""} onChange={(e) => setD({ ...d, [f]: e.target.value })} />
            ) : (
              <input value={d[f] ?? ""} onChange={(e) => setD({ ...d, [f]: e.target.value })} />
            )}
          </label>
        ))}
      </div>
      <div className="row">
        <button className="primary" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
          {t("web.save")}
        </button>
      </div>
      <ErrorNote error={save.error} />
    </div>
  );
}

/** The script as written: every act and scene with its beat, place and mood. Editable; shots are edited under "Shots". */
export function ScriptOutline({ pid, row }: { pid: number; row: Row }) {
  const t = useT();
  const acts = (Array.isArray(row["acts"]) ? row["acts"] : []) as ActRow[];
  const episode = Number(row["episode_no"] ?? 1);
  return (
    <div className="stack">
      <p className="muted small">{t("web.outline.hint")}</p>
      {String(row["synopsis"] ?? "") ? <p>{String(row["synopsis"])}</p> : null}
      {acts.map((a) => (
        <section key={a.number} className="stack">
          <h3>{t("web.outline.act", { n: a.number, title: a.title ?? "" })}</h3>
          {a.summary ? <p className="muted small">{a.summary}</p> : null}
          {(a.scenes ?? []).map((sc) => (
            <Scene key={`${a.number}-${sc.number}`} pid={pid} episode={episode} act={a.number} scene={sc} />
          ))}
        </section>
      ))}
    </div>
  );
}
