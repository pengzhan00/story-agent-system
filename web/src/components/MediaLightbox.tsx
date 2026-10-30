import { useEffect, useRef } from "react";
import { useT } from "../i18n";

/** A picture or a video shown large over the page. Closes with the button, Escape or a click outside; the video stops. */
export function MediaLightbox(props: { src: string; kind: "image" | "video"; title: string; onClose: () => void }) {
  const t = useT();
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
    return () => d?.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className="lightbox"
      aria-label={props.title}
      onClose={(e) => {
        // StrictMode can close then reopen this node before its queued close event is dispatched.
        if (!e.currentTarget.open) props.onClose();
      }}
      onClick={(e) => {
        if (e.target === ref.current) props.onClose();
      }}
    >
      <div className="stack">
        <div className="row">
          <strong className="grow">{props.title}</strong>
          <a href={props.src} download>
            {t("web.workshop.download")}
          </a>
          <button onClick={props.onClose}>{t("web.close")}</button>
        </div>
        {props.kind === "image" ? <img src={props.src} alt={props.title} /> : <video src={props.src} controls autoPlay playsInline />}
      </div>
    </dialog>
  );
}
