import { usePipeline, usePipelineChoices, type PipelineRow } from "../api/pipeline";
import { useT } from "../i18n";
import { ErrorNote } from "./ErrorNote";

/** The models a project runs with: the plan recommends one complete set; single models can be changed (their companions
 *  follow), each row and the whole set go back to the recommendation in one click. */
export function PipelinePanel({ projectId }: { projectId: number }) {
  const t = useT();
  const view = usePipeline(projectId).data;
  const { set, reset } = usePipelineChoices(projectId);
  if (!view) return null;
  const picked = Object.fromEntries(view.rows.filter((r) => r.overridden).map((r) => [r.kind, r.chosen]));
  const choose = (row: PipelineRow, ref: string) => {
    const next = { ...picked };
    if (ref === row.default) delete next[row.kind];
    else next[row.kind] = ref;
    set.mutate(next);
  };
  const busy = set.isPending || reset.isPending;
  return (
    <section className="stack" aria-label={t("web.pipeline.title")}>
      <div className="row">
        <h3>{t("web.pipeline.title")}</h3>
        <span className="spacer" />
        <button disabled={busy || !view.overridden} onClick={() => reset.mutate()}>
          {t("web.pipeline.reset_all")}
        </button>
      </div>
      <p className="muted small">{t("web.pipeline.hint")}</p>
      <div className="table-wrap">
        <table>
          <tbody>
            {view.rows.map((row) => (
              <tr key={row.kind}>
                <td>{t(`media.kind.${row.kind}`)}</td>
                <td>
                  {row.chosen === "" ? (
                    <span className="muted">{t("web.pipeline.unused")}</span>
                  ) : (
                    <select value={row.chosen} disabled={busy} onChange={(e) => choose(row, e.target.value)} aria-label={t(`media.kind.${row.kind}`)}>
                      {row.candidates.map((c) => (
                        <option key={c.ref} value={c.ref}>
                          {`${c.ref}${c.ref === row.default ? ` · ${t("web.pipeline.recommended")}` : ""}${c.paired ? "" : ` · ${t("web.pipeline.not_paired")}`}${c.rating !== "general" ? ` · ${t("web.content.badge")}` : ""}`}
                        </option>
                      ))}
                    </select>
                  )}
                </td>
                <td>
                  {row.overridden ? <span className="chip chip-stale">{t("web.pipeline.changed")}</span> : null}
                  {row.auto ? <span className="chip">{t("web.pipeline.auto")}</span> : null}
                </td>
                <td>
                  {row.overridden ? (
                    <button className="link" disabled={busy} onClick={() => choose(row, row.default)}>
                      {t("web.pipeline.reset_one", { model: row.default })}
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {view.notes.map((n) => (
        <p key={`${n.code}-${n.kind}`} className="note small">
          {t(`web.pipeline.note.${n.code}`, { kind: t(`media.kind.${n.kind}`), model: n.model })}
        </p>
      ))}
      <ErrorNote error={set.error ?? reset.error} />
    </section>
  );
}
