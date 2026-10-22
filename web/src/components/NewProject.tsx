import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useContentStatus } from "../api/content";
import { useCreateProject, useEstimate, useMeta } from "../api/hooks";
import type { BriefAnswers } from "../api/types";
import { useI18n } from "../i18n";
import { useDebounced } from "../lib/debounce";
import { ErrorNote } from "./ErrorNote";
import { EstimateView } from "./EstimateView";

const num = (v: string): number | undefined => (v.trim() === "" || Number.isNaN(Number(v)) ? undefined : Number(v));

/** The creation wizard: an idea, the kind of work (it recommends the pipeline plan), the structure and length, and what
 *  that costs - decided once, up front. Everything it offers comes from the server's configuration. */
export function NewProject() {
  const { t, locale } = useI18n();
  const nav = useNavigate();
  const meta = useMeta().data;
  const create = useCreateProject();
  const [name, setName] = useState("");
  const [premise, setPremise] = useState("");
  const [kindName, setKindName] = useState("");
  const [plan, setPlan] = useState("");
  const [seasons, setSeasons] = useState("");
  const [episodes, setEpisodes] = useState("");
  const [episodeSec, setEpisodeSec] = useState("");
  const [audience, setAudience] = useState("");
  const [platform, setPlatform] = useState("");
  const [rating, setRating] = useState("");
  const ratings = useContentStatus().data?.ratings ?? [];

  const kinds = meta?.work_kinds ?? [];
  const kind = kinds.find((k) => k.name === (kindName || meta?.default_work_kind));
  const plans = meta?.pipelines ?? [];
  const chosenPlan = plan || kind?.pipeline || "";
  const planInfo = plans.find((p) => p.name === chosenPlan);
  const asks = (key: string) => kind?.ask.includes(key) ?? false;
  const brief: BriefAnswers = {
    seasons: asks("seasons") ? num(seasons) : undefined,
    episodes_per_season: asks("episodes") ? num(episodes) : undefined,
    episode_sec: asks("episode_sec") ? num(episodeSec) : undefined,
    audience: asks("audience") ? audience.trim() : "",
    platform: asks("platform") ? platform.trim() : "",
  };
  // debounce on the serialised answers: a fresh object every render would restart the timer for ever
  const asked = useDebounced(JSON.stringify({ kind: kind?.name ?? "", pipeline: plan, brief }));
  const request = JSON.parse(asked) as { kind: string; pipeline: string; brief: BriefAnswers };
  const estimate = useEstimate(request, !!kind);

  function pick(name: string) {
    setKindName(name);
    setPlan("");                                  // the new kind recommends its own plan
    setSeasons("");
    setEpisodes("");
    setEpisodeSec("");
  }
  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate(
      { name: name.trim(), description: premise.trim(), content_language: locale, kind: kind?.name ?? "", pipeline: plan, brief, rating },
      { onSuccess: (p) => nav(`/projects/${p.id}`) },
    );
  }

  return (
    <form className="card stack" onSubmit={submit}>
      <h2>{t("web.projects.new")}</h2>
      <label className="field">
        <span>{t("web.projects.name")}</span>
        <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={200} />
      </label>
      <label className="field">
        <span>{t("web.projects.premise")}</span>
        <textarea value={premise} onChange={(e) => setPremise(e.target.value)} rows={3} placeholder={t("web.projects.premise_hint")} />
      </label>
      <fieldset className="stack">
        <legend>{t("web.projects.kind")}</legend>
        <div className="form-grid">
          {kinds.map((k) => (
            <label key={k.name} className="field check">
              <input type="radio" name="kind" checked={kind?.name === k.name} onChange={() => pick(k.name)} />
              <span>
                <strong>{k.label}</strong>
                <span className="muted small"> {k.note}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      <label className="field">
        <span>{t("web.plan.pipeline")}</span>
        <select value={chosenPlan} onChange={(e) => setPlan(e.target.value === kind?.pipeline ? "" : e.target.value)}>
          {plans.map((p) => (
            <option key={p.name} value={p.name}>
              {p.name === kind?.pipeline ? `${p.label} · ${t("web.plan.recommended")}` : p.label}
            </option>
          ))}
        </select>
        {planInfo ? (
          <span className="muted small">
            {`${planInfo.note} ${t("web.plan.frame", { w: planInfo.width, h: planInfo.height, tier: t(`media.tier.${planInfo.tier}`) })}`}
          </span>
        ) : null}
        <span className="muted small">{t("web.plan.pipeline_hint")}</span>
      </label>
      {ratings.length > 1 ? (
        <label className="field">
          <span>{t("web.content.rating")}</span>
          <select value={rating} onChange={(e) => setRating(e.target.value)}>
            {ratings.map((r) => (
              <option key={r.name} value={r.name}>
                {t(r.label_key)}
              </option>
            ))}
          </select>
          <span className="muted small">{t("web.content.rating_hint")}</span>
        </label>
      ) : null}
      <div className="form-grid">
        {asks("seasons") ? (
          <label className="field">
            <span>{t("web.plan.seasons")}</span>
            <input type="number" min={1} value={seasons} placeholder="1" onChange={(e) => setSeasons(e.target.value)} />
          </label>
        ) : null}
        {asks("episodes") && kind ? (
          <label className="field">
            <span>{t("web.plan.episodes")}</span>
            <input type="number" min={kind.episodes.min} max={kind.episodes.max} value={episodes} placeholder={String(kind.episodes.default)} onChange={(e) => setEpisodes(e.target.value)} />
            <span className="muted small">{t("web.plan.range", { lo: kind.episodes.min, hi: kind.episodes.max })}</span>
          </label>
        ) : null}
        {asks("episode_sec") && kind ? (
          <label className="field">
            <span>{t("web.plan.episode_sec")}</span>
            <input type="number" min={kind.episode_sec.min} max={kind.episode_sec.max} value={episodeSec} placeholder={String(kind.episode_sec.default)} onChange={(e) => setEpisodeSec(e.target.value)} />
            <span className="muted small">{t("web.plan.range", { lo: kind.episode_sec.min, hi: kind.episode_sec.max })}</span>
          </label>
        ) : null}
        {asks("audience") ? (
          <label className="field">
            <span>{t("web.plan.audience")}</span>
            <input value={audience} maxLength={200} onChange={(e) => setAudience(e.target.value)} />
          </label>
        ) : null}
        {asks("platform") ? (
          <label className="field">
            <span>{t("web.plan.platform")}</span>
            <input value={platform} maxLength={200} onChange={(e) => setPlatform(e.target.value)} />
          </label>
        ) : null}
      </div>
      <ErrorNote error={estimate.error} />
      <EstimateView estimate={estimate.data} busy={estimate.isFetching} />
      <div className="row">
        <button className="primary" type="submit" disabled={!name.trim() || !kind || create.isPending}>
          {t("web.projects.create")}
        </button>
      </div>
      <ErrorNote error={create.error} />
    </form>
  );
}
