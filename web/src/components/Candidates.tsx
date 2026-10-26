import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { Asset, Job, Spec } from "../api/types";
import { put } from "../api/client";
import { duration } from "../lib/format";
import { useT } from "../i18n";
import { jobError } from "../lib/jobError";
import { ErrorNote } from "./ErrorNote";
import { JobId } from "./JobId";
import { MediaLightbox } from "./MediaLightbox";
import { ProgressBar } from "./ProgressBar";
import { PromptAssist } from "./PromptAssist";
import { StatusChip } from "./StatusChip";

/** A written text (story, script, transcript): shown in full, editable (saved as a new version of the same slot), with
 * the AI suggestion / translation beside it and a link to the file. */
function TextPreview({ asset, src, onSaved }: { asset: Asset; src: string; onSaved?: (a: Asset) => void }) {
  const t = useT();
  const qc = useQueryClient();
  const [draft, setDraft] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (text: string) => put<Asset>(`/api/assets/${asset.id}/text`, { text }),
    onSuccess: (a) => {
      setDraft(null);
      onSaved?.(a);
      void qc.invalidateQueries();
    },
  });
  const body = useQuery({
    queryKey: ["asset-text", asset.id],
    queryFn: async () => {
      const res = await fetch(src);
      if (!res.ok) throw new Error(String(res.status));
      return res.text();
    },
    staleTime: Infinity,
  });
  const editable = asset.kind.startsWith("text.") || asset.kind === "upload.text";
  return (
    <div className="stack">
      {draft === null ? (
        <pre className="transcript">{body.data ?? (body.isError ? t("web.error.generic") : "")}</pre>
      ) : (
        <>
          <textarea rows={16} value={draft} onChange={(e) => setDraft(e.target.value)} />
          <PromptAssist text={draft} onApply={setDraft} purpose="text" projectId={asset.project_id} />
          <ErrorNote error={save.error} />
          <div className="row">
            <button type="button" className="primary" disabled={save.isPending} onClick={() => save.mutate(draft)}>
              {t("web.text.save")}
            </button>
            <button type="button" className="link" onClick={() => setDraft(null)}>
              {t("web.close")}
            </button>
            <span className="muted small">{t("web.text.save_hint")}</span>
          </div>
        </>
      )}
      <div className="row small">
        {editable && draft === null && body.data !== undefined ? (
          <button type="button" className="link" onClick={() => setDraft(body.data ?? "")}>
            {t("web.edit")}
          </button>
        ) : null}
        <a href={src} download>
          {t("web.workshop.download")}
        </a>
      </div>
    </div>
  );
}

/** A restricted take while locked: the blurred thumbnail the server keeps (or a plain tile when there is none). */
function MaskedPreview({ asset }: { asset: Asset }) {
  const t = useT();
  const [broken, setBroken] = useState(false);
  return (
    <div className="masked" role="img" aria-label={t("web.content.masked")}>
      {broken ? null : <img className="preview" src={`/api/assets/${asset.id}/mask`} alt="" loading="lazy" onError={() => setBroken(true)} />}
      <span className="small muted">{t("web.content.masked")}</span>
    </div>
  );
}

/** A picture or a video with a way to open it large (click the picture, or the enlarge button on the video). */
function Zoomable({ asset, src, kind }: { asset: Asset; src: string; kind: "image" | "video" }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  return (
    <div className="media-wrap">
      {kind === "image" ? (
        <button type="button" className="zoomable" aria-label={t("web.media.enlarge")} title={t("web.media.enlarge")} onClick={() => setOpen(true)}>
          <img className="preview" src={src} alt={asset.slot} loading="lazy" />
        </button>
      ) : (
        <>
          <video className="preview" src={src} controls preload="metadata" />
          <button type="button" className="enlarge" aria-label={t("web.media.enlarge")} title={t("web.media.enlarge")} onClick={() => setOpen(true)}>
            {t("web.media.enlarge")}
          </button>
        </>
      )}
      {open ? <MediaLightbox src={src} kind={kind} title={asset.slot} onClose={() => setOpen(false)} /> : null}
    </div>
  );
}

export function AssetPreview({ asset, onSaved }: { asset: Asset; onSaved?: (a: Asset) => void }) {
  const src = `/api/assets/${asset.id}/file`;
  if (asset.locked) return <MaskedPreview asset={asset} />;
  if (asset.kind.startsWith("image")) return <Zoomable asset={asset} src={src} kind="image" />;
  if (asset.kind.startsWith("video")) return <Zoomable asset={asset} src={src} kind="video" />;
  if (asset.kind.startsWith("audio") || asset.kind.startsWith("upload.audio")) return <audio className="audio" src={src} controls preload="none" />;
  if (asset.kind.startsWith("text") || asset.kind === "upload.text") return <TextPreview asset={asset} src={src} onSaved={onSaved} />;
  return <a href={src}>{asset.path}</a>;
}

interface Props {
  spec: Spec;
  assets: Asset[];
  jobs: Job[];
  onAdopt: (assetId: string) => void;
  adopting: boolean;
  onTrash: (assetId: string) => void;
  onPurge: (assetId: string) => void;
  onPurgeVersion: (assetId: string) => void;
}

/** Every take of one spec side by side: the one in use, the other candidates, and the ones still being made. */
export function Candidates({ spec, assets, jobs, onAdopt, adopting, onTrash, onPurge, onPurgeVersion }: Props) {
  const t = useT();
  const running = jobs.filter((j) => j.status === "queued" || j.status === "running");
  const failed = jobs.filter((j) => j.status !== "queued" && j.status !== "running" && j.status !== "completed").slice(0, 3);
  const current = assets.find((a) => a.is_current);
  return (
    <div className="stack">
      {assets.length > 0 ? (
        <label className="field">
          <span>{t("web.creative.version")}</span>
          <select value={current?.id ?? ""} onChange={(e) => e.target.value && onAdopt(e.target.value)} disabled={adopting}>
            {assets.map((a) => (
              <option key={a.id} value={a.id}>
                {a.is_current
                  ? t("web.creative.current_version", { v: a.spec_version ?? spec.version })
                  : t("web.creative.version_v", { v: a.spec_version ?? spec.version })}
                {a.metadata?.["seed"] ? ` · ${t("web.creative.seed_n", { seed: String(a.metadata["seed"]) })}` : ""}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {running.map((j) => (
        <div key={j.id} className="job-live">
          <div className="row">
            <StatusChip status={j.status} group="status" />
            <span className="small muted">{j.progress_msg || t("web.creative.waiting")}</span>
            <span className="spacer" />
            <span className="small">{j.status === "running" ? `${Math.round(j.progress * 100)}%` : "0%"}</span>
          </div>
          <ProgressBar percent={j.status === "running" ? j.progress * 100 : 0} tone={j.status === "queued" ? "queued" : "running"} label={j.progress_msg} />
        </div>
      ))}
      {failed.map((j) => (
        <div key={j.id} className="error-box small">
          <JobId id={j.id} /> {j.error_code ? jobError(t, j).text : t("web.error.generic")}
          {j.error_code ? <p>{jobError(t, j).remediation}</p> : null}
        </div>
      ))}
      {assets.length === 0 && running.length === 0 ? <p className="empty">{t("web.creative.no_assets")}</p> : null}
      <ul className="cands">
        {assets.map((a) => {
          const stale = (a.spec_version ?? spec.version) < spec.version;
          return (
            <li key={a.id} className={a.is_current ? "cand current" : "cand"}>
              <AssetPreview asset={a} />
              <div className="row small">
                {a.is_current ? <span className="chip chip-completed">{t("web.creative.in_use")}</span> : null}
                {stale ? <span className="chip chip-stale" title={t("web.creative.stale_hint")}>{t("web.creative.stale", { v: a.spec_version ?? 0 })}</span> : null}
                <span className="muted">{t("web.creative.seed_n", { seed: String(a.metadata?.["seed"] ?? "-") })}</span>
                {a.duration_sec ? <span className="muted">{duration(a.duration_sec)}</span> : null}
                <span className="spacer" />
                {!a.is_current ? (
                  <>
                    <button className="link" disabled={adopting} onClick={() => onAdopt(a.id)}>
                      {t("web.creative.use_this")}
                    </button>
                    <button className="link" onClick={() => onTrash(a.id)}>
                      {t("web.creative.trash")}
                    </button>
                    <button className="link" onClick={() => window.confirm(t("web.creative.purge_confirm")) && onPurge(a.id)}>
                      {t("web.creative.purge")}
                    </button>
                    {stale ? (
                      <button className="link" onClick={() => window.confirm(t("web.creative.purge_version_confirm")) && onPurgeVersion(a.id)}>
                        {t("web.creative.purge_version")}
                      </button>
                    ) : null}
                  </>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
