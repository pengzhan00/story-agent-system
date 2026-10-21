import { useState } from "react";
import { useEpisodes, useSeasons, useStartRun, useUpdateSeason } from "../api/hooks";
import { useT } from "../i18n";
import { ErrorNote } from "./ErrorNote";

/** A season: how many episodes it plans, its outline (hook and cliffhanger per episode) and the button that writes it. */
export function SeasonCard({ id, busy }: { id: number; busy: boolean }) {
  const t = useT();
  const seasons = useSeasons(id);
  const episodes = useEpisodes(id);
  const update = useUpdateSeason(id);
  const start = useStartRun(id);
  const [season, setSeason] = useState(1);
  const [draft, setDraft] = useState<string | null>(null);
  const list = seasons.data ?? [];
  const current = list.find((s) => s.number === season);
  const planned = Number(current?.planned_episodes ?? 0);
  const shown = draft ?? (planned ? String(planned) : "");
  const mine = (episodes.data ?? []).filter((e) => e.season === season);
  const numbers = list.map((s) => Number(s.number));
  return (
    <section className="card stack">
      <div className="form-grid">
        <label className="field">
          <span>{t("web.season.season")}</span>
          <select value={season} onChange={(e) => { setSeason(Number(e.target.value)); setDraft(null); }}>
            {[...numbers, Math.max(0, ...numbers) + 1].map((n) => (
              <option key={n} value={n}>
                {t("web.season.name", { n })}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>{t("web.season.planned")}</span>
          <input type="number" min={1} value={shown} onChange={(e) => setDraft(e.target.value)} />
        </label>
      </div>
      {current?.summary ? <p>{String(current.summary)}</p> : null}
      <div className="row">
        <button
          disabled={update.isPending || draft === null || Number(draft) < 1}
          onClick={() => update.mutate({ number: season, body: { planned_episodes: Number(draft) } }, { onSuccess: () => setDraft(null) })}
        >
          {t("web.save")}
        </button>
        <button
          className="primary"
          disabled={busy || start.isPending || planned < 1 || draft !== null}
          onClick={() => start.mutate({ stages: ["outline"], params: { season, force: mine.some((e) => e.hook) } })}
        >
          {mine.some((e) => e.hook) ? t("web.season.rewrite") : t("web.season.write")}
        </button>
      </div>
      <p className="muted small">{t("web.season.hint")}</p>
      <ol className="plain">
        {mine.map((e) => (
          <li key={String(e.id)}>
            <strong>{t("web.season.episode_label", { season, n: Number(e.number_in_season) })}</strong> {String(e.title)}
            {e.hook ? <span className="muted">{` · ${String(e.hook)} → ${String(e.cliffhanger)}`}</span> : null}
          </li>
        ))}
      </ol>
      <ErrorNote error={update.error ?? start.error} />
    </section>
  );
}
