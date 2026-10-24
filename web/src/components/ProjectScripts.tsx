import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import { usePublishScript, useScriptDoc, useScripts } from "../api/scripts";
import type { ScriptBrief } from "../api/types";
import { ErrorNote } from "./ErrorNote";
import { useT } from "../i18n";
import { Markdown } from "../lib/markdown";

function saveAs(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

/** The one place a script gets its name: what is typed here is what `library:script/<name>` will be. */
function FileUnderName({ s, onDone }: { s: ScriptBrief; onDone: () => void }) {
  const t = useT();
  const publish = usePublishScript(s.project_id, s.episode_no);
  const [name, setName] = useState(`${s.project_name}-${s.episode_no}`);
  const [replace, setReplace] = useState(false);
  const exists = publish.error instanceof ApiError && publish.error.status === 409; // the name is taken
  const ref = publish.data?.ref;
  return (
    <div className="stack">
      <div className="form-grid">
        <label className="field">
          <span>{t("web.library.name")}</span>
          <input type="text" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
        </label>
      </div>
      {exists ? (
        <label className="check">
          <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} />
          <span>{t("web.library.scripts.replace")}</span>
        </label>
      ) : null}
      <div className="row small">
        <button type="button" disabled={publish.isPending || !name.trim() || ref !== undefined} onClick={() => publish.mutate({ name: name.trim(), description: "", replace })}>
          {t("web.library.file_it")}
        </button>
        <button type="button" className="link" onClick={onDone}>
          {t("web.library.scripts.hide")}
        </button>
      </div>
      {ref ? (
        <p className="small" role="status">
          {t("web.library.scripts.filed")} <code>{ref}</code>
        </p>
      ) : null}
      <ErrorNote error={publish.error} />
    </div>
  );
}

function ScriptCard({ s }: { s: ScriptBrief }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [filing, setFiling] = useState(false);
  const [copied, setCopied] = useState(false);
  const doc = useScriptDoc(s.project_id, s.episode_no, open && !s.locked);
  const file = `script-p${s.project_id}-e${s.episode_no}.md`;
  return (
    <li className="card stack">
      <div className="row">
        <strong>{s.project_name}</strong>
        {s.project_kind ? <span className="chip">{s.project_kind}</span> : null}
        <span className="chip">{t("web.library.scripts.episode", { n: s.episode_no })}</span>
        {s.locked ? <span className="chip chip-stale">{t("web.wiki.restricted")}</span> : null}
        <span className="spacer" />
        <span className="muted small">{s.updated_at.replace("T", " ").slice(0, 16)}</span>
      </div>
      {s.locked ? (
        <p className="muted small">{t("web.library.scripts.locked")}</p>
      ) : (
        <>
          <h3>{s.title}</h3>
          {s.synopsis ? <p className="small">{s.synopsis}</p> : null}
        </>
      )}
      <p className="muted small">{t("web.library.scripts.counts", { scenes: s.scenes, shots: s.shots, lines: s.lines })}</p>
      <div className="row small">
        {s.locked ? null : (
          <button type="button" className="link" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? t("web.library.scripts.hide") : t("web.library.scripts.read")}
          </button>
        )}
        {doc.data ? (
          <>
            <button
              type="button"
              className="link"
              onClick={() => {
                void navigator.clipboard?.writeText(doc.data.text).then(
                  () => {
                    setCopied(true);
                    window.setTimeout(() => setCopied(false), 1500);
                  },
                  () => undefined,
                );
              }}
            >
              {copied ? t("web.library.copied") : t("web.library.scripts.copy")}
            </button>
            <button type="button" className="link" onClick={() => saveAs(file, doc.data.text)}>
              {t("web.library.scripts.download")}
            </button>
          </>
        ) : null}
        {s.locked ? null : (
          <button type="button" className="link" aria-expanded={filing} onClick={() => setFiling(!filing)}>
            {t("web.library.scripts.file")}
          </button>
        )}
        <span className="spacer" />
        <Link to={`/projects/${s.project_id}/story`}>{t("web.library.scripts.open_project")}</Link>
      </div>
      {filing ? <FileUnderName s={s} onDone={() => setFiling(false)} /> : null}
      <ErrorNote error={doc.error} />
      {open && doc.data ? <Markdown source={doc.data.text} /> : null}
    </li>
  );
}

/** The scripts of every project (advert and drama alike), readable, searchable, copyable and downloadable. */
export function ProjectScripts({ q, projectId }: { q: string; projectId: number | null }) {
  const t = useT();
  const scripts = useScripts(q, projectId);
  return (
    <>
      <ErrorNote error={scripts.error} />
      {scripts.data && scripts.data.length === 0 ? <p className="empty">{t(q.trim() ? "web.library.scripts.no_match" : "web.library.scripts.empty")}</p> : null}
      <ul className="grid">{scripts.data?.map((s) => <ScriptCard key={`${s.project_id}/${s.episode_no}`} s={s} />)}</ul>
    </>
  );
}
