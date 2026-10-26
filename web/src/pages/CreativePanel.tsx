import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMeta, useStory } from "../api/hooks";
import { useReview, useSpecActions, useSpecAssets, useSpecJobs, useSpecs } from "../api/specs";
import type { Spec } from "../api/types";
import { Candidates } from "../components/Candidates";
import { ErrorNote } from "../components/ErrorNote";
import { SpecEditor } from "../components/SpecEditor";
import { subjectLabel, type StoryIndex } from "../lib/subjects";
import { useT } from "../i18n";

const key = (s: Spec) => `${s.subject_type}|${s.subject_id}|${s.slot}`;

export function CreativePanel({ id }: { id: number }) {
  const t = useT();
  const meta = useMeta();
  const specs = useSpecs(id);
  const review = useReview(id);
  const characters = useStory(id, "characters");
  const locations = useStory(id, "locations");
  const music = useStory(id, "music");
  const sfx = useStory(id, "sfx");
  const shots = useStory(id, "shots");
  const [params] = useSearchParams();
  const [selected, setSelected] = useState<string | null>(null);
  const wanted = params.get("spec");               // a link from a failed job: open that spec
  const [filter, setFilter] = useState("");

  const story: StoryIndex = useMemo(
    () => ({ characters: characters.data ?? [], locations: locations.data ?? [], music: music.data ?? [], sfx: sfx.data ?? [], shots: shots.data ?? [] }),
    [characters.data, locations.data, music.data, sfx.data, shots.data],
  );
  const needs = useMemo(() => new Set((review.data ?? []).map((r) => key(r.spec))), [review.data]);
  const groups = useMemo(() => {
    const m = new Map<string, Spec[]>();
    for (const s of specs.data ?? []) {
      if (filter && s.subject_type !== filter) continue;
      m.set(s.subject_type, [...(m.get(s.subject_type) ?? []), s]);
    }
    return [...m.entries()];
  }, [specs.data, filter]);
  const current = (specs.data ?? []).find((s) => (selected ? key(s) === selected : s.id === wanted)) ?? null;

  return (
    <div className="split">
      <aside className="stack">
        <label className="field">
          <span>{t("web.creative.filter")}</span>
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">{t("web.creative.all")}</option>
            {meta.data?.subject_types.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <ErrorNote error={specs.error} />
        {specs.data && specs.data.length === 0 ? <p className="empty">{t("web.creative.empty")}</p> : null}
        {groups.map(([type, list]) => (
          <section key={type}>
            <h3 className="group">{t(`spec.subject.${type}`)}</h3>
            <ul className="pick">
              {list.map((s) => (
                <li key={key(s)}>
                  <button className={key(s) === selected ? "pick-item active" : "pick-item"} onClick={() => setSelected(key(s))}>
                    <span className="clamp1">{subjectLabel(s.subject_type, s.subject_id, story, t)}</span>
                    <span className="badges">
                      {needs.has(key(s)) ? <span className="dot dot-warn" title={t("web.creative.needs_review")} /> : null}
                      {s.locked ? <span className="small" title={t("web.creative.locked")}>{t("web.creative.lock_mark")}</span> : null}
                      <span className="small muted">{t("web.creative.v", { v: s.version })}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </aside>
      <section>{current ? <SpecDetail pid={id} spec={current} review={needs.has(key(current))} label={subjectLabel(current.subject_type, current.subject_id, story, t)} /> : <p className="empty">{t("web.creative.pick")}</p>}</section>
    </div>
  );
}

function SpecDetail({ pid, spec, review, label }: { pid: number; spec: Spec; review: boolean; label: string }) {
  const t = useT();
  const meta = useMeta();
  const act = useSpecActions(pid, spec);
  const assets = useSpecAssets(pid, spec);
  const jobs = useSpecJobs(spec);
  const [n, setN] = useState(2);
  const [tier, setTier] = useState("draft");
  const [vary, setVary] = useState(true);
  const busy = (jobs.data ?? []).some((j) => j.status === "queued" || j.status === "running");
  return (
    <div className="stack">
      <header>
        <h2>{label}</h2>
        <p className="muted small">
          {t(`spec.subject.${spec.subject_type}`)} · {t(`media.kind.${spec.kind}`)}
        </p>
      </header>
      <div className="card stack">
        <div className="row">
          <label className="field">
            <span>{t("web.creative.how_many")}</span>
            <input type="number" min={1} max={8} value={n} onChange={(e) => setN(Math.max(1, Math.min(8, Number(e.target.value) || 1)))} />
          </label>
          <label className="field">
            <span>{t("web.creative.quality")}</span>
            <select value={tier} onChange={(e) => setTier(e.target.value)}>
              {meta.data?.tiers.map((q) => (
                <option key={q.value} value={q.value}>
                  {q.label}
                </option>
              ))}
            </select>
          </label>
          <label className="check">
            <input type="checkbox" checked={vary} onChange={(e) => setVary(e.target.checked)} />
            <span>{t("web.creative.vary_seed")}</span>
          </label>
          <span className="spacer" />
          <button className="primary" disabled={act.regenerate.isPending} onClick={() => act.regenerate.mutate({ n_candidates: n, tier, vary_seed: vary })}>
            {busy ? t("web.creative.make_more") : t("web.creative.generate")}
          </button>
        </div>
        <ErrorNote error={act.regenerate.error ?? act.adopt.error ?? act.trash.error ?? act.purge.error} />
        <Candidates
          spec={spec}
          assets={assets.data ?? []}
          jobs={jobs.data ?? []}
          onAdopt={(a) => act.adopt.mutate(a)}
          adopting={act.adopt.isPending}
          onTrash={(a) => act.trash.mutate(a)}
          onPurge={(a) => act.purge.mutate(a)}
          onPurgeVersion={(a) => act.purgeVersion.mutate(a)}
        />
      </div>
      <SpecEditor pid={pid} spec={spec} review={review} onAcknowledge={() => act.acknowledge.mutate()} />
    </div>
  );
}
