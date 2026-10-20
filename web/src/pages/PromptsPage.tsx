import { useEffect, useState } from "react";
import { useAgentEdit, useAgents } from "../api/hooks";
import type { Agent } from "../api/types";
import { ErrorNote } from "../components/ErrorNote";
import { useT } from "../i18n";

function Editor({ agent }: { agent: Agent }) {
  const t = useT();
  const edit = useAgentEdit();
  const [d, setD] = useState({
    system: agent.system, prompt: agent.prompt, model: agent.model, temperature: String(agent.temperature),
    max_tokens: String(agent.max_tokens), attempts: String(agent.attempts),
  });
  useEffect(
    () => setD({ system: agent.system, prompt: agent.prompt, model: agent.model, temperature: String(agent.temperature), max_tokens: String(agent.max_tokens), attempts: String(agent.attempts) }),
    [agent],
  );
  const dirty =
    d.system !== agent.system || d.prompt !== agent.prompt || d.model !== agent.model || Number(d.temperature) !== agent.temperature ||
    Number(d.max_tokens) !== agent.max_tokens || Number(d.attempts) !== agent.attempts;
  return (
    <section className="card stack" id={agent.name}>
      <div className="row">
        <h2 className="grow mono">{agent.name}</h2>
        {agent.overridden ? <span className="chip chip-stale">{t("web.prompts.modified")}</span> : <span className="chip">{t("web.prompts.packaged")}</span>}
      </div>
      <p className="muted small">{agent.description}</p>
      <p className="small">
        <span className="muted">{t("web.prompts.placeholders")}</span>{" "}
        {agent.inputs.map((i) => (
          <code key={i} className="chip">{`$${i}`}</code>
        ))}
      </p>
      <label className="field wide">
        <span>{t("web.prompts.system")}</span>
        <textarea rows={6} value={d.system} onChange={(e) => setD({ ...d, system: e.target.value })} />
      </label>
      <label className="field wide">
        <span>{t("web.prompts.prompt")}</span>
        <textarea rows={12} value={d.prompt} onChange={(e) => setD({ ...d, prompt: e.target.value })} />
      </label>
      <div className="form-grid">
        <label className="field">
          <span>{t("web.prompts.model")}</span>
          <input value={d.model} placeholder={t("web.prompts.model_hint")} onChange={(e) => setD({ ...d, model: e.target.value })} />
        </label>
        <label className="field">
          <span>{t("web.prompts.temperature")}</span>
          <input value={d.temperature} inputMode="decimal" onChange={(e) => setD({ ...d, temperature: e.target.value })} />
        </label>
        <label className="field">
          <span>{t("web.prompts.max_tokens")}</span>
          <input value={d.max_tokens} inputMode="numeric" onChange={(e) => setD({ ...d, max_tokens: e.target.value })} />
        </label>
        <label className="field">
          <span>{t("web.prompts.attempts")}</span>
          <input value={d.attempts} inputMode="numeric" onChange={(e) => setD({ ...d, attempts: e.target.value })} />
        </label>
      </div>
      <div className="row">
        <button
          className="primary"
          disabled={!dirty || edit.save.isPending}
          onClick={() =>
            edit.save.mutate({
              name: agent.name,
              body: { system: d.system, prompt: d.prompt, model: d.model, temperature: Number(d.temperature), max_tokens: Number(d.max_tokens), attempts: Number(d.attempts) },
            })
          }
        >
          {t("web.save")}
        </button>
        {agent.overridden ? (
          <button
            disabled={edit.reset.isPending}
            onClick={() => {
              if (window.confirm(t("web.prompts.reset_confirm"))) edit.reset.mutate(agent.name);
            }}
          >
            {t("web.prompts.reset")}
          </button>
        ) : null}
        <span className="muted small">{t("web.prompts.applies")}</span>
      </div>
      <ErrorNote error={edit.save.error ?? edit.reset.error} />
    </section>
  );
}

/** The instructions behind every AI step. Edit the wording; it applies to the next call. */
export function PromptsPage() {
  const t = useT();
  const agents = useAgents();
  const hash = typeof window === "undefined" ? "" : window.location.hash.slice(1);
  const [pick, setPick] = useState(hash);
  const current = agents.data?.find((a) => a.name === pick) ?? agents.data?.[0];
  return (
    <div className="stack">
      <h1>{t("web.nav.prompts")}</h1>
      <p className="muted">{t("web.prompts.intro")}</p>
      <ErrorNote error={agents.error} />
      <nav className="tabs" aria-label={t("web.nav.prompts")}>
        {agents.data?.map((a) => (
          <button key={a.name} className={a.name === current?.name ? "tab active" : "tab"} onClick={() => setPick(a.name)}>
            {a.name}
            {a.overridden ? ` ${t("web.prompts.dot")}` : ""}
          </button>
        ))}
      </nav>
      {current ? <Editor key={current.name} agent={current} /> : null}
    </div>
  );
}
