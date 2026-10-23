import { useState } from "react";
import { ApiError } from "../api/client";
import { usePreviewComfy, useSaveComfy, useToolModels } from "../api/tools";
import { useMeta } from "../api/hooks";
import { ErrorNote } from "../components/ErrorNote";
import { useT } from "../i18n";

const NOT_IMPORTABLE = ["text.", "compose.", "train."];

function detailOf(error: unknown): string {
  return error instanceof ApiError && typeof error.detail === "string" ? error.detail : "";
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
              <p className="muted small">{t("web.tools.restart")}</p>
              <p className="muted small mono">{save.data.workflow}</p>
            </div>
          ) : null}
        </section>
      ) : null}

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
