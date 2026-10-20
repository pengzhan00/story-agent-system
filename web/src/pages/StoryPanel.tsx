import { useState } from "react";
import { STORY_PARTS, useStory, type StoryPart } from "../api/hooks";
import type { Row } from "../api/types";
import { ErrorNote } from "../components/ErrorNote";
import { useT } from "../i18n";

/** The few fields worth showing per kind of row; the full editor arrives with R4b. */
const FIELDS: Record<StoryPart, string[]> = {
  characters: ["name", "role", "appearance", "voice_profile"],
  locations: ["name", "description", "lighting"],
  music: ["name", "kind", "mood", "description"],
  sfx: ["name", "category", "description"],
  shots: ["act_number", "scene_number", "shot_number", "location", "narration"],
  dialogue: ["character", "line", "emotion"],
  scripts: ["episode_no", "title", "synopsis"],
};

function cell(v: unknown): string {
  if (v === null || v === undefined) return "";
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

export function StoryPanel({ id }: { id: number }) {
  const t = useT();
  const [part, setPart] = useState<StoryPart>("characters");
  const rows = useStory(id, part);
  const fields = FIELDS[part];
  return (
    <div className="stack">
      <nav className="tabs" aria-label={t("web.story.parts")}>
        {STORY_PARTS.map((p) => (
          <button key={p} className={p === part ? "tab active" : "tab"} onClick={() => setPart(p)}>
            {t(`web.story.${p}`)}
          </button>
        ))}
      </nav>
      <ErrorNote error={rows.error} />
      {rows.data && rows.data.length === 0 ? <p className="empty">{t("web.story.empty")}</p> : null}
      {rows.data && rows.data.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {fields.map((f) => (
                  <th key={f}>{t(`web.field.${f}`)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.data.map((r: Row, i) => (
                <tr key={cell(r["id"]) || i}>
                  {fields.map((f) => (
                    <td key={f} className={f === "name" ? "strong" : ""}>
                      {cell(r[f])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
