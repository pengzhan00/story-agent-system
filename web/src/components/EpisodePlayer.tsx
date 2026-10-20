import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Asset, Row } from "../api/types";
import { duration } from "../lib/format";
import { useT } from "../i18n";

interface QcCheck {
  code: string;
  ok: boolean | null;
  value: string | number | boolean | null;
  limit: string | number | boolean | null;
}

interface Warning {
  code: string;
  shot_id?: number;
  line?: number;
}

/** The finished episode: player, download, subtitles and the list of things the assembler had to improvise. */
export function EpisodePlayer({ projectId, summary }: { projectId: number; summary: Row }) {
  const t = useT();
  const assetId = typeof summary["asset_id"] === "string" ? summary["asset_id"] : null;
  const srt = useQuery({
    queryKey: ["episode-srt", projectId, assetId],
    enabled: !!assetId,
    queryFn: async () => {
      const assets = await api<Asset[]>(`/api/assets?project_id=${projectId}&kind=compose.assemble&slot=srt&limit=5`);
      return assets[0] ?? null;
    },
  });
  if (!assetId) return null;
  const warnings = (Array.isArray(summary["warnings"]) ? summary["warnings"] : []) as Warning[];
  const qc = (summary["qc"] ?? null) as { passed: boolean; checks: QcCheck[] } | null;
  const file = `/api/assets/${assetId}/file`;
  return (
    <section className="card stack">
      <h2>{t("web.episode.title")}</h2>
      <video className="episode" src={file} controls preload="metadata" />
      <div className="row small">
        <span className="muted">{t("web.episode.length", { time: duration(Number(summary["duration_sec"] ?? 0)) })}</span>
        <span className="spacer" />
        <a href={file} download>
          {t("web.episode.download")}
        </a>
        <a href={`/api/projects/${projectId}/episodes/${Number(summary["episode"] ?? 1)}/package`} download>
          {t("web.episode.package")}
        </a>
        {srt.data ? (
          <a href={`/api/assets/${srt.data.id}/file`} download>
            {t("web.episode.subtitles")}
          </a>
        ) : null}
      </div>
      {warnings.length > 0 ? (
        <div className="notice stack">
          <strong>{t("web.episode.warnings")}</strong>
          <ul className="plain">
            {warnings.map((w, i) => (
              <li key={`${w.code}-${w.shot_id ?? ""}-${w.line ?? ""}-${i}`}>
                {t(`web.compose.warning.${w.code}`, { shot: w.shot_id ?? "", line: (w.line ?? 0) + 1 })}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {qc ? (
        <div className={qc.passed ? "notice stack notice-ok" : "notice stack"}>
          <strong>{t("web.episode.qc")}: {t(qc.passed ? "web.episode.qc_passed" : "web.episode.qc_failed")}</strong>
          <ul className="plain">
            {qc.checks.map((c) => (
              <li key={c.code}>
                <span aria-hidden="true">{c.ok === null ? "–" : c.ok ? "✓" : "✗"}</span>{" "}
                {t(`web.compose.qc.${c.code}`, { value: String(c.value ?? ""), limit: String(c.limit ?? "") })}
                {c.ok === null ? <span className="muted"> ({t("web.episode.qc_skipped")})</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
