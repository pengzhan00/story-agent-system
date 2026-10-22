import { useState } from "react";
import { useContentStatus } from "../api/content";
import { useT } from "../i18n";
import { ContentBar } from "./ContentBar";

/** The lock of restricted content, in the top bar of every page: the lock state at a glance and, on click, the same
 * unlock panel the restricted projects show. Pictures made outside a project (workshop, library, queue) are unlocked
 * from here too. */
export function ContentToggle() {
  const t = useT();
  const status = useContentStatus().data;
  const [open, setOpen] = useState(false);
  if (!status) return null;
  const until = status.expires_at ? new Date(status.expires_at).toLocaleTimeString() : "";
  return (
    <div className="content-toggle">
      <button
        type="button"
        className={status.unlocked ? "feed feed-live" : "feed"}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {`${t("web.content.title")} · ${status.unlocked ? t("web.content.unlocked", { time: until }) : t("web.content.locked")}`}
      </button>
      {open ? (
        <div className="content-pop">
          <ContentBar />
        </div>
      ) : null}
    </div>
  );
}
