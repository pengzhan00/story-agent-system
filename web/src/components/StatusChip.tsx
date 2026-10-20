import { useT } from "../i18n";

/** Status is always text *and* colour (never colour alone). */
export function StatusChip({ status, group = "workflow.status" }: { status: string; group?: string }) {
  const t = useT();
  return <span className={`chip chip-${status}`}>{t(`${group}.${status}`)}</span>;
}
