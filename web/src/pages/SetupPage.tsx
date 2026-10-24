import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useSetup, useSetupConfig, useVerifySetup, useWriteSetupConfig } from "../api/setup";
import type { SetupComponent, SetupStep } from "../api/setup";
import { ErrorNote } from "../components/ErrorNote";
import { useT } from "../i18n";

const TONE: Record<string, string> = { ok: "completed", partial: "stale", missing: "failed", unverified: "queued" };
const MACHINE_TONE: Record<string, string> = { ok: "completed", warn: "stale", fail: "failed" };

function size(gib: number, measured: boolean): string {
  if (gib <= 0) return "-";
  return `${measured ? "" : "~"}${gib >= 0.1 ? `${gib.toFixed(1)} GiB` : `${Math.round(gib * 1024)} MiB`}`;
}

/** One step: the command to copy (never run from here), or for a download its source, file and size. */
function StepView({ step }: { step: SetupStep }) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void navigator.clipboard?.writeText(step.shell).then(() => setCopied(true));
  };
  return (
    <li className={step.done ? "muted" : undefined}>
      <div className="row">
        <span className="chip chip-queued">{t(`setup.step.kind.${step.kind}`)}</span>
        <code className="small">{step.id}</code>
        {step.done ? <span className="chip chip-completed">{t("setup.state.ok")}</span> : null}
        {step.runnable && !step.done ? <span className="muted small">{t("web.setup.run_in_terminal", { id: step.id })}</span> : null}
      </div>
      {step.note_key ? <p className="small">{t(step.note_key)}</p> : null}
      {step.error ? <p className="error-box">{t(`api.error.setup_${step.error}`)}</p> : null}
      {step.shell ? (
        <div className="row">
          <pre className="transcript">{step.shell}</pre>
          <button onClick={copy}>{copied ? t("web.setup.copied") : t("web.setup.copy")}</button>
        </div>
      ) : null}
      {step.kind === "download" ? (
        <p className="small">
          {t("web.setup.download_info", {
            file: step.file,
            source: step.source,
            size: step.size_gib ? `${step.size_gib.toFixed(1)} GiB` : "?",
          })}
        </p>
      ) : null}
      {step.needs_service ? <p className="muted small">{t("web.setup.needs_service", { name: step.needs_service })}</p> : null}
    </li>
  );
}

function ComponentRow({ c }: { c: SetupComponent }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const todo = c.state !== "ok";
  const missing = c.checks.filter((k) => k.state === "missing").map((k) => k.label);
  const unknown = c.checks.filter((k) => k.state === "unknown").map((k) => k.label);
  return (
    <>
      <tr>
        <td>
          <span className="strong">{t(c.label_key)}</span>{" "}
          <span className={`chip chip-${c.need === "required" ? "queued" : "stale"}`}>{t(`setup.need.${c.need}`)}</span>
          <div className="muted small">{t(c.note_key)}</div>
          {todo && missing.length > 0 ? <div className="small">{t("web.setup.missing", { items: missing.join(", ") })}</div> : null}
          {unknown.length > 0 ? <div className="muted small">{t("web.setup.unverified", { items: unknown.join(", ") })}</div> : null}
          {c.blocked_by.length > 0 ? (
            <div className="muted small">{t("web.setup.blocked_by", { items: c.blocked_by.map((b) => t(`setup.c.${b}`)).join(", ") })}</div>
          ) : null}
        </td>
        <td className="nowrap">
          <span className={`chip chip-${TONE[c.state] ?? "stale"}`}>{t(`setup.state.${c.state}`)}</span>
        </td>
        <td className="nowrap">{size(c.size_gib, c.measured)}</td>
        <td className="small">
          {c.license}
          {c.url ? (
            <div>
              <a href={c.url} target="_blank" rel="noreferrer">
                {c.url}
              </a>
            </div>
          ) : null}
        </td>
        <td className="nowrap">
          {c.steps.length > 0 ? (
            <button onClick={() => setOpen(!open)} aria-expanded={open}>
              {open ? t("web.setup.hide_steps") : t("web.setup.show_steps", { count: c.steps.length })}
            </button>
          ) : null}
        </td>
      </tr>
      {open ? (
        <tr>
          <td colSpan={5}>
            <ul className="issue-list">
              {c.steps.map((s) => (
                <StepView key={s.id} step={s} />
              ))}
            </ul>
          </td>
        </tr>
      ) : null}
    </>
  );
}

/** The install guide: what this machine has, what is missing and how to install it, the configuration to write, a check. */
export function SetupPage() {
  const t = useT();
  const qc = useQueryClient();
  const [profile, setProfile] = useState("full");
  const [root, setRoot] = useState("");
  const [llm, setLlm] = useState("");
  const [onlyTodo, setOnlyTodo] = useState(false);
  const choice = { profile, root };
  const setup = useSetup(choice);
  const config = useSetupConfig(choice, llm);
  const write = useWriteSetupConfig();
  const verify = useVerifySetup();
  const data = setup.data;
  const changed = (config.data?.files ?? []).filter((f) => f.changed);

  const doWrite = () => {
    if (!window.confirm(t("web.setup.write_confirm"))) return;
    write.mutate({ ...choice, llm_model: llm }, { onSuccess: () => void qc.invalidateQueries({ queryKey: ["setup"] }) });
  };
  return (
    <div className="stack">
      <h1>{t("web.setup.title")}</h1>
      <p className="muted">{t("web.setup.subtitle")}</p>
      <ErrorNote error={setup.error ?? config.error ?? write.error ?? verify.error} />
      <div className="row">
        <label>
          {t("web.setup.profile")}{" "}
          <select value={profile} onChange={(e) => setProfile(e.target.value)}>
            {(data?.profiles ?? [{ name: "full", label_key: "setup.profile.full" }]).map((p) => (
              <option key={p.name} value={p.name}>
                {t(p.label_key)}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("web.setup.root")}{" "}
          <input value={root} onChange={(e) => setRoot(e.target.value)} placeholder={data?.default_root ?? ""} />
        </label>
        <button onClick={() => setup.refetch()} disabled={setup.isFetching}>
          {t("web.setup.recheck")}
        </button>
        <span className="spacer" />
        <Link className="button" to="/launch">
          {t("web.setup.to_launch")}
        </Link>
      </div>
      {data ? <p className="muted small">{t(data.profiles.find((p) => p.name === profile)?.note_key ?? "setup.profile.full.note")}</p> : null}
      {setup.isLoading ? <p className="muted">{t("web.loading")}</p> : null}
      {data ? (
        <>
          <div className={`banner ${data.ready ? "banner-ok" : "banner-warn"}`} role="status">
            <strong>{data.ready ? t("web.setup.ready") : t("web.setup.not_ready")}</strong>{" "}
            {data.downloads.length > 0 ? <span>{t("web.setup.downloads_total", { count: data.downloads.length, gib: data.download_gib.toFixed(0) })}</span> : null}
          </div>

          <h2>{t("web.setup.machine")}</h2>
          <ul className="issue-list">
            {data.machine.map((m) => (
              <li key={m.key}>
                <span className={`chip chip-${MACHINE_TONE[m.state] ?? "stale"}`}>{m.state}</span>{" "}
                {t(`setup.machine.${m.key}.${m.state}`, m.params)}
              </li>
            ))}
          </ul>

          <div className="row">
            <h2>{t("web.setup.components")}</h2>
            <label className="small">
              <input type="checkbox" checked={onlyTodo} onChange={(e) => setOnlyTodo(e.target.checked)} /> {t("web.setup.only_todo")}
            </label>
          </div>
          {data.capabilities.map((cap) => {
            const rows = data.components.filter((c) => c.capability === cap.name && (!onlyTodo || c.state !== "ok"));
            if (rows.length === 0) return null;
            return (
              <div key={cap.name} className="stack small">
                <div className="row">
                  <strong>{t(cap.label_key)}</strong>
                  <span className={`chip chip-${cap.need === "required" ? "queued" : "stale"}`}>{t(`setup.need.${cap.need}`)}</span>
                  <span className={`chip chip-${TONE[cap.state] ?? "stale"}`}>{t(`setup.state.${cap.state}`)}</span>
                  <span className="muted">{t(cap.note_key)}</span>
                </div>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>{t("web.setup.col.component")}</th>
                        <th>{t("web.setup.col.state")}</th>
                        <th>{t("web.setup.col.size")}</th>
                        <th>{t("web.setup.col.license")}</th>
                        <th>{t("web.setup.col.steps")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((c) => (
                        <ComponentRow key={c.name} c={c} />
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            );
          })}
          <p className="muted small">{t("web.setup.size_note")}</p>

          {data.downloads.length > 0 ? (
            <>
              <h2>{t("web.setup.downloads")}</h2>
              <p className="muted small">{t("web.setup.downloads_note")}</p>
              <ul className="issue-list">
                {data.downloads.map((d) => (
                  <li key={d.id}>
                    <span className="strong">{d.file}</span> <span className="muted small">{d.source}</span>{" "}
                    <span className="chip chip-stale">{d.size_gib ? `${d.size_gib.toFixed(1)} GiB` : "?"}</span>
                  </li>
                ))}
              </ul>
            </>
          ) : null}

          <h2>{t("web.setup.config")}</h2>
          <p className="muted small">{t("web.setup.config_note")}</p>
          <label>
            {t("web.setup.llm_model")} <input value={llm} onChange={(e) => setLlm(e.target.value)} placeholder="gemma4:31b" />
          </label>
          {changed.length === 0 ? <p className="muted">{t("web.setup.config_nothing")}</p> : null}
          {changed.map((f) => (
            <details key={f.name} open>
              <summary>
                <code>{f.path}</code> <span className="muted small">{f.exists ? t("web.setup.config_merge") : t("web.setup.config_new")}</span>
              </summary>
              <pre className="transcript">{f.changes.map((c) => `+ ${c}`).join("\n")}</pre>
            </details>
          ))}
          <div className="row">
            <button className="primary" onClick={doWrite} disabled={write.isPending || changed.length === 0}>
              {t("web.setup.write")}
            </button>
          </div>
          {write.data ? (
            <ul className="issue-list">
              {write.data.written.map((w) => (
                <li key={w.path}>
                  <span className="chip chip-completed">{t("web.setup.written")}</span> <code>{w.path}</code>{" "}
                  {w.backup ? <span className="muted small">{t("web.setup.backup", { path: w.backup })}</span> : null}
                </li>
              ))}
            </ul>
          ) : null}

          <h2>{t("web.setup.verify")}</h2>
          <p className="muted small">{t("web.setup.verify_note")}</p>
          <div className="row">
            <button onClick={() => verify.mutate(choice)} disabled={verify.isPending}>
              {t("web.setup.verify_run")}
            </button>
          </div>
          {verify.data ? (
            <>
              <div className={`banner ${verify.data.ok ? "banner-ok" : "banner-warn"}`} role="status">
                {verify.data.ok ? t("web.setup.verify_ok") : t("web.setup.verify_bad")}
              </div>
              <ul className="issue-list">
                {verify.data.entries
                  .filter((e) => e.required || !e.ok)
                  .map((e) => (
                    <li key={e.name}>
                      <span className={`chip chip-${e.ok ? "completed" : e.required ? "failed" : "stale"}`}>{e.label}</span>{" "}
                      <span className="strong">{e.name}</span> <span className="muted small">{e.detail}</span>
                    </li>
                  ))}
              </ul>
            </>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
