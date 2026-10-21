import { useEffect, useState } from "react";
import { useMeta } from "../api/hooks";
import { useDiff, useSpecActions, useVersions } from "../api/specs";
import type { Spec, SpecEdit } from "../api/types";
import { fieldsOfKind, textFieldKey } from "../lib/subjects";
import { useT } from "../i18n";
import { ErrorNote } from "./ErrorNote";
import { ValueEditor } from "./ValueEditor";
import { formatValue, useLabel } from "../lib/labels";


export function SpecEditor({ pid, spec, review, onAcknowledge }: { pid: number; spec: Spec; review: boolean; onAcknowledge: () => void }) {
  const t = useT();
  const meta = useMeta();
  const act = useSpecActions(pid, spec);
  const [prompt, setPrompt] = useState(spec.prompt);
  const [negative, setNegative] = useState(spec.negative_prompt);
  const [text, setText] = useState(spec.text_payload);
  const [seed, setSeed] = useState(spec.seed === null ? "" : String(spec.seed));
  const [model, setModel] = useState(spec.model_ref);
  const [params, setParams] = useState<Record<string, unknown>>({ ...(spec.params ?? {}) });
  const [note, setNote] = useState("");

  // Reload the form when another spec is chosen or a new version arrives.
  useEffect(() => {
    setPrompt(spec.prompt);
    setNegative(spec.negative_prompt);
    setText(spec.text_payload);
    setSeed(spec.seed === null ? "" : String(spec.seed));
    setModel(spec.model_ref);
    setParams({ ...(spec.params ?? {}) });
    setNote("");
  }, [spec.id, spec.version, spec.slot]);

  const models = (meta.data?.models ?? []).filter((m) => m.kind === spec.kind);
  const f = fieldsOfKind(spec.kind);
  const seedValue = seed.trim() === "" ? null : Number(seed);
  const seedOk = seedValue === null || Number.isInteger(seedValue);

  const changes: Partial<SpecEdit> = {};
  if (prompt !== spec.prompt) changes.prompt = prompt;
  if (negative !== spec.negative_prompt) changes.negative_prompt = negative;
  if (text !== spec.text_payload) changes.text_payload = text;
  if (model !== spec.model_ref) changes.model_ref = model;
  if (seedOk && seedValue !== spec.seed) changes.seed = seedValue;
  if (JSON.stringify(params) !== JSON.stringify(spec.params ?? {})) changes.params = params;
  const dirty = Object.keys(changes).length > 0;
  const locked = spec.locked;

  return (
    <div className="stack">
      {review ? (
        <div className="notice">
          <span>{t("web.creative.needs_review")}</span>
          <button className="link" onClick={onAcknowledge}>
            {t("web.creative.still_fine")}
          </button>
        </div>
      ) : null}
      {locked ? (
        <div className="notice">
          <span>{t("web.creative.locked")}</span>
          <button className="link" onClick={() => act.unlock.mutate()}>
            {t("web.creative.unlock")}
          </button>
        </div>
      ) : null}
      <fieldset className="stack" disabled={locked}>
        {f.prompt ? (
          <label className="field">
            <span>{t("web.creative.prompt")}</span>
            <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={4} />
          </label>
        ) : null}
        {f.negative ? (
          <label className="field">
            <span>{t("web.creative.negative")}</span>
            <textarea value={negative} onChange={(e) => setNegative(e.target.value)} rows={2} />
          </label>
        ) : null}
        <label className="field">
          <span>{t(textFieldKey(spec.subject_type))}</span>
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={spec.subject_type === "music.theme_song" ? 8 : 3} />
        </label>
        <div className="row">
          <label className="field grow">
            <span>{t("web.creative.model")}</span>
            <select value={model} onChange={(e) => setModel(e.target.value)}>
              {!models.some((m) => m.ref === model) ? <option value={model}>{model || t("web.creative.default_model")}</option> : null}
              {models.map((m) => (
                <option key={m.ref} value={m.ref}>
                  {m.ref}
                </option>
              ))}
            </select>
          </label>
          {f.seed ? (
            <label className="field">
              <span>{t("web.creative.seed")}</span>
              <input value={seed} onChange={(e) => setSeed(e.target.value)} inputMode="numeric" placeholder={t("web.creative.seed_auto")} aria-invalid={!seedOk} />
            </label>
          ) : null}
        </div>
        <div className="field wide">
          <span>{t("web.creative.params")}</span>
          {Object.keys(params).length > 0 ? <ValueEditor value={params} onChange={setParams} prefix="param" /> : null}
          <AddParam onAdd={(k, v) => setParams({ ...params, [k]: v })} existing={Object.keys(params)} />
        </div>
        <label className="field">
          <span>{t("web.creative.note")}</span>
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder={t("web.creative.note_hint")} />
        </label>
        <div className="row">
          <button className="primary" disabled={!dirty || !seedOk || act.edit.isPending} onClick={() => act.edit.mutate({ ...changes, note })}>
            {t("web.creative.save_version", { v: spec.version + 1 })}
          </button>
          <span className="small muted">{t("web.creative.version_of", { v: spec.version, author: t(`web.creative.author.${spec.author}`) })}</span>
          <span className="spacer" />
          {!locked ? (
            <button className="link" onClick={() => act.lock.mutate()}>
              {t("web.creative.lock")}
            </button>
          ) : null}
        </div>
      </fieldset>
      <ErrorNote error={act.edit.error ?? act.lock.error ?? act.unlock.error} />
      <History pid={pid} spec={spec} />
    </div>
  );
}

/** A parameter that is not there yet: a name and a value (numbers and yes/no are recognised). */
function AddParam({ onAdd, existing }: { onAdd: (key: string, value: unknown) => void; existing: string[] }) {
  const t = useT();
  const [key, setKey] = useState("");
  const [val, setVal] = useState("");
  const name = key.trim();
  const ok = name !== "" && !existing.includes(name);
  const parse = (raw: string): unknown => {
    if (raw.trim() !== "" && Number.isFinite(Number(raw))) return Number(raw);
    if (raw === "true" || raw === "false") return raw === "true";
    return raw;
  };
  return (
    <div className="row">
      <input value={key} onChange={(e) => setKey(e.target.value)} placeholder={t("web.creative.param_name")} aria-label={t("web.creative.param_name")} />
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

function History({ pid, spec }: { pid: number; spec: Spec }) {
  const t = useT();
  const act = useSpecActions(pid, spec);
  const versions = useVersions(pid, spec);
  const [open, setOpen] = useState<number | null>(null);
  const diff = useDiff(pid, spec, open, spec.version);
  const fieldName = useLabel("specfield");
  const paramName = useLabel("param");
  const list = versions.data ?? [];
  if (list.length <= 1) return null;
  return (
    <details className="card">
      <summary>{t("web.creative.history", { n: list.length })}</summary>
      <ul className="history">
        {list.map((v) => (
          <li key={v.version}>
            <div className="row small">
              <strong>{t("web.creative.v", { v: v.version })}</strong>
              <span className="muted">{t(`web.creative.author.${v.author}`)}</span>
              <span className="muted">{v.note}</span>
              <span className="spacer" />
              {v.version !== spec.version ? (
                <>
                  <button className="link" onClick={() => setOpen(open === v.version ? null : v.version)}>
                    {t("web.creative.compare")}
                  </button>
                  <button className="link" disabled={spec.locked || act.revert.isPending} onClick={() => act.revert.mutate(v.version)}>
                    {t("web.creative.revert")}
                  </button>
                </>
              ) : (
                <span className="chip chip-completed">{t("web.creative.current")}</span>
              )}
            </div>
            {open === v.version && diff.data ? (
              <table className="diff">
                <tbody>
                  {diff.data.map((d) => (
                    <tr key={d.field}>
                      <th>{fieldName(d.field)}</th>
                      <td className="del">{formatValue(d.before, t, paramName)}</td>
                      <td className="add">{formatValue(d.after, t, paramName)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}
          </li>
        ))}
      </ul>
    </details>
  );
}
