import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useJobActions, useMeta } from "../api/hooks";
import type { Asset, Job, WorkshopField, WorkshopHistoryItem, WorkshopKind, WorkshopModule } from "../api/types";
import { type Step, useWorkshop, useWorkshopActions, useWorkshopHistory, useWorkshopParams } from "../api/workshop";
import { AddParam } from "../components/AddParam";
import { AssetPreview } from "../components/Candidates";
import { ErrorNote } from "../components/ErrorNote";
import { MakeProject } from "../components/MakeProject";
import { ProgressBar } from "../components/ProgressBar";
import { StatusChip } from "../components/StatusChip";
import { ValueEditor } from "../components/ValueEditor";
import { ValueView } from "../components/ValueView";
import { WorkshopAssist } from "../components/WorkshopAssist";
import { useT } from "../i18n";
import { LicenseAlert } from "../components/LicenseAlert";
import { PromptAssist } from "../components/PromptAssist";
import { jobError } from "../lib/jobError";
import { mediaOfAsset, targetsFor } from "../lib/assets";
import { duration } from "../lib/format";

type Values = Record<string, unknown>;
const PIPELINE = "pipeline";
const PREV = "@prev";

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** The form values a finished job was started with (for "run again"). */
function valuesFromInputs(kind: WorkshopKind, inputs: Record<string, unknown>): Values {
  const out: Values = {};
  const used: Record<string, number> = {};
  for (const f of kind.inputs) {
    if (f.type === "medialist") {
      const list = inputs[f.target.replace("[]", "")];
      if (Array.isArray(list)) out[f.name] = list;
      continue;
    }
    const parts = f.target.replace("[]", "").split(".");
    let v: unknown = inputs;
    for (const p of parts) v = (v as Record<string, unknown> | undefined)?.[p];
    if (f.target.endsWith("[]")) {
      const list = Array.isArray(v) ? v : [];
      const i = used[f.target] ?? 0;
      used[f.target] = i + 1;
      v = list[i];
    }
    if (v !== undefined && v !== null) out[f.name] = v;
  }
  return out;
}

const acceptOf = (media: string) =>
  media === "text" ? "text/*,.md,.markdown" : media === "lora" ? ".safetensors" : `${media}/*`;

/** Several files of one media type (the photos of a LoRA training set): upload, pick from earlier results, remove. */
function MediaList({ field, value, onChange, recents }: { field: WorkshopField; value: string[]; onChange: (v: string[]) => void; recents: Asset[] }) {
  const t = useT();
  const { upload } = useWorkshopActions();
  const file = useRef<HTMLInputElement>(null);
  const choices = recents.filter((a) => mediaOfAsset(a) === field.media && !value.includes(`asset:${a.id}`));
  const add = (refs: string[]) => onChange([...value, ...refs]);
  return (
    <div className="stack">
      <div className="row">
        <button type="button" onClick={() => file.current?.click()} disabled={upload.isPending}>
          {t("web.workshop.upload_many")}
        </button>
        <input
          ref={file}
          type="file"
          multiple
          hidden
          accept={acceptOf(field.media)}
          onChange={async (e) => {
            const files = Array.from(e.target.files ?? []);
            e.target.value = "";
            const refs: string[] = [];
            for (const f of files) refs.push(`asset:${(await upload.mutateAsync(f)).id}`);
            add(refs);
          }}
        />
        {choices.length > 0 ? (
          <select value="" aria-label={t("web.workshop.history")} onChange={(e) => e.target.value && add([`asset:${e.target.value}`])}>
            <option value="">{t("web.workshop.history")}</option>
            {choices.slice(0, 20).map((a) => (
              <option key={a.id} value={a.id}>
                {`${a.created_at.replace("T", " ").slice(5, 16)} ${a.kind}`}
              </option>
            ))}
          </select>
        ) : null}
        <span className="muted small">{t("web.workshop.count_selected", { n: value.length })}</span>
      </div>
      <div className="thumbs">
        {value.map((ref, i) => (
          <figure key={`${ref}-${i}`} className="thumb">
            {field.media === "image" && ref.startsWith("asset:") ? <img src={`/api/assets/${ref.slice(6)}/file`} alt="" loading="lazy" /> : <code>{ref}</code>}
            <button type="button" className="link" onClick={() => onChange(value.filter((_, j) => j !== i))}>
              {t("web.workshop.remove")}
            </button>
          </figure>
        ))}
      </div>
      <ErrorNote error={upload.error} />
    </div>
  );
}

type Shot = Record<string, string>;
const SHOT_PICKS = [
  { name: "video", media: "video" },
  { name: "image", media: "image" },
  { name: "speech", media: "audio" },
] as const;

/** An episode put together by hand: one row per shot with its picture, spoken line and the voice of that line. */
function ShotList({ value, onChange, recents }: { value: Shot[]; onChange: (v: Shot[]) => void; recents: Asset[] }) {
  const t = useT();
  const set = (i: number, patch: Shot) => onChange(value.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const move = (i: number, d: number) => {
    const next = [...value];
    const [row] = next.splice(i, 1);
    if (row) next.splice(i + d, 0, row);
    onChange(next);
  };
  return (
    <div className="stack">
      {value.map((row, i) => (
        <div key={i} className="card stack">
          <div className="row">
            <strong>{t("web.workshop.shot_n", { n: i + 1 })}</strong>
            <span className="spacer" />
            <button type="button" className="link" disabled={i === 0} onClick={() => move(i, -1)}>
              {t("web.workshop.move_up")}
            </button>
            <button type="button" className="link" disabled={i === value.length - 1} onClick={() => move(i, 1)}>
              {t("web.workshop.move_down")}
            </button>
            <button type="button" className="link" onClick={() => onChange(value.filter((_, j) => j !== i))}>
              {t("web.workshop.remove")}
            </button>
          </div>
          <div className="form-grid">
            {SHOT_PICKS.map((p) => (
              <div key={p.name} className="field wide">
                <span>{`${t(`web.workshop.shot.${p.name}`)} (${t(`workshop.media.${p.media}`)})`}</span>
                <MediaInput
                  field={{ name: p.name, target: p.name, type: "media", media: p.media, required: false }}
                  value={row[p.name] ?? ""}
                  onChange={(v) => set(i, { [p.name]: v })}
                  recents={recents}
                />
              </div>
            ))}
            <label className="field wide">
              <span>{t("web.workshop.shot.text")}</span>
              <textarea rows={2} value={row["text"] ?? ""} onChange={(e) => set(i, { text: e.target.value })} />
            </label>
            <label className="field wide">
              <span>{t("web.workshop.shot.narration")}</span>
              <input value={row["narration"] ?? ""} onChange={(e) => set(i, { narration: e.target.value })} />
            </label>
            <label className="field">
              <span>{t("web.workshop.shot.seconds")}</span>
              <input type="number" min={1} step={0.5} value={row["seconds"] ?? "3"} onChange={(e) => set(i, { seconds: e.target.value })} />
            </label>
          </div>
        </div>
      ))}
      <div className="row">
        <button type="button" onClick={() => onChange([...value, {}])}>
          {t("web.workshop.add_shot")}
        </button>
      </div>
    </div>
  );
}

function MediaInput({
  field, value, onChange, recents, prevOutput,
}: {
  field: WorkshopField;
  value: string;
  onChange: (v: string) => void;
  recents: Asset[];
  prevOutput?: string;
}) {
  const t = useT();
  const { upload } = useWorkshopActions();
  const file = useRef<HTMLInputElement>(null);
  const fromPrev = value === PREV;
  const choices = recents.filter((a) => mediaOfAsset(a) === field.media);
  return (
    <div className="stack">
      <div className="row">
        {fromPrev ? (
          <span className="chip chip-completed">{t("web.workshop.from_prev")}</span>
        ) : (
          <input value={value} placeholder={t("web.workshop.pick_asset")} onChange={(e) => onChange(e.target.value)} />
        )}
        <button type="button" onClick={() => file.current?.click()} disabled={upload.isPending || fromPrev}>
          {t("web.workshop.upload")}
        </button>
        <input
          ref={file}
          type="file"
          hidden
          accept={acceptOf(field.media)}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) upload.mutate(f, { onSuccess: (a) => onChange(`asset:${a.id}`) });
            e.target.value = "";
          }}
        />
        {prevOutput === field.media ? (
          <button type="button" className={fromPrev ? "tab active" : "tab"} onClick={() => onChange(fromPrev ? "" : PREV)}>
            {t("web.workshop.from_prev")}
          </button>
        ) : null}
        {!fromPrev && choices.length > 0 ? (
          <select value="" aria-label={t("web.workshop.history")} onChange={(e) => e.target.value && onChange(`asset:${e.target.value}`)}>
            <option value="">{t("web.workshop.history")}</option>
            {choices.slice(0, 12).map((a) => (
              <option key={a.id} value={a.id}>
                {`${a.created_at.replace("T", " ").slice(5, 16)} ${a.kind}`}
              </option>
            ))}
          </select>
        ) : null}
      </div>
      <ErrorNote error={upload.error} />
    </div>
  );
}

function Inputs({
  kind, values, set, recents, prevOutput,
}: {
  kind: WorkshopKind;
  values: Values;
  set: (name: string, v: unknown) => void;
  recents: Asset[];
  prevOutput?: string;
}) {
  const t = useT();
  const workKinds = useMeta().data?.work_kinds ?? [];
  return (
    <div className="form-grid">
      {kind.inputs.map((f) => {
        const label = `${t(`workshop.field.${f.name}`)}${f.required ? " *" : ""}`;
        const v = values[f.name];
        if (f.type === "workkind")
          return (
            <label key={f.name} className="field">
              <span>{label}</span>
              <select value={String(v ?? "")} onChange={(e) => set(f.name, e.target.value)}>
                <option value="">{t("workshop.kind.none")}</option>
                {workKinds.map((k) => (
                  <option key={k.name} value={k.name}>{k.label}</option>
                ))}
              </select>
            </label>
          );
        if (f.type === "bool")
          return (
            <label key={f.name} className="field check">
              <input type="checkbox" checked={v === true} onChange={(e) => set(f.name, e.target.checked)} />
              <span>{label}</span>
            </label>
          );
        if (f.type === "shots")
          return (
            <div key={f.name} className="field wide">
              <span>{label}</span>
              <ShotList value={Array.isArray(v) ? (v as Shot[]) : []} onChange={(x) => set(f.name, x)} recents={recents} />
            </div>
          );
        if (f.type === "medialist")
          return (
            <div key={f.name} className="field wide">
              <span>{label}</span>
              <MediaList field={f} value={Array.isArray(v) ? (v as string[]) : []} onChange={(x) => set(f.name, x)} recents={recents} />
            </div>
          );
        if (f.type === "media")
          return (
            <div key={f.name} className="field wide">
              <span>{`${label} (${t(`workshop.media.${f.media}`)})`}</span>
              <MediaInput field={f} value={String(v ?? "")} onChange={(x) => set(f.name, x)} recents={recents} prevOutput={prevOutput} />
            </div>
          );
        return (
          <label key={f.name} className={f.type === "longtext" ? "field wide" : "field"}>
            <span>{label}</span>
            {f.type === "longtext" ? (
              <>
                <textarea rows={3} value={String(v ?? "")} onChange={(e) => set(f.name, e.target.value)} />
                <PromptAssist text={String(v ?? "")} onApply={(x) => set(f.name, x)} purpose="text" />
              </>
            ) : (
              <input value={String(v ?? "")} onChange={(e) => set(f.name, e.target.value)} />
            )}
          </label>
        );
      })}
    </div>
  );
}

/** One media kind: its model and parameters (visible, editable, savable) and its inputs. */
function KindPanel({
  module, kind, recents, prefill, onPrefillDone, onRan,
}: {
  module: string;
  kind: WorkshopKind;
  recents: Asset[];
  prefill: { values: Values } | null;
  onPrefillDone: () => void;
  onRan: () => void;
}) {
  const t = useT();
  const act = useWorkshopActions();
  const [model, setModel] = useState(kind.model_ref);
  const [tier, setTier] = useState(kind.tier);
  const [params, setParams] = useState<Values>({});
  const [values, setValues] = useState<Values>({});
  const [count, setCount] = useState(1);
  const [note, setNote] = useState("");
  const q = useWorkshopParams(kind.kind, model, tier);
  useEffect(() => {
    if (q.data) setParams({ ...q.data.effective });
  }, [q.data]);
  useEffect(() => {
    if (prefill) {
      setValues((v) => {
        const next = { ...v };
        for (const [name, val] of Object.entries(prefill.values)) {
          const f = kind.inputs.find((i) => i.name === name);
          next[name] = (f?.type === "medialist" || f?.type === "shots") && Array.isArray(val) ? [...(Array.isArray(v[name]) ? (v[name] as unknown[]) : []), ...val] : val;
        }
        return next;
      });
      onPrefillDone();
    }
  }, [prefill, onPrefillDone, kind.inputs]);
  const chosen = kind.models.find((m) => m.ref === model);
  const effective = q.data?.effective ?? {};
  const changed = Object.fromEntries(Object.entries(params).filter(([k, v]) => !same(v, effective[k])));
  const asDefault = Object.fromEntries(Object.entries(params).filter(([k, v]) => !same(v, q.data?.base?.[k])));
  const empty = (v: unknown) => v === undefined || v === "" || (Array.isArray(v) && v.length === 0);
  const missing = kind.inputs.some((f) => f.required && empty(values[f.name]));
  const run = () => {
    const inputs = Object.fromEntries(Object.entries(values).filter(([, v]) => !empty(v)));
    act.run.mutate({ kind: kind.kind, inputs, model_ref: model, tier, params: changed, count }, { onSuccess: onRan });
  };
  return (
    <div className="stack">
      <section className="card stack">
        <div className="form-grid">
          <label className="field">
            <span>{t("web.workshop.model")}</span>
            <select value={model} onChange={(e) => setModel(e.target.value)}>
              {kind.models.map((m) => (
                <option key={m.ref} value={m.ref}>
                  {`${m.ref} · ${m.provider}${m.available ? "" : ` (${t("health.not_running")})`}${m.in_plan ? "" : ` · ${t("web.workshop.trial")}`}`}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>{t("web.workshop.tier")}</span>
            <select value={tier} onChange={(e) => setTier(e.target.value)}>
              {["draft", "preview", "final"].map((x) => (
                <option key={x} value={x}>
                  {t(`media.tier.${x}`)}
                </option>
              ))}
            </select>
          </label>
        </div>
        {chosen ? (
          <p className="small row">
            <span className="muted">{`${chosen.version} · ${chosen.license_tag}`}</span>
            {chosen.heavy ? <span className="chip">{t("web.workshop.heavy")}</span> : null}
            {!chosen.commercial_ok ? <span className="chip chip-stale">{t("web.workshop.restricted")}</span> : null}
            {!chosen.available ? <span className="chip chip-failed">{t("web.workshop.unavailable", { note: chosen.note })}</span> : null}
          </p>
        ) : null}
        {chosen && !chosen.commercial_ok ? <LicenseAlert model={chosen.ref} licenseTag={chosen.license_tag} /> : null}
        <details open>
          <summary className="strong">{t("web.workshop.params")}</summary>
          <p className="muted small">{t("web.workshop.params_hint")}</p>
          {Object.keys(params).length > 0 ? <ValueEditor value={params} onChange={setParams} prefix="param" /> : null}
          <AddParam
            existing={Object.keys(params)}
            suggestions={Object.keys(q.data?.schema ?? {})}
            onAdd={(k, v) => setParams({ ...params, [k]: v })}
          />
          <div className="row">
            <button
              type="button"
              onClick={() =>
                act.save.mutate({ kind: kind.kind, model_ref: model, tier, params: asDefault }, { onSuccess: () => setNote(t("web.workshop.saved")) })
              }
              disabled={act.save.isPending}
            >
              {t("web.workshop.save_preset")}
            </button>
            <button type="button" onClick={() => act.reset.mutate(kind.kind, { onSuccess: () => setNote(t("web.workshop.reset_done")) })} disabled={act.reset.isPending}>
              {t("web.workshop.reset_preset")}
            </button>
            <span className="muted small">{note}</span>
          </div>
        </details>
      </section>
      <WorkshopAssist
        module={module}
        kind={kind.kind}
        model={model}
        tier={tier}
        values={values}
        params={params}
        onApply={(inputs, next) => {
          setValues({ ...values, ...inputs });
          setParams({ ...params, ...next });
        }}
      />
      <section className="card stack">
        <Inputs kind={kind} values={values} set={(n, v) => setValues({ ...values, [n]: v })} recents={recents} />
        <div className="row">
          <button className="primary" disabled={missing || act.run.isPending} onClick={run}>
            {t("web.workshop.run")}
          </button>
          <label className="row small">
            <span>{t("web.workshop.count")}</span>
            <input type="number" min={1} max={8} value={count} onChange={(e) => setCount(Math.min(Math.max(Number(e.target.value) || 1, 1), 8))} />
          </label>
          <span className="muted small">{t("web.workshop.output", { media: t(`workshop.media.${kind.output}`) })}</span>
        </div>
        <ErrorNote error={act.run.error ?? act.save.error ?? act.reset.error} />
      </section>
    </div>
  );
}

function JobCard({
  item, modules, onUse, onAgain,
}: {
  item: WorkshopHistoryItem;
  modules: WorkshopModule[];
  onUse: (target: { module: string; kind: string; field: string }, assetId: string) => void;
  onAgain: (job: Job) => void;
}) {
  const t = useT();
  const { cancel } = useJobActions();
  const j = item.job;
  const active = j.status === "queued" || j.status === "running";
  const waiting = j.status === "queued" && j.depends_on;
  const [edited, setEdited] = useState<Record<string, Asset>>({});   // a text saved here: this card follows the new version
  const shown = item.assets.map((a) => edited[a.id] ?? a);
  const main = shown.find((a) => a.slot === j.slot) ?? shown[0];
  return (
    <li className="card stack">
      <div className="row">
        <StatusChip status={j.status} group="status" />
        <strong>{t(`media.kind.${j.kind}`)}</strong>
        <span className="muted small">{j.model_ref}</span>
        {j.pipeline_step ? <span className="chip">{t("web.workshop.pipeline_label", { n: j.pipeline_step })}</span> : null}
        <span className="spacer" />
        <span className="muted small">{j.created_at.replace("T", " ").slice(5, 16)}</span>
      </div>
      {active ? (
        <div className="stack">
          <ProgressBar percent={j.status === "running" ? j.progress * 100 : 0} tone={j.status === "queued" ? "queued" : "running"} label={j.progress_msg} />
          <div className="row small muted">
            <span>{waiting ? t("web.creative.waiting") : j.progress_msg ? t(j.progress_msg.startsWith("wait.") ? j.progress_msg : "web.creative.waiting") : ""}</span>
            {j.est_seconds ? <span>{duration(j.est_seconds)}</span> : null}
            <span className="spacer" />
            <button className="link" onClick={() => cancel.mutate(j.id)}>
              {t("web.cancel")}
            </button>
          </div>
        </div>
      ) : null}
      {j.error_code ? (
        <div className="error-box small">
          {jobError(t, j).text}
          <p>{jobError(t, j).remediation}</p>
        </div>
      ) : null}
      {item.assets.map((a) => (
        <AssetPreview key={a.id} asset={a} onSaved={(n) => setEdited({ ...edited, [a.id]: n })} />
      ))}
      {main ? (
        <div className="row small">
          <span className="muted">{t("web.workshop.use_as")}</span>
          {targetsFor(modules, mediaOfAsset(main)).map((tg) => (
            <button key={`${tg.module}${tg.kind}`} className="chip chip-button" onClick={() => onUse(tg, main.id)}>
              {`${t(`workshop.module.${tg.module}`)} · ${t(`media.kind.${tg.kind}`)}`}
            </button>
          ))}
        </div>
      ) : null}
      {main && (main.kind === "text.story" || main.kind === "text.script") ? <MakeProject assetId={main.id} /> : null}
      <details>
        <summary className="small muted">{t("web.workshop.params")}</summary>
        <ValueView value={j.inputs} prefix="input" />
        <ValueView value={j.params} prefix="param" />
      </details>
      <div className="row">
        <button className="link" onClick={() => onAgain(j)}>
          {t("web.workshop.again")}
        </button>
      </div>
    </li>
  );
}

function History({
  module, modules, onUse, onAgain,
}: {
  module: string;
  modules: WorkshopModule[];
  onUse: (target: { module: string; kind: string; field: string }, assetId: string) => void;
  onAgain: (job: Job) => void;
}) {
  const t = useT();
  const hist = useWorkshopHistory(module);
  return (
    <section className="stack">
      <h2>{t("web.workshop.history")}</h2>
      <ErrorNote error={hist.error} />
      {hist.data && hist.data.length === 0 ? <p className="empty">{t("web.workshop.no_history")}</p> : null}
      <ul className="stack plain">
        {hist.data?.map((i) => (
          <JobCard key={i.job.id} item={i} modules={modules} onUse={onUse} onAgain={onAgain} />
        ))}
      </ul>
    </section>
  );
}

interface PStep {
  kind: string;
  values: Values;
}

function PipelineBuilder({ modules, recents }: { modules: WorkshopModule[]; recents: Asset[] }) {
  const t = useT();
  const act = useWorkshopActions();
  const kinds = useMemo(() => modules.flatMap((m) => m.kinds.map((k) => ({ module: m.id, ...k }))), [modules]);
  const [steps, setSteps] = useState<PStep[]>([]);
  const [queued, setQueued] = useState(0);
  const byKind = (k: string) => kinds.find((x) => x.kind === k);
  const example = (ks: string[], first: Values = {}) => setSteps(ks.map((kind, i) => ({ kind, values: i === 0 ? first : {} })));
  const ready = steps.length > 0 && steps.every((s) => {
    const k = byKind(s.kind);
    return k?.inputs.every((f) => !f.required || (s.values[f.name] !== undefined && s.values[f.name] !== ""));
  });
  const submit = () =>
    act.pipeline.mutate(
      steps.map((s): Step => ({ kind: s.kind, inputs: Object.fromEntries(Object.entries(s.values).filter(([, v]) => v !== "" && v !== undefined)) })),
      { onSuccess: (r) => setQueued(r.jobs.length) },
    );
  return (
    <div className="stack">
      <p className="muted">{t("web.workshop.pipeline_hint")}</p>
      <div className="row">
        <button onClick={() => example(["image.generate", "video.lipsync"])}>{t("web.workshop.example_image_video")}</button>
        <button onClick={() => example(["audio.tts", "video.lipsync"])}>{t("web.workshop.example_voice_video")}</button>
        <button onClick={() => example(["audio.music", "audio.convert"])}>{t("web.workshop.example_song_swap")}</button>
      </div>
      {steps.map((s, i) => {
        const k = byKind(s.kind);
        const prev = i > 0 ? byKind(steps[i - 1]?.kind ?? "") : undefined;
        return (
          <section key={i} className="card stack">
            <div className="row">
              <strong>{t("web.workshop.step", { n: i + 1 })}</strong>
              <select value={s.kind} onChange={(e) => setSteps(steps.map((x, j) => (j === i ? { kind: e.target.value, values: {} } : x)))}>
                {kinds.map((x) => (
                  <option key={x.kind} value={x.kind}>
                    {`${t(`workshop.module.${x.module}`)} · ${t(`media.kind.${x.kind}`)}`}
                  </option>
                ))}
              </select>
              <span className="spacer" />
              <button className="link" onClick={() => setSteps(steps.filter((_, j) => j !== i))}>
                {t("web.workshop.remove_step")}
              </button>
            </div>
            {k ? (
              <Inputs
                kind={k}
                values={s.values}
                recents={recents}
                prevOutput={prev?.output}
                set={(n, v) => setSteps(steps.map((x, j) => (j === i ? { ...x, values: { ...x.values, [n]: v } } : x)))}
              />
            ) : null}
          </section>
        );
      })}
      <div className="row">
        <button onClick={() => setSteps([...steps, { kind: kinds[0]?.kind ?? "", values: {} }])}>{t("web.workshop.add_step")}</button>
        <button className="primary" disabled={!ready || act.pipeline.isPending} onClick={submit}>
          {t("web.workshop.submit_pipeline")}
        </button>
        {queued > 0 ? <span className="muted small">{t("web.workshop.queued_n", { n: queued })}</span> : null}
      </div>
      <ErrorNote error={act.pipeline.error} />
    </div>
  );
}

/** Every media module on its own, or chained: pick a model, tune its parameters, run, reuse the result elsewhere. */
export function WorkshopPage() {
  const t = useT();
  const plans = useMeta().data?.pipelines ?? [];
  const [plan, setPlan] = useState("");
  const shop = useWorkshop(plan);
  const all = useWorkshopHistory("");
  const [tab, setTab] = useState("");
  const [kindPick, setKindPick] = useState<Record<string, string>>({});
  const [prefill, setPrefill] = useState<{ kind: string; values: Values } | null>(null);
  const modules = shop.data ?? [];
  const current = tab === PIPELINE ? undefined : (modules.find((m) => m.id === tab) ?? modules[0]);
  const kind = current ? (current.kinds.find((k) => k.kind === kindPick[current.id]) ?? current.kinds[0]) : undefined;
  const recents = useMemo(() => (all.data ?? []).flatMap((i) => i.assets), [all.data]);
  const clear = useMemo(() => () => setPrefill(null), []);
  const goTo = useCallback(
    (target: { module: string; kind: string; field: string }, assetId: string) => {
      const field = modules.find((m) => m.id === target.module)?.kinds.find((k) => k.kind === target.kind)?.inputs.find((i) => i.name === target.field);
      setTab(target.module);
      setKindPick((p) => ({ ...p, [target.module]: target.kind }));
      const ref = `asset:${assetId}`;
      const value = field?.type === "medialist" ? [ref] : field?.type === "shots" ? [{ [field.media]: ref }] : ref;
      setPrefill({ kind: target.kind, values: { [target.field]: value } });
    },
    [modules],
  );
  // "Use as..." from the asset library: /workshop?use=<module>:<kind>:<field>:<asset id>
  const [search, setSearch] = useSearchParams();
  useEffect(() => {
    const use = search.get("use");
    if (!use || modules.length === 0) return;
    const [module, kindId, field, assetId] = use.split(":");
    if (module && kindId && field && assetId) goTo({ module, kind: kindId, field }, assetId);
    setSearch({}, { replace: true });
  }, [search, setSearch, modules, goTo]);
  const again = (job: Job) => {
    const m = modules.find((x) => x.kinds.some((k) => k.kind === job.kind));
    const k = m?.kinds.find((x) => x.kind === job.kind);
    if (!m || !k) return;
    setTab(m.id);
    setKindPick((p) => ({ ...p, [m.id]: k.kind }));
    setPrefill({ kind: k.kind, values: valuesFromInputs(k, job.inputs) });
  };
  return (
    <div className="stack">
      <h1>{t("web.workshop.title")}</h1>
      <Link to="/control">{t("web.control.title")}</Link>
      <p className="muted">{t("web.workshop.intro")}</p>
      <label className="field">
        <span>{t("web.plan.pipeline")}</span>
        <select value={plan} onChange={(e) => setPlan(e.target.value)}>
          <option value="">{t("web.workshop.plan_any")}</option>
          {plans.map((p) => (
            <option key={p.name} value={p.name}>
              {p.label}
            </option>
          ))}
        </select>
        <span className="muted small">{t("web.workshop.plan_hint")}</span>
      </label>
      <ErrorNote error={shop.error} />
      <nav className="tabs">
        {modules.map((m) => (
          <button key={m.id} className={current?.id === m.id ? "tab active" : "tab"} onClick={() => setTab(m.id)}>
            {t(`workshop.module.${m.id}`)}
          </button>
        ))}
        <button className={tab === PIPELINE ? "tab active" : "tab"} onClick={() => setTab(PIPELINE)}>
          {t("web.workshop.tab.pipeline")}
        </button>
      </nav>
      {tab === PIPELINE ? <PipelineBuilder modules={modules} recents={recents} /> : null}
      {current && kind ? (
        <>
          {current.kinds.length > 1 ? (
            <nav className="tabs">
              {current.kinds.map((k) => (
                <button key={k.kind} className={k.kind === kind.kind ? "tab active" : "tab"} onClick={() => setKindPick({ ...kindPick, [current.id]: k.kind })}>
                  {t(`media.kind.${k.kind}`)}
                </button>
              ))}
            </nav>
          ) : null}
          <KindPanel
            key={kind.kind}
            module={current.id}
            kind={kind}
            recents={recents}
            prefill={prefill && prefill.kind === kind.kind ? prefill : null}
            onPrefillDone={clear}
            onRan={() => undefined}
          />
          <History module={current.id} modules={modules} onUse={goTo} onAgain={again} />
        </>
      ) : null}
      {tab === PIPELINE ? <History module="" modules={modules} onUse={goTo} onAgain={again} /> : null}
    </div>
  );
}
