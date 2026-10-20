import { clampPercent } from "../lib/format";

interface Props {
  /** 0-100; null = running but the item count is not known yet (indeterminate). */
  percent: number | null;
  label?: string;
  tone?: "running" | "completed" | "failed" | "pending" | "cancelled" | "stale" | "queued";
  size?: "sm" | "lg";
}

export function ProgressBar({ percent, label, tone = "running", size = "sm" }: Props) {
  const indeterminate = percent === null;
  const value = clampPercent(percent);
  return (
    <div
      className={`bar bar-${size} bar-${tone}${indeterminate ? " bar-indeterminate" : ""}`}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={indeterminate ? undefined : Math.round(value)}
      aria-label={label}
    >
      <div className="bar-fill" style={indeterminate ? undefined : { width: `${value}%` }} />
    </div>
  );
}
