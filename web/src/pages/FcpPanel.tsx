import { useState } from "react";
import { useEpisodes } from "../api/hooks";
import { useInterchange } from "../api/interchange";
import { ErrorNote } from "../components/ErrorNote";
import { useT } from "../i18n";

export function FcpPanel({ id }: { id: number }) {
  const t = useT();
  const episodes = useEpisodes(id);
  const [episode, setEpisode] = useState(1);
  const [note, setNote] = useState("");
  const [xml, setXml] = useState<File | null>(null);
  const [film, setFilm] = useState<File | null>(null);
  const fcp = useInterchange(id, episode);
  const diff = fcp.diff.data;
  return (
    <section className="stack">
      <h2>{t("web.fcp.title")}</h2>
      <p className="muted small">{t("web.fcp.hint")}</p>
      <label className="field">
        <span>{t("web.fcp.episode")}</span>
        <select value={episode} onChange={(e) => setEpisode(Number(e.target.value))}>
          {(episodes.data ?? [{ id: 1, number: 1 }]).map((e) => (
            <option key={String(e.id)} value={String(e.number)}>
              {String(e.number)}
            </option>
          ))}
        </select>
      </label>
      <h3>{t("web.fcp.out")}</h3>
      <p>
        <a href={fcp.downloadUrl} download>
          {t("web.fcp.download")}
        </a>
      </p>
      <h3>{t("web.fcp.compare")}</h3>
      <div className="form-grid">
        <label className="field wide">
          <span>{t("web.fcp.xml_file")}</span>
          <input type="file" accept=".fcpxml,.xml" onChange={(e) => setXml(e.target.files?.[0] ?? null)} />
        </label>
        <div className="wide">
          <button disabled={!xml || fcp.diff.isPending} onClick={() => xml && fcp.diff.mutate(xml)}>
            {t("web.fcp.compare_run")}
          </button>
        </div>
      </div>
      <ErrorNote error={fcp.diff.error} />
      {diff ? (
        <div className="stack">
          <p className="muted">{t("web.fcp.diff_head", { original: String(diff.original_sec), edited: String(diff.edited_sec), changed: String(diff.changed) })}</p>
          {diff.changed === 0 ? <p className="empty">{t("web.fcp.unchanged")}</p> : null}
          <ul className="small">
            {diff.shots
              .filter((s) => s.status !== "kept")
              .map((s) => (
                <li key={s.shot_id}>{t("web.fcp.diff_shot", { shot: String(s.shot_id), status: t(`interchange.status.${s.status}`), old: String(s.duration), new: String(s.new_duration) })}</li>
              ))}
            {diff.added.map((a, i) => (
              <li key={i}>{t("web.fcp.diff_added", { name: a.name || a.media, seconds: String(a.duration) })}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <h3>{t("web.fcp.in")}</h3>
      <div className="form-grid">
        <label className="field wide">
          <span>{t("web.fcp.film_file")}</span>
          <input type="file" accept=".mp4,.mov,.m4v" onChange={(e) => setFilm(e.target.files?.[0] ?? null)} />
        </label>
        <label className="field wide">
          <span>{t("web.fcp.note")}</span>
          <input value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
        <div className="wide">
          <button className="primary" disabled={!film || fcp.final.isPending} onClick={() => film && fcp.final.mutate({ file: film, note })}>
            {t("web.fcp.import")}
          </button>
        </div>
      </div>
      <ErrorNote error={fcp.final.error} />
      {fcp.final.data ? <p className="muted">{t("web.fcp.imported", { version: String(fcp.final.data.version) })}</p> : null}
    </section>
  );
}
