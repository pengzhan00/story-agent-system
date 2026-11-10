import { useLayoutEffect, useRef, useState } from "react";
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
  const pop = useRef<HTMLDivElement>(null);
  // the panel hangs from the button's right edge; when the top bar wrapped the button to the left of the page that
  // would push it off the screen, so it then hangs from the left edge instead
  useLayoutEffect(() => {
    const el = pop.current;
    if (!open || !el) return;
    el.style.left = "";
    el.style.right = "";
    if (el.getBoundingClientRect().left < 8) {
      el.style.left = "0";
      el.style.right = "auto";
    }
  }, [open, status?.unlocked]);
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
        {`${t("web.content.title")} · ${status.unlocked ? (status.expires_at ? t("web.content.unlocked", { time: until }) : t("web.content.unlocked_open")) : t("web.content.locked")}`}
      </button>
      {open ? (
        <div className="content-pop" ref={pop}>
          <ContentBar />
        </div>
      ) : null}
    </div>
  );
}
