import { useState } from "react";
import { ApiError } from "../api/client";
import { type CliOption, useBuildCli, useCheckConnector, useParseCli, usePreviewComfy, useSaveComfy, useSaveConnector, useToolModels, useToolTemplates } from "../api/tools";
import { useMeta } from "../api/hooks";
import { ErrorNote } from "../components/ErrorNote";
import { useT } from "../i18n";

const NOT_IMPORTABLE = ["text.", "compose.", "train."];
const ROLES = ["param", "switch", "prompt", "negative_prompt", "seed", "image", "audio", "video", "file", "output", "output_dir", "fixed", "skip"];

function detailOf(error: unknown): string {
  return error instanceof ApiError && typeof error.detail === "string" ? error.detail : "";
}

type Issue = { level: string; code: string; message: string };

function Issues({ issues }: { issues: Issue[] }) {
  return (
    <ul className="small">
      {issues.map((i) => (
        <li key={`${i.code}-${i.message}`} className={i.level === "error" ? "error-text" : "muted"}>
          {i.message}
        </li>
      ))}
    </ul>
  );
}

const NEEDS_VALUE = ["param", "fixed"];

/** A command-line tool from its --help: every option a row, files by type, the output placed by the system. */
function CliWizard({ roles, kinds, onBuilt }: { roles: string[]; kinds: { value: string; label: string }[]; onBuilt: (models: string) => void }) {
  const t = useT();
  const parse = useParseCli();
  const build = useBuildCli();
  const [program, setProgram] = useState("");
  const [helpText, setHelpText] = useState("");
  const [options, setOptions] = useState<CliOption[]>([]);
  const [ref, setRef] = useState("");
  const [kind, setKind] = useState("video.generate");
  const [ext, setExt] = useState("mp4");
  const edit = (i: number, patch: Partial<CliOption>) => {
    setOptions((all) => all.map((o, j) => (j === i ? { ...o, ...patch } : o)));
    build.reset();
  };
  const read = async () => {
    build.reset();
    const parsed = await parse.mutateAsync({ program, help_text: helpText });
    setOptions(parsed.options);
    if (!ref) setRef(program.split("/").pop()?.replace(/[^A-Za-z0-9._-]+/g, "-") ?? "");
  };
  const make = async () => {
    const built = await build.mutateAsync({ ref, kind, program, options, ext, cwd: "", timeout_sec: 3600, license_tag: "", sample: {} });
    onBuilt(built.models);
  };
  return (
    <section className="stack">
      <h2>{t("web.tools.wizard")}</h2>
      <p className="muted small">{t("web.tools.wizard_hint")}</p>
      <label className="field">
        <span>{t("web.tools.wiz_program")}</span>
        <input className="mono" value={program} onChange={(e) => setProgram(e.target.value)} />
      </label>
      <label className="field">
        <span>{t("web.tools.wiz_help")}</span>
        <textarea className="mono" rows={4} spellCheck={false} value={helpText} onChange={(e) => setHelpText(e.target.value)} />
      </label>
      <div>
        <button disabled={(!program.trim() && !helpText.trim()) || parse.isPending} onClick={() => void read().catch(() => undefined)}>
          {t("web.tools.wiz_read")}
        </button>
      </div>
      <ErrorNote error={parse.error ?? build.error} />
      {parse.data ? (
        <p className="muted small">
          {parse.data.ran
            ? t("web.tools.wiz_ran", { program: parse.data.program, count: options.length })
            : t("web.tools.wiz_pasted", { count: options.length })}
        </p>
      ) : null}
      {options.length ? (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("web.tools.wiz_use")}</th>
                  <th>{t("web.tools.wiz_option")}</th>
                  <th>{t("web.tools.wiz_role")}</th>
                  <th>{t("web.tools.wiz_value")}</th>
                  <th>{t("web.tools.wiz_what")}</th>
                </tr>
              </thead>
              <tbody>
                {options.map((o, i) => (
                  <tr key={o.flag}>
                    <td>
                      <input type="checkbox" aria-label={o.flag} checked={o.include} onChange={(e) => edit(i, { include: e.target.checked })} />
                    </td>
                    <td className="mono strong">{[o.flag, ...o.metavars].join(" ")}</td>
                    <td>
                      <select aria-label={`${o.flag} role`} value={o.role} onChange={(e) => edit(i, { role: e.target.value, include: e.target.value !== "skip" })}>
                        {roles.map((r) => (
                          <option key={r} value={r}>{t(`web.tools.role.${r}`)}</option>
                        ))}
                      </select>
                    </td>
                    <td>
                      {NEEDS_VALUE.includes(o.role) ? (
                        <input
                          className="mono"
                          aria-label={`${o.flag} value`}
                          value={o.default == null ? "" : String(o.default)}
                          onChange={(e) => edit(i, { default: o.kind === "int" || o.kind === "float" ? (e.target.value === "" ? null : Number(e.target.value)) : e.target.value })}
                        />
                      ) : null}
                    </td>
                    <td className="muted small">{o.description}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="form-grid">
            <label className="field">
              <span>{t("web.tools.wiz_ref")}</span>
              <input value={ref} onChange={(e) => { setRef(e.target.value); build.reset(); }} />
            </label>
            <label className="field">
              <span>{t("web.tools.kind")}</span>
              <select value={kind} onChange={(e) => { setKind(e.target.value); build.reset(); }}>
                {kinds.map((k) => (
                  <option key={k.value} value={k.value}>{k.label}</option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>{t("web.tools.wiz_ext")}</span>
              <input value={ext} onChange={(e) => { setExt(e.target.value); build.reset(); }} />
            </label>
          </div>
          <div>
            <button disabled={!ref.trim() || build.isPending} onClick={() => void make().catch(() => undefined)}>
              {t("web.tools.wiz_build")}
            </button>
          </div>
          {build.data ? (
            <div className="stack">
              <span className="muted small">{t("web.tools.wiz_command")}</span>
              <pre className="transcript">{build.data.command}</pre>
              <p className="muted small">{t("web.tools.wiz_loaded")}</p>
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

/** Write a command-line tool or an HTTP service as configuration: pick a starting point, edit, check, save. */
function ConnectorEditor({ kinds, roles }: { kinds: { value: string; label: string }[]; roles: string[] }) {
  const t = useT();
  const templates = useToolTemplates();
  const check = useCheckConnector();
  const save = useSaveConnector();
  const [models, setModels] = useState("");
  const [services, setServices] = useState("");
  const [force, setForce] = useState(false);
  const clear = () => {
    check.reset();
    save.reset();
  };
  const start = (name: string) => {
    const row = templates.data?.find((x) => x.name === name);
    if (!row) return;
    setModels(row.models);
    setServices(row.services);
    clear();
  };
  const body = { models, services, force };
  const onSave = async () => {
    save.reset();
    const checked = await check.mutateAsync(body);
    if (checked.ok) save.mutate(body);
  };
  const failure = check.error ?? save.error;
  return (
    <>
    <CliWizard roles={roles} kinds={kinds} onBuilt={(text) => { setModels(text); setServices(""); clear(); }} />
    <section className="stack">
      <h2>{t("web.tools.connector")}</h2>
      <p className="muted small">{t("web.tools.connector_hint")}</p>
      <label className="field">
        <span>{t("web.tools.start_from")}</span>
        <select value="" onChange={(e) => start(e.target.value)}>
          <option value="" />
          {(templates.data ?? []).map((x) => (
            <option key={x.name} value={x.name}>{t(`web.tools.tpl.${x.name}`)}</option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>{t("web.tools.models_toml")}</span>
        <textarea className="mono" rows={14} spellCheck={false} value={models} onChange={(e) => { setModels(e.target.value); clear(); }} />
      </label>
      <label className="field">
        <span>{t("web.tools.services_toml")}</span>
        <textarea className="mono" rows={5} spellCheck={false} value={services} onChange={(e) => { setServices(e.target.value); clear(); }} />
      </label>
      <label className="field check">
        <input type="checkbox" checked={force} onChange={(e) => { setForce(e.target.checked); clear(); }} />
        <span>{t("web.tools.replace")}</span>
      </label>
      <div className="row">
        <button disabled={!models.trim() || check.isPending} onClick={() => check.mutate(body)}>
          {t("web.tools.check")}
        </button>
        <button disabled={!models.trim() || check.isPending || save.isPending} onClick={() => void onSave()}>
          {t("web.tools.save_connector")}
        </button>
      </div>
      <ErrorNote error={failure} />
      {check.data ? (
        <div className={check.data.ok ? "banner banner-ok" : "banner banner-warn"} role="status">
          <strong>{check.data.ok ? t("web.tools.checked_ok", { ref: check.data.ref }) : t("web.tools.checked_bad")}</strong>
          <Issues issues={check.data.issues} />
        </div>
      ) : null}
      {save.data ? (
        <div className="banner banner-ok" role="status">
          <strong>{t("web.tools.saved", { ref: save.data.ref })}</strong>
          <p className="muted small">{save.data.reloaded ? t("web.tools.restart") : t("web.tools.reload_failed", { error: save.data.error })}</p>
        </div>
      ) : null}
    </section>
    </>
  );
}

/** Connect outside software: import a ComfyUI workflow (saved in API format) as a model the pipelines can use. */
export function ToolsPage() {
  const t = useT();
  const meta = useMeta();
  const models = useToolModels();
  const preview = usePreviewComfy();
  const save = useSaveComfy();
  const [workflow, setWorkflow] = useState<Record<string, unknown> | null>(null);
  const [readError, setReadError] = useState(false);
  const [kind, setKind] = useState("image.generate");
  const [name, setName] = useState("");
  const [family, setFamily] = useState("");
  const [heavy, setHeavy] = useState(false);
  const [timeoutSec, setTimeoutSec] = useState(1800);
  const [force, setForce] = useState(false);
  const kinds = (meta.data?.kinds ?? []).filter((k) => !NOT_IMPORTABLE.some((p) => k.value.startsWith(p)));

  const onFile = async (file: File | undefined) => {
    preview.reset();
    save.reset();
    setReadError(false);
    if (!file) return;
    try {
      const parsed: unknown = JSON.parse(await file.text());
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error("shape");
      setWorkflow(parsed as Record<string, unknown>);
      if (!name) setName(file.name.replace(/\.json$/i, "").toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-+/, ""));
    } catch {
      setWorkflow(null);
      setReadError(true);
    }
  };
  const failure = preview.error ?? save.error;
  return (
    <div className="stack">
      <h1>{t("web.tools.title")}</h1>
      <p className="muted">{t("web.tools.subtitle")}</p>

      <section className="stack">
        <h2>{t("web.tools.comfy")}</h2>
        <p className="muted small">{t("web.tools.comfy_hint")}</p>
        <label className="field">
          <span>{t("web.tools.file")}</span>
          <input type="file" accept="application/json,.json" onChange={(e) => void onFile(e.target.files?.[0])} />
        </label>
        {readError ? <div className="error-box" role="alert">{t("web.tools.invalid_json")}</div> : null}
        <label className="field">
          <span>{t("web.tools.kind")}</span>
          <select value={kind} onChange={(e) => { setKind(e.target.value); preview.reset(); }}>
            {kinds.map((k) => (
              <option key={k.value} value={k.value}>{k.label}</option>
            ))}
          </select>
        </label>
        <div>
          <button disabled={!workflow || preview.isPending} onClick={() => workflow && preview.mutate({ workflow, kind })}>
            {t("web.tools.preview")}
          </button>
        </div>
        <ErrorNote error={failure} />
        {detailOf(failure) ? <p className="muted">{detailOf(failure)}</p> : null}
      </section>

      {preview.data ? (
        <section className="stack">
          <h2>{t("web.tools.bound")}</h2>
          {preview.data.warnings.length ? (
            <div className="banner banner-warn" role="status">
              <ul>
                {preview.data.warnings.map((w) => (
                  <li key={w.code}>{w.message}</li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="muted">{t("web.tools.no_warnings")}</p>
          )}
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("web.tools.input")}</th>
                  <th>{t("web.tools.lands_in")}</th>
                  <th>{t("web.tools.own_value")}</th>
                </tr>
              </thead>
              <tbody>
                {preview.data.bindings.map((b) => (
                  <tr key={b.name}>
                    <td className="strong mono">{b.name}</td>
                    <td className="mono">{b.targets.map((x, i) => `${x} (${b.classes[i] ?? ""})`).join(", ")}</td>
                    <td className="muted">{b.value == null ? "" : String(b.value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted small">
            {t("web.tools.output", { node: preview.data.output_node || "-", type: preview.data.output_type.join(", ") })}
          </p>

          <h2>{t("web.tools.save_as")}</h2>
          <div className="form-grid">
            <label className="field">
              <span>{t("web.tools.name")}</span>
              <input value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <label className="field">
              <span>{t("web.tools.family")}</span>
              <input value={family} placeholder={t("web.tools.family_hint")} onChange={(e) => setFamily(e.target.value)} />
            </label>
            <label className="field">
              <span>{t("web.tools.timeout")}</span>
              <input type="number" min={10} value={timeoutSec} onChange={(e) => setTimeoutSec(Number(e.target.value) || 1800)} />
            </label>
            <label className="field check">
              <input type="checkbox" checked={heavy} onChange={(e) => setHeavy(e.target.checked)} />
              <span>{t("web.tools.heavy")}</span>
            </label>
            <label className="field check">
              <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
              <span>{t("web.tools.force")}</span>
            </label>
          </div>
          <div>
            <button
              disabled={!workflow || !name || save.isPending}
              onClick={() => workflow && save.mutate({ workflow, kind, name, ref: name, family, heavy, timeout_sec: timeoutSec, force })}
            >
              {t("web.tools.save")}
            </button>
          </div>
          {save.data ? (
            <div className="banner banner-ok" role="status">
              <strong>{t("web.tools.saved", { ref: save.data.ref })}</strong>
              <p className="muted small">{save.data.reloaded ? t("web.tools.restart") : t("web.tools.reload_failed", { error: save.data.error })}</p>
              <p className="muted small mono">{save.data.workflow}</p>
            </div>
          ) : null}
        </section>
      ) : null}

      <ConnectorEditor kinds={kinds} roles={ROLES} />

      <section className="stack">
        <h2>{t("web.tools.models")}</h2>
        {models.data && models.data.length === 0 ? <p className="empty">{t("web.tools.none")}</p> : null}
        {models.data && models.data.length > 0 ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("web.tools.name")}</th>
                  <th>{t("web.tools.kind")}</th>
                  <th>{t("web.tools.family")}</th>
                  <th>{t("web.tools.provider")}</th>
                  <th>{t("web.tools.template")}</th>
                </tr>
              </thead>
              <tbody>
                {models.data.map((m) => (
                  <tr key={m.ref}>
                    <td className="strong">{m.ref}</td>
                    <td>{t(`media.kind.${m.kind}`)}</td>
                    <td>{m.family}</td>
                    <td className="mono">{m.provider}</td>
                    <td className="muted">{m.workflow ? `${m.workflow} - ${m.mine ? t("web.tools.mine") : t("web.tools.packaged")}` : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </div>
  );
}
