import { Link } from "react-router-dom";
import { ValueView } from "./ValueView";
import { useFlow, useHealth } from "../api/hooks";
import type { FlowMedia, FlowStep } from "../api/types";
import { useT } from "../i18n";
import { ErrorNote } from "./ErrorNote";
import { StatusChip } from "./StatusChip";

function MediaRow({ m, health }: { m: FlowMedia; health: Map<string, { ok: boolean; label: string }> }) {
  const t = useT();
  const counts = m.counts;
  return (
    <li className="flow-media">
      <div className="row">
        <strong>{m.subject === "episode" ? t("web.flow.subject.episode") : t(`spec.subject.${m.subject}`)}</strong>
        <span className="muted small">{t(`media.kind.${m.kind}`)}</span>
        <span className="spacer" />
        {counts ? (
          <span className="small">
            {t("web.flow.made", { made: counts.made, specs: counts.specs })}
            {counts.stale > 0 ? <span className="bad">{` · ${t("web.flow.stale", { n: counts.stale })}`}</span> : null}
          </span>
        ) : null}
      </div>
      {m.models.map((model) => {
        if (!model.ref) return <p key="none" className="small bad">{t("web.flow.no_model")}</p>;
        const h = model.provider ? health.get(`adapter.${model.provider}`) : undefined;
        return (
          <details key={model.ref} className="flow-model">
            <summary>
              <span className="mono">{model.ref}</span>
              <span className="muted small">{` ${model.provider ?? ""}`}</span>
              {h ? <span className={`chip chip-${h.ok ? "completed" : "stale"}`}>{h.label}</span> : null}
              {model.heavy ? <span className="chip">{t("web.flow.heavy")}</span> : null}
              {model.commercial_ok === false ? <span className="chip chip-stale">{t("web.flow.restricted")}</span> : null}
            </summary>
            <p className="muted small">{t("web.flow.params_hint")}</p>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("web.flow.tier")}</th>
                    <th>{t("web.flow.params")}</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(model.params ?? {}).map(([tier, params]) => (
                    <tr key={tier}>
                      <td className="strong">{t(`media.tier.${tier}`)}</td>
                      <td className="small"><ValueView value={params} prefix="param" /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        );
      })}
    </li>
  );
}

function Step({ step, index, health }: { step: FlowStep; index: number; health: Map<string, { ok: boolean; label: string }> }) {
  const t = useT();
  return (
    <li className={`flow-step flow-${step.how}`}>
      <div className="row">
        <span className="flow-index" aria-hidden="true">
          {index + 1}
        </span>
        <h3 className="grow">{t(`workflow.stage.${step.stage}`)}</h3>
        <span className="chip">{t(`web.flow.how.${step.how}`)}</span>
        {step.status ? <StatusChip status={step.status} /> : null}
      </div>
      <p className="muted small">{t(`web.flow.what.${step.stage}`)}</p>
      <div className="flow-io small">
        <span className="muted">{t("web.flow.reads")}</span>
        {step.reads.map((k) => (
          <span key={k} className="chip">
            {t(`web.flow.data.${k}`)}
          </span>
        ))}
        <span aria-hidden="true">{"→"}</span>
        <span className="muted">{t("web.flow.writes")}</span>
        {step.writes.map((k) => (
          <span key={k} className="chip chip-completed">
            {t(`web.flow.data.${k}`)}
          </span>
        ))}
      </div>
      {step.agents.length > 0 ? (
        <ul className="plain flow-agents">
          {step.agents.map((a) => (
            <li key={a.name} className="row small">
              <Link to={`/prompts#${a.name}`} className="mono">
                {a.name}
              </Link>
              <span className="muted">{a.description}</span>
              <span className="spacer" />
              <span className="mono">{`${a.model} @ ${a.backend}`}</span>
              {a.optional ? <span className="chip">{t("web.flow.optional")}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
      {step.media.length > 0 ? (
        <ul className="plain flow-medias">
          {step.media.map((m) => (
            <MediaRow key={m.subject} m={m} health={health} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

/** The whole production chain, step by step: what runs, on which model, with which parameters, and (for a project) how far it is. */
export function FlowChain({ projectId }: { projectId?: number }) {
  const t = useT();
  const flow = useFlow(projectId);
  const health = useHealth();
  const states = new Map((health.data?.entries ?? []).map((e) => [e.name, { ok: e.ok, label: e.label }]));
  return (
    <div className="stack">
      <p className="muted">{t("web.flow.intro")}</p>
      <ErrorNote error={flow.error} />
      {flow.isLoading ? <p className="muted">{t("web.loading")}</p> : null}
      <ol className="flow">
        {flow.data?.steps.map((s, i) => (
          <Step key={s.stage} step={s} index={i} health={states} />
        ))}
      </ol>
    </div>
  );
}
