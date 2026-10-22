import { useState } from "react";
import { JobId } from "../components/JobId";
import { useEpisodes, useJobActions, useJobs, useProject } from "../api/hooks";
import { useDeliveries, useDelivery, usePlatforms } from "../api/delivery";
import { ErrorNote } from "../components/ErrorNote";
import { ProgressBar } from "../components/ProgressBar";
import { useT } from "../i18n";
import { jobError } from "../lib/jobError";

export function DeliveryPanel({ id }: { id: number }) {
  const t = useT();
  const episodes = useEpisodes(id);
  const platforms = usePlatforms();
  const [episode, setEpisode] = useState(1);
  const [chosen, setChosen] = useState("");
  const [languages, setLanguages] = useState("");
  const [confirm, setConfirm] = useState(false);
  const restricted = (useProject(id).data?.rating ?? "general") !== "general";
  const make = useDelivery(id, episode);
  const list = platforms.data ?? [];
  const platform = list.find((p) => p.name === chosen) ?? list.find((p) => p.default) ?? list[0];
  const jobs = (useJobs(id).data ?? []).filter((j) => j.kind === "compose.deliver");
  const running = jobs.filter((j) => j.status === "queued" || j.status === "running");
  const failed = jobs.filter((j) => j.status !== "queued" && j.status !== "running" && j.status !== "completed").slice(0, 3);
  const done = useDeliveries(id, running.length > 0).data ?? [];
  const { cancel, retry } = useJobActions();
  return (
    <section className="stack">
      <h2>{t("web.delivery.title")}</h2>
      <p className="muted small">{t("web.delivery.hint")}</p>
      <div className="form-grid">
        <label className="field">
          <span>{t("web.delivery.episode")}</span>
          <select value={episode} onChange={(e) => setEpisode(Number(e.target.value))}>
            {(episodes.data ?? [{ id: 1, number: 1 }]).map((e) => (
              <option key={String(e.id)} value={String(e.number)}>
                {String(e.number)}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>{t("web.delivery.platform")}</span>
          <select value={platform?.name ?? ""} onChange={(e) => setChosen(e.target.value)}>
            {list.map((p) => (
              <option key={p.name} value={p.name}>
                {t(p.label_key)}
              </option>
            ))}
          </select>
        </label>
        <label className="field wide">
          <span>{t("web.delivery.languages")}</span>
          <input value={languages} onChange={(e) => setLanguages(e.target.value)} />
        </label>
      </div>
      {platform ? (
        <p className="muted small">
          {t(platform.note_key)} {t("web.delivery.facts", { width: String(platform.width), height: String(platform.height), fps: String(platform.fps), lufs: String(platform.loudnorm_i), reframe: t(`web.delivery.reframe.${platform.reframe}`) })}
        </p>
      ) : null}
      <p className="muted small">{t("web.delivery.languages_hint")}</p>
      {restricted ? (
        <label className="check">
          <input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} />
          <span>{t("web.content.confirm_export")}</span>
        </label>
      ) : null}
      <div>
        <button
          className="primary"
          disabled={!platform || make.isPending}
          onClick={() =>
            platform &&
            make.mutate({
              platform: platform.name,
              languages: languages
                .split(",")
                .map((x) => x.trim())
                .filter(Boolean),
              confirm,
            })
          }
        >
          {make.isPending ? t("web.delivery.working") : t("web.delivery.make")}
        </button>
      </div>
      <ErrorNote error={make.error} />
      {running.map((j) => (
        <div key={j.id} className="job-live">
          <div className="row">
            <span className="small">{t(`delivery.step.${j.progress_msg || "queued"}`)}</span>
            <span className="spacer" />
            <span className="small">{j.status === "running" ? `${Math.round(j.progress * 100)}%` : "0%"}</span>
            <button className="link" disabled={cancel.isPending} onClick={() => cancel.mutate(j.id)}>
              {t("web.jobs.cancel")}
            </button>
          </div>
          <ProgressBar percent={j.status === "running" ? j.progress * 100 : 0} tone={j.status === "queued" ? "queued" : "running"} label={j.progress_msg} />
        </div>
      ))}
      {failed.map((j) => (
        <div key={j.id} className="error-box small">
          <JobId id={j.id} /> {j.error_code ? jobError(t, j).text : t("web.error.generic")}
          {j.error_code ? <p>{jobError(t, j).remediation}</p> : null}
          {(j.retries ?? 0) > 0 && j.retried_by ? (
            <p>
              <span className="chip chip-stale">{t("web.jobs.retried")}</span> <JobId id={j.retried_by} />
            </p>
          ) : (
            <button className="link" disabled={retry.isPending} onClick={() => retry.mutate(j.id)}>
              {t("web.jobs.retry")}
            </button>
          )}
        </div>
      ))}
      {done.length === 0 && running.length === 0 ? <p className="empty">{t("web.delivery.none")}</p> : null}
      {done.map((d) => (
        <div key={d.asset.id} className="stack">
          <p>
            {d.asset.locked ? (
              <span className="muted">{t("web.content.masked")}</span>
            ) : (
              <>
                <strong>{`${d.platform} · ${t("web.delivery.episode")} ${d.episode}`}</strong>{" "}
                {t("web.delivery.done", { size: String(d.result["size_mb"] ?? "-"), lufs: d.result["loudness_lufs"] == null ? "-" : String(d.result["loudness_lufs"]) })}{" "}
                <a href={`/api/assets/${d.asset.id}/file`} download>
                  {t("web.delivery.download", { name: `${d.platform}-ep${d.episode}.zip` })}
                </a>
              </>
            )}
          </p>
          {d.warnings.length > 0 ? (
            <ul className="small">
              {d.warnings.map((w) => (
                <li key={w}>{t(`delivery.warn.${w}`)}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ))}
    </section>
  );
}
