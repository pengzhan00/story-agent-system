import { useState } from "react";
import { useGrammar, useGrammarFix, type GrammarViolation } from "../api/grammar";
import { useEpisodes } from "../api/hooks";
import { ErrorNote } from "../components/ErrorNote";
import { useT } from "../i18n";

function Row({ v, onFix, busy }: { v: GrammarViolation; onFix: () => void; busy: boolean }) {
  const t = useT();
  const fix = v.fix as { type: string; value: string } | null;
  return (
    <tr>
      <td>
        <span className={`chip ${v.severity === "error" ? "chip-failed" : v.severity === "warning" ? "chip-stale" : ""}`}>{t(`web.grammar.severity.${v.severity}`)}</span>
      </td>
      <td className="mono">{`${v.episode}.${v.act}.${v.scene}.${v.shot}`}</td>
      <td>
        {t(`grammar.msg.${v.rule}`, Object.fromEntries(Object.entries(v.params).map(([k, x]) => [k, String(x)])))}
        {fix ? <div className="muted small">{t("web.grammar.fix_hint", { fix: t(`grammar.fix.${fix.type}`, { value: fix.value }) })}</div> : null}
      </td>
      <td>
        {v.fixable ? (
          <button disabled={busy} onClick={onFix}>
            {t("web.grammar.fix")}
          </button>
        ) : null}
      </td>
    </tr>
  );
}

export function GrammarPanel({ id }: { id: number }) {
  const t = useT();
  const episodes = useEpisodes(id);
  const [episode, setEpisode] = useState<number | null>(null);
  const report = useGrammar(id, episode);
  const fix = useGrammarFix(id);
  const data = report.data;
  const fixable = data?.violations.filter((v) => v.fixable) ?? [];
  const left = fix.data?.left.length ?? 0;
  return (
    <section className="stack">
      <h2>{t("web.grammar.title")}</h2>
      <p className="muted small">{t("web.grammar.hint")}</p>
      <div className="form-grid">
        <label className="field">
          <span>{t("web.grammar.episode")}</span>
          <select value={episode ?? ""} onChange={(e) => setEpisode(e.target.value === "" ? null : Number(e.target.value))}>
            <option value="">{t("web.grammar.all_episodes")}</option>
            {(episodes.data ?? []).map((e) => (
              <option key={String(e.id)} value={String(e.number)}>
                {String(e.number)}
              </option>
            ))}
          </select>
        </label>
        <div className="wide">
          <button className="primary" disabled={fix.isPending || fixable.length === 0} onClick={() => fix.mutate({ episode })}>
            {t("web.grammar.fix_all", { n: String(fixable.length) })}
          </button>
        </div>
      </div>
      <ErrorNote error={report.error ?? fix.error} />
      {data ? (
        <p className="muted">
          {t("web.grammar.summary", {
            shots: String(data.shots),
            error: String(data.counts.error),
            warning: String(data.counts.warning),
            info: String(data.counts.info),
            unclassified: String(data.unclassified),
          })}
        </p>
      ) : null}
      {fix.data ? <p className="muted small">{t("web.grammar.fixed", { n: String(fix.data.fixed.length), left: String(left) })}</p> : null}
      {data && data.violations.length === 0 ? <p className="empty">{t("web.grammar.none")}</p> : null}
      {data && data.violations.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th />
                <th>{t("web.grammar.position")}</th>
                <th>{t("web.grammar.problem")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.violations.map((v) => (
                <Row key={`${v.rule}-${v.shot_id}`} v={v} busy={fix.isPending} onFix={() => fix.mutate({ episode, rules: [v.rule], shot_ids: [v.shot_id] })} />
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
