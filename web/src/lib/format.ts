/** 80 -> "1m20s", 3700 -> "1h01m", null -> "-". */
export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "-";
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}m`;
}

export function clampPercent(p: number | null | undefined): number {
  if (p === null || p === undefined || Number.isNaN(p)) return 0;
  return Math.max(0, Math.min(100, p));
}
