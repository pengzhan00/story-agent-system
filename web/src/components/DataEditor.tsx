import { useState } from "react";
import { useProjectData, useSaveProjectData } from "../api/hooks";
import { useT } from "../i18n";
import { ErrorNote } from "./ErrorNote";
import { ValueEditor } from "./ValueEditor";

/** The plan or the style of a project (the series bible the stages read): shown as fields and editable. */
export function DataEditor({ id, name }: { id: number; name: "plan" | "style" }) {
  const t = useT();
  const data = useProjectData(id, name);
  const save = useSaveProjectData(id, name);
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null);
  const current = (data.data ?? {}) as Record<string, unknown>;
  const values = draft ?? current;
  const dirty = draft !== null && JSON.stringify(draft) !== JSON.stringify(current);
  return (
    <section className="card stack">
      <h2>{t(`web.process.${name}`)}</h2>
      <p className="muted small">{t(`web.process.${name}_hint`)}</p>
      <ErrorNote error={data.error ?? save.error} />
      {Object.keys(current).length === 0 && !data.isLoading ? <p className="empty">{t("web.process.not_yet")}</p> : null}
      {Object.keys(values).length > 0 ? <ValueEditor value={values} onChange={setDraft} /> : null}
      {Object.keys(values).length > 0 ? (
        <div className="row">
          <button className="primary" disabled={!dirty || save.isPending} onClick={() => save.mutate(values, { onSuccess: () => setDraft(null) })}>
            {t("web.save")}
          </button>
          <button disabled={!dirty} onClick={() => setDraft(null)}>
            {t("web.cancel")}
          </button>
        </div>
      ) : null}
    </section>
  );
}
