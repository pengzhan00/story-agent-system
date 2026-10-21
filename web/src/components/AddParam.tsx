import { useState } from "react";
import { useT } from "../i18n";
import { useLabel } from "../lib/labels";

/** A parameter that is not there yet: a name (suggestions from what the model understands) and a value. */
export function AddParam({
  onAdd,
  existing,
  suggestions = [],
}: {
  onAdd: (key: string, value: unknown) => void;
  existing: string[];
  suggestions?: string[];
}) {
  const t = useT();
  const label = useLabel("param");
  const [key, setKey] = useState("");
  const [val, setVal] = useState("");
  const name = key.trim();
  const ok = name !== "" && !existing.includes(name);
  const parse = (raw: string): unknown => {
    if (raw.trim() !== "" && Number.isFinite(Number(raw))) return Number(raw);
    if (raw === "true" || raw === "false") return raw === "true";
    return raw;
  };
  const free = suggestions.filter((s) => !existing.includes(s));
  return (
    <div className="row">
      <input
        list={free.length ? "param-suggestions" : undefined}
        value={key}
        onChange={(e) => setKey(e.target.value)}
        placeholder={t("web.creative.param_name")}
        aria-label={t("web.creative.param_name")}
      />
      {free.length ? (
        <datalist id="param-suggestions">
          {free.map((s) => (
            <option key={s} value={s}>
              {label(s)}
            </option>
          ))}
        </datalist>
      ) : null}
      <input value={val} onChange={(e) => setVal(e.target.value)} placeholder={t("web.creative.param_value")} aria-label={t("web.creative.param_value")} />
      <button
        type="button"
        disabled={!ok}
        onClick={() => {
          onAdd(name, parse(val));
          setKey("");
          setVal("");
        }}
      >
        {t("web.creative.add_param")}
      </button>
    </div>
  );
}
