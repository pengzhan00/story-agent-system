import { usePlanScan } from "../api/hooks";
import type { Meta } from "../api/types";
import { useI18n } from "../i18n";

type Plan = Meta["pipelines"][number];

/** The text after a plan's name that says it cannot run now and what is missing ("" when it can, or is not scanned yet). */
export function usePlanStatus() {
  const { t } = useI18n();
  const scan = usePlanScan();
  const status = (name: string): string => {
    const found = scan.data?.plans.find((p) => p.name === name);
    if (!found || found.usable) return "";
    return t("web.plan.unavailable", { missing: found.missing.map((m) => `${m.name} (${m.label})`).join(", ") });
  };
  return { status, scanning: scan.isFetching, rescan: () => void scan.refetch() };
}

/** The `<option>` rows of a plan picker: usable plans first, the ones that cannot run marked with what is missing. */
export function PlanOptions({ plans, label }: { plans: Plan[]; label: (p: Plan) => string }) {
  const { status } = usePlanStatus();
  const ordered = [...plans].sort((a, b) => Number(status(a.name) !== "") - Number(status(b.name) !== ""));
  return (
    <>
      {ordered.map((p) => {
        const bad = status(p.name);
        return (
          <option key={p.name} value={p.name}>
            {bad ? `${label(p)} · ${bad}` : label(p)}
          </option>
        );
      })}
    </>
  );
}

/** Probe the machine again (after installing or starting a tool) so the list above follows without editing any plan. */
export function ScanPlansButton() {
  const { t } = useI18n();
  const { scanning, rescan } = usePlanStatus();
  return (
    <button type="button" className="chip chip-button" disabled={scanning} onClick={rescan}>
      {scanning ? t("web.plan.scanning") : t("web.plan.scan")}
    </button>
  );
}
