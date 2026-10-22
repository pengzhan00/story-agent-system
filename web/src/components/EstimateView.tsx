import type { Estimate } from "../api/types";
import { useT } from "../i18n";
import { duration } from "../lib/format";

/** The size estimate of a planned work: what it takes to make it, per kind of task, and whether it fits the budget. */
export function EstimateView({ estimate, busy }: { estimate: Estimate | undefined; busy?: boolean }) {
  const t = useT();
  if (!estimate) return <p className="muted small">{t("web.estimate.loading")}</p>;
  const rough = estimate.tasks.some((x) => !x.known);
  return (
    <section className="stack" aria-busy={busy}>
      <h3>{t("web.estimate.title")}</h3>
      <p>{t("web.estimate.summary", { episodes: estimate.episodes, shots: estimate.shots, hours: estimate.hours })}</p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{t("web.estimate.col_task")}</th>
              <th>{t("web.estimate.col_model")}</th>
              <th>{t("web.estimate.col_count")}</th>
              <th>{t("web.estimate.col_each")}</th>
              <th>{t("web.estimate.col_total")}</th>
            </tr>
          </thead>
          <tbody>
            {estimate.tasks.map((x) => (
              <tr key={x.kind}>
                <td>{t(`media.kind.${x.kind}`)}</td>
                <td className="muted small">{x.model_ref}</td>
                <td>{x.count}</td>
                <td>{`${x.known ? "" : "~"}${duration(x.each_sec)}`}</td>
                <td>{`${x.known ? "" : "~"}${duration(x.total_sec)}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rough ? <p className="muted small">{t("web.estimate.rough")}</p> : null}
      {estimate.over_budget ? <p className="error-box">{t("web.estimate.over_budget", { hours: estimate.video_budget_hours ?? 0 })}</p> : null}
    </section>
  );
}
