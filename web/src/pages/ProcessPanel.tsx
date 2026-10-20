import { Fragment, useMemo, useState } from "react";
import { useAiCall, useAiCalls, useProjectData, useSaveProjectData } from "../api/hooks";
import { ErrorNote } from "../components/ErrorNote";
import { useT } from "../i18n";
import { duration } from "../lib/format";

function Transcript({ id }: { id: number }) {
  const t = useT();
  const call = useAiCall(id);
  if (call.isLoading) return <p className="muted">{t("web.loading")}</p>;
  if (!call.data) return <ErrorNote error={call.error} />;
  const c = call.data;
  return (
    <div className="stack">
      {([["system", c.system], ["prompt", c.prompt], ["response", c.response]] as const).map(([k, text]) => (
        <div key={k} className="stack">
          <div className="row">
            <strong className="small">{t(`web.process.${k}`)}</strong>
            <span className="spacer" />
            <button className="link" onClick={() => void navigator.clipboard?.writeText(text)}>
              {t("web.copy")}
            </button>
          </div>
          <pre className="transcript">{text}</pre>
        </div>
      ))}
    </div>
  );
}

function AiCalls({ id }: { id: number }) {
  const t = useT();
  const [label, setLabel] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const all = useAiCalls(id);
  const calls = useAiCalls(id, label);
  const labels = useMemo(() => [...new Set((all.data ?? []).map((c) => c.label).filter(Boolean))].sort(), [all.data]);
  return (
    <section className="card stack">
      <div className="row">
        <h2 className="grow">{t("web.process.calls")}</h2>
        <select value={label} onChange={(e) => setLabel(e.target.value)} aria-label={t("web.process.filter")}>
          <option value="">{t("web.process.all_agents")}</option>
          {labels.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
      </div>
      <p className="muted small">{t("web.process.calls_hint")}</p>
      <ErrorNote error={calls.error} />
      {calls.data && calls.data.length === 0 ? <p className="empty">{t("web.process.no_calls")}</p> : null}
      {calls.data && calls.data.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("web.process.when")}</th>
                <th>{t("web.process.agent")}</th>
                <th>{t("web.process.model")}</th>
                <th>{t("web.process.tokens")}</th>
                <th>{t("web.process.took")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {calls.data.map((c) => (
                <Fragment key={c.id}>
                  <tr>
                    <td className="small muted">{c.created_at.replace("T", " ").slice(0, 19)}</td>
                    <td className="strong mono">{c.label || c.agent}</td>
                    <td className="small">{c.model}</td>
                    <td className="small">{`${c.tokens_in} → ${c.tokens_out}`}</td>
                    <td className="small">{duration(c.duration_ms / 1000)}</td>
                    <td className="actions">
                      <button className="link" onClick={() => setOpen(open === c.id ? null : c.id)}>
                        {open === c.id ? t("web.close") : t("web.process.read")}
                      </button>
                    </td>
                  </tr>
                  {open === c.id ? (
                    <tr className="edit-row">
                      <td colSpan={6}>
                        <Transcript id={c.id} />
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

/** Every key of the plan/style as its own field; texts edit as text, lists and objects as JSON. */
function DataEditor({ id, name }: { id: number; name: "plan" | "style" }) {
  const t = useT();
  const data = useProjectData(id, name);
  const save = useSaveProjectData(id, name);
  const [draft, setDraft] = useState<Record<string, string> | null>(null);
  const [bad, setBad] = useState<string | null>(null);
  const current = data.data ?? {};
  const text = (v: unknown) => (typeof v === "string" ? v : JSON.stringify(v, null, 2));
  const values = draft ?? Object.fromEntries(Object.entries(current).map(([k, v]) => [k, text(v)]));
  const dirty = draft !== null;
  function submit() {
    const out: Record<string, unknown> = {};
    for (const [k, raw] of Object.entries(values)) {
      if (typeof current[k] === "string" || current[k] === undefined) {
        out[k] = raw;
        continue;
      }
      try {
        out[k] = JSON.parse(raw);
      } catch {
        setBad(k);
        return;
      }
    }
    setBad(null);
    save.mutate(out, { onSuccess: () => setDraft(null) });
  }
  return (
    <section className="card stack">
      <h2>{t(`web.process.${name}`)}</h2>
      <p className="muted small">{t(`web.process.${name}_hint`)}</p>
      <ErrorNote error={data.error ?? save.error} />
      {Object.keys(current).length === 0 && !data.isLoading ? <p className="empty">{t("web.process.not_yet")}</p> : null}
      <div className="form-grid">
        {Object.keys(values).map((k) => (
          <label key={k} className="field wide">
            <span className="mono">{k}</span>
            <textarea
              rows={Math.min(Math.max((values[k] ?? "").split("\n").length, 2), 12)}
              value={values[k]}
              aria-invalid={bad === k}
              onChange={(e) => setDraft({ ...values, [k]: e.target.value })}
            />
            {bad === k ? <span className="bad small">{t("web.process.bad_json")}</span> : null}
          </label>
        ))}
      </div>
      {Object.keys(values).length > 0 ? (
        <div className="row">
          <button className="primary" disabled={!dirty || save.isPending} onClick={submit}>
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

/** Behind the scenes of one project: the plan and style everything is generated from, and every model call. */
export function ProcessPanel({ id }: { id: number }) {
  return (
    <div className="stack">
      <DataEditor id={id} name="plan" />
      <DataEditor id={id} name="style" />
      <AiCalls id={id} />
    </div>
  );
}
