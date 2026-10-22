import { useRef, useState } from "react";
import { useProject } from "../api/hooks";
import { usePack, type PackPolicy, type PackReport, type PackScope } from "../api/pack";
import { ErrorNote } from "../components/ErrorNote";
import { useT } from "../i18n";
import { FcpPanel } from "./FcpPanel";

function Counts({ counts }: { counts: Record<string, number> }) {
  const t = useT();
  const entries = Object.entries(counts);
  if (entries.length === 0) return <span className="muted">-</span>;
  return (
    <span>
      {entries.map(([k, v]) => `${t(`web.pack.count.${k}`)} ${v}`).join(" · ")}
    </span>
  );
}

function Report({ report }: { report: PackReport }) {
  const t = useT();
  const rows: [string, Record<string, number>][] = [
    ...Object.entries(report.story).map(([k, v]): [string, Record<string, number>] => [k, v]),
    ["specs", report.specs],
    ["assets", report.assets],
  ];
  return (
    <section className="stack">
      <h3>{t(report.dry_run ? "web.pack.report_preview" : "web.pack.report_done")}</h3>
      <div className="table-wrap">
        <table>
          <tbody>
            {rows
              .filter(([, v]) => Object.keys(v).length > 0)
              .map(([name, v]) => (
                <tr key={name}>
                  <td className="strong">{t(`web.pack.section.${name}`)}</td>
                  <td>
                    <Counts counts={v} />
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
      {report.unassigned.length > 0 ? <p className="muted small">{t("web.pack.unassigned", { names: report.unassigned.join(", ") })}</p> : null}
      {report.warnings.length > 0 ? (
        <ul className="muted small">
          {report.warnings.map((w, i) => (
            <li key={i}>{t(`pack.warn.${String(w.code)}`)}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

export function PackPanel({ id }: { id: number }) {
  const t = useT();
  const pack = usePack(id);
  const [scope, setScope] = useState<PackScope>("all");
  const [history, setHistory] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const restricted = (useProject(id).data?.rating ?? "general") !== "general";
  const [policy, setPolicy] = useState<PackPolicy>("skip");
  const file = useRef<HTMLInputElement>(null);
  const [picked, setPicked] = useState<File | null>(null);
  const exported = pack.exportPack.data;
  const run = (dryRun: boolean) => {
    if (picked) pack.importPack.mutate({ file: picked, policy, dryRun });
  };
  return (
    <div className="stack">
      <section className="stack">
        <h2>{t("web.pack.export")}</h2>
        <p className="muted small">{t("web.pack.export_hint")}</p>
        <div className="form-grid">
          <label className="field">
            <span>{t("web.pack.scope")}</span>
            <select value={scope} onChange={(e) => setScope(e.target.value as PackScope)}>
              {(["all", "assets", "story"] as const).map((s) => (
                <option key={s} value={s}>
                  {t(`web.pack.scope.${s}`)}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>{t("web.pack.history")}</span>
            <input type="checkbox" checked={history} onChange={(e) => setHistory(e.target.checked)} />
          </label>
          {restricted ? (
            <label className="check wide">
              <input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} />
              <span>{t("web.content.confirm_export")}</span>
            </label>
          ) : null}
          <div className="wide">
            <button className="primary" disabled={pack.exportPack.isPending} onClick={() => pack.exportPack.mutate({ scope, history, confirm })}>
              {t("web.pack.make")}
            </button>
          </div>
        </div>
        <ErrorNote error={pack.exportPack.error} />
        {exported ? (
          <p>
            <a href={`/api/packs/${exported.name}`} download>
              {t("web.pack.download", { name: exported.name })}
            </a>{" "}
            <span className="muted small">{t("web.pack.size", { mb: (exported.bytes / 1048576).toFixed(1) })}</span>
          </p>
        ) : null}
      </section>
      <section className="stack">
        <h2>{t("web.pack.import")}</h2>
        <p className="muted small">{t("web.pack.import_hint")}</p>
        <div className="form-grid">
          <label className="field wide">
            <span>{t("web.pack.file")}</span>
            <input ref={file} type="file" accept=".zip" onChange={(e) => setPicked(e.target.files?.[0] ?? null)} />
          </label>
          <label className="field">
            <span>{t("web.pack.conflict")}</span>
            <select value={policy} onChange={(e) => setPolicy(e.target.value as PackPolicy)}>
              <option value="skip">{t("web.pack.conflict.skip")}</option>
              <option value="new_version">{t("web.pack.conflict.new_version")}</option>
            </select>
          </label>
          <div className="wide">
            <button disabled={!picked || pack.importPack.isPending} onClick={() => run(true)}>
              {t("web.pack.preview")}
            </button>{" "}
            <button className="primary" disabled={!picked || pack.importPack.isPending || !pack.importPack.data?.dry_run} onClick={() => run(false)}>
              {t("web.pack.run")}
            </button>
          </div>
        </div>
        <ErrorNote error={pack.importPack.error} />
        {pack.importPack.data ? <Report report={pack.importPack.data} /> : null}
      </section>
      <FcpPanel id={id} />
    </div>
  );
}
