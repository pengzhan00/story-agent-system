import { useState } from "react";
import { useEpisodes, useProject } from "../api/hooks";
import { useDelivery, usePlatforms } from "../api/delivery";
import { ErrorNote } from "../components/ErrorNote";
import { useT } from "../i18n";

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
  const result = make.data;
  const lufs = result?.result["loudness_lufs"];
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
      {result ? (
        <div className="stack">
          <p>{t("web.delivery.done", { size: String(result.result["size_mb"]), lufs: lufs == null ? "-" : String(lufs) })}</p>
          <p>
            <a href={`/api/deliveries/${result.name}`} download>
              {t("web.delivery.download", { name: result.name })}
            </a>
          </p>
          {result.warnings.length > 0 ? (
            <div>
              <h3>{t("web.delivery.warnings")}</h3>
              <ul className="small">
                {result.warnings.map((w) => (
                  <li key={w}>{t(`delivery.warn.${w}`)}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
