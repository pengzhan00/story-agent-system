import { useSeries } from "../api/hooks";
import { ErrorNote } from "../components/ErrorNote";
import { ProgressBar } from "../components/ProgressBar";
import { StatusChip } from "../components/StatusChip";
import { useT } from "../i18n";
import { duration } from "../lib/format";

const STAGES = ["script", "shotplan", "specs", "produce", "compose"];

/** Every season and episode at a glance: stage states, shots, keyframes, clips, the finished film. */
export function SeriesPanel({ id }: { id: number }) {
  const t = useT();
  const board = useSeries(id);
  const totals = board.data?.totals;
  return (
    <div className="stack">
      <ErrorNote error={board.error} />
      {totals ? (
        <section className="card stack">
          <div className="row">
            <strong>{t("web.series.filmed", { done: totals.filmed, total: totals.planned })}</strong>
            <span className="muted">{t("web.series.counts", { scripted: totals.scripted, shots: totals.shots, keyframes: totals.keyframes, videos: totals.videos })}</span>
          </div>
          <ProgressBar percent={totals.planned ? (100 * totals.filmed) / totals.planned : 0} tone="completed" label={t("web.series.filmed", { done: totals.filmed, total: totals.planned })} />
        </section>
      ) : null}
      {(board.data?.seasons ?? []).map((season) => (
        <section key={season.number} className="card stack">
          <h2>{season.title ? `${t("web.season.name", { n: season.number })} · ${season.title}` : t("web.season.name", { n: season.number })}</h2>
          {season.summary ? <p className="muted">{season.summary}</p> : null}
          <ProgressBar
            percent={season.episodes.length ? (100 * season.filmed) / season.episodes.length : 0}
            tone="completed"
            label={t("web.series.filmed", { done: season.filmed, total: season.episodes.length })}
          />
          {season.episodes.length === 0 ? <p className="muted">{t("web.series.empty")}</p> : null}
          <ul className="plain">
            {season.episodes.map((e) => (
              <li key={e.number} className="stack">
                <div className="row">
                  <strong>{t("web.season.episode_label", { season: season.number, n: e.number_in_season })}</strong>
                  <span>{e.exists ? e.title : t("web.series.not_started")}</span>
                  <span className="spacer" />
                  {e.film_seconds != null ? <span className="chip chip-completed">{t("web.series.film", { time: duration(e.film_seconds) })}</span> : null}
                </div>
                {e.exists ? (
                  <div className="row small">
                    {STAGES.map((s) => (
                      <span key={s} className="row">
                        <span className="muted">{t(`workflow.stage.${s}`)}</span>
                        <StatusChip status={e.stages[s] ?? "pending"} />
                      </span>
                    ))}
                    <span className="muted">{t("web.series.media", { shots: e.shots, keyframes: e.keyframes, videos: e.videos })}</span>
                    {e.outdated > 0 ? <span className="chip chip-stale">{t("web.series.outdated", { n: e.outdated })}</span> : null}
                  </div>
                ) : null}
                {e.hook ? <p className="muted small">{`${e.hook} → ${e.cliffhanger}`}</p> : null}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
