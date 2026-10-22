import { useState } from "react";
import { useProjects } from "../api/hooks";
import { useLlm, useResetLlmRole, useSetLlmRole, useTestLlm } from "../api/llm";
import type { LlmBackend, LlmRole } from "../api/llm";
import { ErrorNote } from "../components/ErrorNote";
import { useT } from "../i18n";

const CUSTOM = "__custom__";

function Backends({ backends }: { backends: LlmBackend[] }) {
  const t = useT();
  return (
    <section className="stack">
      <h2>{t("web.llm.backends")}</h2>
      <ul className="plain">
        {backends.map((b) => (
          <li key={b.name} className="row">
            <strong className="mono">{b.name}</strong>
            <span className="muted small">{b.url}</span>
            <span className={`chip chip-${b.state === "ok" ? "completed" : "stale"}`}>{t(`web.llm.state.${b.state}`)}</span>
            {b.state === "ok" ? (
              <span className="muted small">{b.models.length ? t("web.llm.models_count", { n: b.models.length }) : t("web.llm.no_models")}</span>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

function RoleRow({ role, backends, projectId }: { role: LlmRole; backends: LlmBackend[]; projectId: number | null }) {
  const t = useT();
  const set = useSetLlmRole(projectId);
  const reset = useResetLlmRole(projectId);
  const test = useTestLlm();
  const [custom, setCustom] = useState("");
  const options = backends.filter((b) => b.state === "ok").flatMap((b) => b.models.map((m) => ({ backend: b.name, model: m })));
  const current = `${role.backend}|${role.model}`;
  const known = options.some((o) => `${o.backend}|${o.model}` === current);
  const choose = (value: string) => {
    if (value === CUSTOM) return;
    const [backend, ...rest] = value.split("|");
    set.mutate({ role: role.name, backend: backend ?? role.backend, model: rest.join("|") });
  };
  return (
    <tr>
      <td>
        <strong>{t(`llm.role.${role.name}`)}</strong>
        <div className="muted small">{t(`llm.role.${role.name}.note`)}</div>
      </td>
      <td>
        <select aria-label={t("web.llm.model")} value={known ? current : CUSTOM} onChange={(e) => choose(e.target.value)} disabled={set.isPending}>
          {!known ? <option value={CUSTOM}>{`${role.model} (${role.backend})`}</option> : null}
          {options.map((o) => (
            <option key={`${o.backend}|${o.model}`} value={`${o.backend}|${o.model}`}>
              {backends.length > 1 ? `${o.model} · ${o.backend}` : o.model}
            </option>
          ))}
        </select>
        <div className="row small">
          <input aria-label={t("web.llm.custom_name")} placeholder={t("web.llm.custom")} value={custom} onChange={(e) => setCustom(e.target.value)} />
          <button
            type="button"
            className="link"
            disabled={!custom.trim() || set.isPending}
            onClick={() => set.mutate({ role: role.name, backend: role.backend, model: custom.trim() }, { onSuccess: () => setCustom("") })}
          >
            {t("web.llm.use")}
          </button>
        </div>
        <ErrorNote error={set.error ?? reset.error} />
      </td>
      <td className="muted small">{t(`web.llm.source.${role.source}`)}</td>
      <td className="actions">
        <button type="button" className="link" disabled={test.isPending} onClick={() => test.mutate({ backend: role.backend, model: role.model })}>
          {test.isPending ? t("web.llm.testing") : t("web.llm.test")}
        </button>
        {role.source !== "config" ? (
          <button type="button" className="link" disabled={reset.isPending} onClick={() => reset.mutate(role.name)}>
            {t("web.llm.reset")}
          </button>
        ) : null}
        {test.data ? (
          <div className="small">{test.data.ok ? t("web.llm.test_ok", { seconds: test.data.seconds }) : t("web.llm.test_bad")}</div>
        ) : null}
        <ErrorNote error={test.error} />
      </td>
    </tr>
  );
}

/** Which language model each step uses, for every project or for one: chosen here, applied to the next call. */
export function LlmPage() {
  const t = useT();
  const projects = useProjects();
  const [projectId, setProjectId] = useState<number | null>(null);
  const llm = useLlm(projectId);
  const groups = llm.data ? [...new Set(llm.data.roles.map((r) => r.group))] : [];
  return (
    <div className="stack">
      <h1>{t("web.llm.title")}</h1>
      <p className="muted">{t("web.llm.intro")}</p>
      <label className="field">
        <span>{t("web.llm.scope")}</span>
        <select value={projectId ?? ""} onChange={(e) => setProjectId(e.target.value === "" ? null : Number(e.target.value))}>
          <option value="">{t("web.llm.scope_global")}</option>
          {(projects.data ?? []).map((p) => (
            <option key={p.id} value={p.id}>
              {t("web.llm.scope_project", { name: p.name })}
            </option>
          ))}
        </select>
      </label>
      <ErrorNote error={llm.error} />
      {llm.data ? <Backends backends={llm.data.backends} /> : null}
      {llm.data
        ? groups.map((g) => (
            <section key={g} className="stack">
              <h2>{t(`llm.group.${g}`)}</h2>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>{t("web.llm.role")}</th>
                      <th>{t("web.llm.model")}</th>
                      <th>{t("web.llm.source")}</th>
                      <th>{t("web.llm.actions")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {llm.data.roles
                      .filter((r) => r.group === g)
                      .map((r) => (
                        <RoleRow key={`${projectId}-${r.name}-${r.backend}-${r.model}`} role={r} backends={llm.data.backends} projectId={projectId} />
                      ))}
                  </tbody>
                </table>
              </div>
            </section>
          ))
        : null}
    </div>
  );
}
