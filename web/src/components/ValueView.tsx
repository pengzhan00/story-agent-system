import { useT } from "../i18n";
import { type Labeler, useLabel } from "../lib/labels";

function Value({ v, label, depth }: { v: unknown; label: Labeler; depth: number }) {
  const t = useT();
  if (v === null || v === undefined || v === "") return <span className="muted">{t("web.value.none")}</span>;
  if (typeof v === "boolean") return <span>{t(v ? "web.value.yes" : "web.value.no")}</span>;
  if (typeof v !== "object") return <span className="value-text">{String(v)}</span>;
  if (Array.isArray(v)) {
    if (v.every((i) => i === null || typeof i !== "object")) {
      return <span>{v.map((i) => String(i)).join(", ")}</span>;
    }
    return (
      <ol className="value-list">
        {v.map((item, i) => (
          <li key={i}>
            <Value v={item} label={label} depth={depth + 1} />
          </li>
        ))}
      </ol>
    );
  }
  const entries = Object.entries(v as Record<string, unknown>);
  if (entries.length === 0) return <span className="muted">{t("web.value.none")}</span>;
  return (
    <dl className={`value-dl depth-${Math.min(depth, 2)}`}>
      {entries.map(([k, val]) => (
        <div key={k} className="value-row">
          <dt>{label(k)}</dt>
          <dd>
            <Value v={val} label={label} depth={depth + 1} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** Read-only, human-readable rendering of structured data (no braces, no quotes): label -> value. */
export function ValueView({ value, prefix = "field" }: { value: unknown; prefix?: string }) {
  const label = useLabel(prefix);
  return <Value v={value} label={label} depth={0} />;
}
