import type { Asset, WorkshopModule } from "../api/types";

/** What an asset is, in the words of the person using it (i18n: `library.cat.<id>`). */
export const CATEGORIES = [
  "story", "script", "image", "song", "accompaniment", "dialogue", "video", "lora", "transcript", "episode", "upload",
] as const;
export type Category = (typeof CATEGORIES)[number] | "other";

export const mediaOfAsset = (a: Pick<Asset, "kind">): string => {
  if (a.kind === "train.lora" || a.kind === "upload.model") return "lora";
  return a.kind.startsWith("upload.") ? a.kind.slice(7) : (a.kind.split(".")[0] ?? "");
};

export function categoryOf(a: Asset): Category {
  const k = a.kind;
  if (k === "text.story") return "story";
  if (k === "text.script") return "script";
  if (k === "text.asr") return "transcript";
  if (k.startsWith("image.")) return "image";
  if (k === "audio.music") return a.metadata?.["vocals"] === false ? "accompaniment" : "song";
  if (k === "audio.tts" || k === "audio.convert") return "dialogue";
  if (k.startsWith("video.")) return "video";
  if (k === "train.lora" || k === "upload.model") return "lora";
  if (k === "compose.assemble") return "episode";
  if (k.startsWith("upload.")) return "upload";
  return "other";
}

/** Where an asset can go next: every kind that takes a file of its media type. */
export function targetsFor(modules: WorkshopModule[], media: string) {
  const out: { module: string; kind: string; field: string }[] = [];
  for (const m of modules)
    for (const k of m.kinds) {
      const f = k.inputs.find((i) => (i.type === "media" || i.type === "medialist" || i.type === "shots") && i.media === media);
      if (f) out.push({ module: m.id, kind: k.kind, field: f.name });
    }
  return out;
}
