import { useT } from "../i18n";
import { type Labeler, useLabel } from "../lib/labels";

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);

/** An empty value of the same shape, for "add item". */
function blank(v: unknown): unknown {
  if (typeof v === "string") return "";
  if (typeof v === "number") return 0;
  if (typeof v === "boolean") return false;
  if (Array.isArray(v)) return [];
  if (isObj(v)) return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, blank(x)]));
  return "";
}

interface Props {
  value: unknown;
  onChange: (next: unknown) => void;
  name: string;
  label: Labeler;
}

function Field({ value, onChange, name, label }: Props) {
  const t = useT();
  const title = label(name);
  if (typeof value === "boolean") {
    return (
      <label className="field check">
        <input type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} />
        <span>{title}</span>
      </label>
    );
  }
  if (typeof value === "number") {
    return (
      <label className="field">
        <span>{title}</span>
        <input
          type="number"
          step="any"
          value={Number.isFinite(value) ? value : ""}
          onChange={(e) => onChange(e.target.value === "" ? 0 : Number(e.target.value))}
        />
      </label>
    );
  }
  if (Array.isArray(value)) {
    if (value.every((i) => typeof i === "string" || typeof i === "number")) {
      const numeric = value.length > 0 && value.every((i) => typeof i === "number");
      return (
        <label className="field wide">
          <span>
            {title} <span className="muted small">{t("web.value.list_hint")}</span>
          </span>
          <textarea
            rows={Math.min(Math.max(value.length + 1, 2), 10)}
            value={value.join("\n")}
            onChange={(e) => {
              const lines = e.target.value.split("\n").filter((l) => l.trim() !== "");
              onChange(numeric ? lines.map(Number).filter((n) => Number.isFinite(n)) : lines);
            }}
          />
        </label>
      );
    }
    const sample = value[0];
    return (
      <fieldset className="vgroup wide">
        <legend>{title}</legend>
        {value.map((item, i) => (
          <div key={i} className="vitem">
            <div className="row">
              <strong className="small grow">{t("web.value.item", { n: i + 1 })}</strong>
              <button type="button" className="link" onClick={() => onChange(value.filter((_, j) => j !== i))}>
                {t("web.value.remove")}
              </button>
            </div>
            <Field
              value={item}
              name={name}
              label={label}
              onChange={(n) => onChange(value.map((old, j) => (j === i ? n : old)))}
            />
          </div>
        ))}
        {sample !== undefined ? (
          <button type="button" onClick={() => onChange([...value, blank(sample)])}>
            {t("web.value.add")}
          </button>
        ) : null}
      </fieldset>
    );
  }
  if (isObj(value)) {
    return (
      <fieldset className="vgroup wide">
        {name ? <legend>{title}</legend> : null}
        <div className="form-grid">
          {Object.entries(value).map(([k, v]) => (
            <Field key={k} name={k} value={v} label={label} onChange={(n) => onChange({ ...value, [k]: n })} />
          ))}
        </div>
      </fieldset>
    );
  }
  const text = value === null || value === undefined ? "" : String(value);
  const long = text.length > 70 || text.includes("\n");
  return (
    <label className={`field${long ? " wide" : ""}`}>
      <span>{title}</span>
      {long ? (
        <textarea rows={Math.min(Math.max(text.split("\n").length, 3), 12)} value={text} onChange={(e) => onChange(e.target.value)} />
      ) : (
        <input value={text} onChange={(e) => onChange(e.target.value)} />
      )}
    </label>
  );
}

/** Edit structured data as labelled fields (text, numbers, switches, lists, nested groups): never as JSON. */
export function ValueEditor({ value, onChange, prefix = "field" }: { value: Obj; onChange: (next: Obj) => void; prefix?: string }) {
  const label = useLabel(prefix);
  return <Field value={value} name="" label={label} onChange={(n) => onChange(n as Obj)} />;
}
