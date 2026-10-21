import { Fragment, useMemo, useState } from "react";
import { useAiCall, useAiCalls, useProjectData, useSaveProjectData } from "../api/hooks";
import { ErrorNote } from "../components/ErrorNote";
import { ValueEditor } from "../components/ValueEditor";
import { ValueView } from "../components/ValueView";
import { useT } from "../i18n";
import { duration } from "../lib/format";

/** A model answer that is a JSON object is shown as labelled fields; anything else stays plain text. */
function asObject(text: string): Record<string, unknown> | null {
  const trimmed = text.trim();
  if (!trimmed.startsWith("{")) return null;
  try {
    const v: unknown = JSON.parse(trimmed);
    return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

function agentName(t: (k: string) => string, name: string): string {
  const key = `agent.${name}.title`;
  const text = t(key);
  return text === key ? name : text;
}

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
          {k === "response" && asObject(text) ? <div className="transcript"><ValueView value={asObject(text)} /></div> : <pre className="transcript">{text}</pre>}
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
              {agentName(t, l)}
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
                    <td className="strong">{agentName(t, c.label || c.agent)}</td>
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

/** The plan / style as labelled fields (text, numbers, lists, groups), never as JSON. */
function DataEditor({ id, name }: { id: number; name: "plan" | "style" }) {
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
