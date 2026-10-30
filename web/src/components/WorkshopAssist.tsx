import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAssistActions, useAssistRequirements } from "../api/workshop";
import { useI18n } from "../i18n";
import { ErrorNote } from "./ErrorNote";

type Values = Record<string, unknown>;

const shown = (v: unknown) => (typeof v === "string" ? v : JSON.stringify(v));

/** The AI assistant of one workshop module: the author's standing requirements for the module, a request for this
 * time, and a proposal for the form (prompt, lyrics, text, parameters) built from them and the wiki. The proposal is
 * shown first; ticked items are applied to the form on a click. Nothing is run or saved by a proposal. */
export function WorkshopAssist({
  module,
  kind,
  model,
  tier,
  values,
  params,
  onApply,
}: {
  module: string;
  kind: string;
  model: string;
  tier: string;
  values: Values;
  params: Values;
  onApply: (inputs: Values, params: Values) => void;
}) {
  const { t, locale } = useI18n();
  const saved = useAssistRequirements(module);
  const act = useAssistActions(module);
  const [requirements, setRequirements] = useState("");
  const [request, setRequest] = useState("");
  const [useWiki, setUseWiki] = useState(true);
  const [skip, setSkip] = useState<Record<string, boolean>>({});
  const [done, setDone] = useState("");
  useEffect(() => {
    if (saved.data) setRequirements(saved.data.requirements);
  }, [saved.data]);

  const proposal = act.design.data;
  const run = () => {
    setSkip({});
    setDone("");
    act.design.mutate({ module, kind, model_ref: model, tier, inputs: values, params, request, use_wiki: useWiki, language: locale });
  };
  const apply = () => {
    if (!proposal) return;
    const inputs = Object.fromEntries(proposal.inputs.filter((i) => !skip[`i:${i.name}`]).map((i) => [i.name, i.value]));
    const next = Object.fromEntries(proposal.params.filter((p) => !skip[`p:${p.name}`]).map((p) => [p.name, p.value]));
    onApply(inputs, next);
    setDone(t("web.workshop.assist.applied"));
  };
  const toggle = (key: string) => setSkip({ ...skip, [key]: !skip[key] });
  const nothing = proposal && proposal.inputs.length === 0 && proposal.params.length === 0;
  const ticked =
    proposal === undefined ? 0 : proposal.inputs.filter((i) => !skip[`i:${i.name}`]).length + proposal.params.filter((p) => !skip[`p:${p.name}`]).length;

  return (
    <details className="card stack" open={false}>
      <summary className="strong">{t("web.workshop.assist.title")}</summary>
      <p className="muted small">{t("web.workshop.assist.hint")}</p>
      <label className="field wide">
        <span>{t("web.workshop.assist.requirements")}</span>
        <textarea rows={3} value={requirements} onChange={(e) => setRequirements(e.target.value)} />
        <span className="muted small">{t("web.workshop.assist.requirements_hint")}</span>
      </label>
      <div className="row">
        <button
          type="button"
          onClick={() => act.save.mutate(requirements)}
          disabled={act.save.isPending || requirements.trim() === (saved.data?.requirements ?? "")}
        >
          {t("web.workshop.assist.save_requirements")}
        </button>
        {act.save.isSuccess && requirements.trim() === (saved.data?.requirements ?? "") ? <span className="muted small">{t("web.workshop.assist.saved")}</span> : null}
      </div>
      <label className="field wide">
        <span>{t("web.workshop.assist.request")}</span>
        <textarea rows={2} placeholder={t("web.workshop.assist.request_hint")} value={request} onChange={(e) => setRequest(e.target.value)} />
      </label>
      <div className="row">
        <button type="button" className="primary" onClick={run} disabled={act.design.isPending}>
          {act.design.isPending ? t("web.workshop.assist.running") : t("web.workshop.assist.run")}
        </button>
        <label className="row small">
          <input type="checkbox" checked={useWiki} onChange={(e) => setUseWiki(e.target.checked)} />
          <span>{t("web.workshop.assist.use_wiki")}</span>
        </label>
      </div>
      <ErrorNote error={act.design.error ?? act.save.error} />
      {proposal ? (
        <div className="stack">
          <strong>{t("web.workshop.assist.result")}</strong>
          <span className="muted small">{t("web.workshop.assist.by", { model: proposal.model, backend: proposal.backend, seconds: proposal.seconds })}</span>
          {proposal.notes ? (
            <div>
              <span className="strong small">{t("web.workshop.assist.notes")}</span>
              <p>{proposal.notes}</p>
            </div>
          ) : null}
          {nothing ? <p className="muted">{t("web.workshop.assist.nothing")}</p> : null}
          {proposal.inputs.map((i) => (
            <label key={`i:${i.name}`} className="card stack">
              <span className="row">
                <input type="checkbox" checked={!skip[`i:${i.name}`]} onChange={() => toggle(`i:${i.name}`)} />
                <strong>{t(`workshop.field.${i.name}`)}</strong>
              </span>
              <span className="muted small">{t("web.workshop.assist.now")}</span>
              <pre className="small">{i.current || t("web.workshop.assist.empty_value")}</pre>
              <span className="small">{t("web.workshop.assist.proposed")}</span>
              <pre>{i.value}</pre>
            </label>
          ))}
          {proposal.params.map((p) => (
            <label key={`p:${p.name}`} className="row">
              <input type="checkbox" checked={!skip[`p:${p.name}`]} onChange={() => toggle(`p:${p.name}`)} />
              <strong>{p.name}</strong>
              <span className="muted">{p.current === null || p.current === undefined ? t("web.workshop.assist.empty_value") : shown(p.current)}</span>
              <span>{"→"}</span>
              <code>{shown(p.value)}</code>
            </label>
          ))}
          {proposal.dropped.length > 0 ? (
            <div>
              <span className="strong small">{t("web.workshop.assist.dropped")}</span>
              <ul className="small muted">
                {proposal.dropped.map((d) => (
                  <li key={`${d.part}:${d.name}`}>{t(`web.workshop.assist.dropped.${d.reason}`, { name: d.name, detail: d.detail })}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {proposal.sources.length > 0 ? (
            <div>
              <span className="strong small">{t("web.workshop.assist.sources")}</span>
              <ul className="small">
                {proposal.sources.map((s) => (
                  <li key={s.n}>
                    {`[${s.n}] `}
                    <Link to={`/wiki/${s.slug}`}>{s.title}</Link>
                    {s.heading && s.heading !== s.title ? <span className="muted">{` / ${s.heading}`}</span> : null}
                    {s.cited ? <span className="chip">{t("web.workshop.assist.cited")}</span> : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : useWiki ? (
            <p className="muted small">{t("web.workshop.assist.no_sources")}</p>
          ) : null}
          <div className="row">
            <button type="button" className="primary" onClick={apply} disabled={ticked === 0}>
              {t("web.workshop.assist.apply")}
            </button>
            <span className="muted small">{done}</span>
          </div>
        </div>
      ) : null}
    </details>
  );
}
