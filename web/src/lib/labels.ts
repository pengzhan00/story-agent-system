import { useT, type T } from "../i18n";

/** "camera_movement" -> "Camera movement": the fallback when a key has no translated label yet. */
export function humanize(key: string): string {
  const s = key.replace(/[_.\-]+/g, " ").trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : key;
}

export type Labeler = (key: string) => string;

export function labeler(t: T, prefix: string): Labeler {
  return (key) => {
    const full = `${prefix}.${key}`;
    const text = t(full);
    return text === full ? humanize(key) : text;
  };
}

/** Label of a data field: ``useLabel("field")("camera_movement")`` -> "镜头运动". */
export function useLabel(prefix: string): Labeler {
  return labeler(useT(), prefix);
}

type Plain = string | number | boolean | null | undefined | Plain[] | { [k: string]: Plain };

/** One readable line for any value, for places with little room (diffs, event lines). */
export function formatValue(v: unknown, t: T, label: Labeler): string {
  const x = v as Plain;
  if (x === null || x === undefined || x === "") return t("web.value.none");
  if (typeof x === "boolean") return t(x ? "web.value.yes" : "web.value.no");
  if (typeof x !== "object") return String(x);
  if (Array.isArray(x)) return x.map((i) => formatValue(i, t, label)).join(", ");
  return Object.entries(x)
    .map(([k, val]) => `${label(k)}: ${formatValue(val, t, label)}`)
    .join("; ");
}
