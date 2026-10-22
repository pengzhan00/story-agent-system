import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { Asset, Job, Spec } from "../api/types";
import { duration } from "../lib/format";
import { useT } from "../i18n";
import { ProgressBar } from "./ProgressBar";
import { StatusChip } from "./StatusChip";

/** A written text (story, script, transcript): shown in full, with a link to the file. */
function TextPreview({ asset, src }: { asset: Asset; src: string }) {
  const t = useT();
  const body = useQuery({
    queryKey: ["asset-text", asset.id],
    queryFn: async () => {
      const res = await fetch(src);
      if (!res.ok) throw new Error(String(res.status));
      return res.text();
    },
    staleTime: Infinity,
  });
  return (
    <div className="stack">
      <pre className="transcript">{body.data ?? (body.isError ? t("web.error.generic") : "")}</pre>
      <a className="small" href={src} download>
        {t("web.workshop.download")}
      </a>
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

export function AssetPreview({ asset }: { asset: Asset }) {
  const src = `/api/assets/${asset.id}/file`;
  if (asset.locked) return <MaskedPreview asset={asset} />;
  if (asset.kind.startsWith("image")) return <img className="preview" src={src} alt={asset.slot} loading="lazy" />;
  if (asset.kind.startsWith("video")) return <video className="preview" src={src} controls preload="metadata" />;
  if (asset.kind.startsWith("audio") || asset.kind.startsWith("upload.audio")) return <audio className="audio" src={src} controls preload="none" />;
  if (asset.kind.startsWith("text") || asset.kind === "upload.text") return <TextPreview asset={asset} src={src} />;
  return <a href={src}>{asset.path}</a>;
}

interface Props {
  spec: Spec;
  assets: Asset[];
  jobs: Job[];
  onAdopt: (assetId: string) => void;
  adopting: boolean;
}

/** Every take of one spec side by side: the one in use, the other candidates, and the ones still being made. */
export function Candidates({ spec, assets, jobs, onAdopt, adopting }: Props) {
  const t = useT();
  const running = jobs.filter((j) => j.status === "queued" || j.status === "running");
  const failed = jobs.filter((j) => j.status !== "queued" && j.status !== "running" && j.status !== "completed").slice(0, 3);
  return (
    <div className="stack">
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
          {j.error_code ? t(`error.${j.error_code}`) : t("web.error.generic")}
          {j.error_code ? <p>{t(`error.${j.error_code}.remediation`)}</p> : null}
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
                  <button className="link" disabled={adopting} onClick={() => onAdopt(a.id)}>
                    {t("web.creative.use_this")}
                  </button>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
