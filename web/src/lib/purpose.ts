/** What a prompt is for, as the AI buttons understand it, from a media kind (``image.generate`` -> ``image``). */
export function purposeOf(kind: string): string {
  if (kind === "audio.music") return "music";
  if (kind === "audio.tts" || kind === "audio.convert") return "voice";
  const head = kind.split(".")[0] ?? kind;
  return head === "text" ? "text" : head;
}
