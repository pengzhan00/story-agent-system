import type { Row } from "../api/types";

export interface StoryIndex {
  characters: Row[];
  locations: Row[];
  music: Row[];
  sfx: Row[];
  shots: Row[];
}

const byId = (rows: Row[], id: string) => rows.find((r) => String(r["id"]) === id);
const text = (v: unknown) => (v === null || v === undefined ? "" : String(v));

/** A human name for a spec subject: the character's name, "Act 1 · Scene 2 · Shot 3", the theme's title ... */
export function subjectLabel(subjectType: string, subjectId: string, story: StoryIndex, t: (k: string, p?: Record<string, string | number>) => string): string {
  const [head] = subjectType.split(".");
  if (head === "character") return text(byId(story.characters, subjectId)?.["name"]) || subjectId;
  if (subjectType === "scene.keyframe") return text(byId(story.locations, subjectId)?.["name"]) || subjectId;
  if (head === "music") return text(byId(story.music, subjectId)?.["name"]) || subjectId;
  if (head === "sfx") return text(byId(story.sfx, subjectId)?.["name"]) || subjectId;
  const shotLabel = (id: string) => {
    const s = byId(story.shots, id);
    return s ? t("web.creative.shot_label", { act: text(s["act_number"]), scene: text(s["scene_number"]), shot: text(s["shot_number"]) }) : id;
  };
  if (head === "shot") return shotLabel(subjectId);
  if (subjectType === "dialogue.line") {
    const [shot, idx] = subjectId.split("#");
    return `${shotLabel(shot ?? "")} · ${t("web.creative.line_n", { n: Number(idx ?? 0) + 1 })}`;
  }
  return subjectId;
}

/** What the free-text field means for this kind of subject. */
export function textFieldKey(subjectType: string): string {
  if (subjectType === "dialogue.line") return "web.creative.text_line";
  if (subjectType === "music.theme_song") return "web.creative.text_lyrics";
  if (subjectType === "character.voice") return "web.creative.text_sample";
  return "web.creative.text_generic";
}

export function fieldsOfKind(kind: string): { prompt: boolean; negative: boolean; seed: boolean } {
  const audio = kind.startsWith("audio.");
  return { prompt: true, negative: !audio, seed: true };
}
