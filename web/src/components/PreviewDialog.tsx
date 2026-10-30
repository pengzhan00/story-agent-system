import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { useT } from "../i18n";
import { duration } from "../lib/format";

/** A modal player for a finished episode. Closing it (button, Escape, click outside) stops the video. */
export function PreviewDialog(props: { assetId: string; title: string; seconds: number | null; projectId: number; onClose: () => void }) {
  const t = useT();
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
    return () => d?.close();
  }, []);
  const file = `/api/assets/${props.assetId}/file`;
  return (
    <dialog
      ref={ref}
      className="preview"
      aria-label={props.title}
      onClose={(e) => {
        if (!e.currentTarget.open) props.onClose();
      }}
      onClick={(e) => {
        if (e.target === ref.current) props.onClose();
      }}
    >
      <div className="stack">
        <div className="row">
          <h2 className="grow">{props.title}</h2>
          <button onClick={props.onClose}>{t("web.close")}</button>
        </div>
        <video className="episode" src={file} controls autoPlay playsInline />
        <div className="row small">
          {props.seconds ? <span className="muted">{t("web.episode.length", { time: duration(props.seconds) })}</span> : null}
          <span className="spacer" />
          <a href={file} download>
            {t("web.episode.download")}
          </a>
          <Link to={`/projects/${props.projectId}/workflow`}>{t("web.projects.open")}</Link>
        </div>
      </div>
    </dialog>
  );
}
