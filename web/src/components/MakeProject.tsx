import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useProjectFromText } from "../api/hooks";
import { useT } from "../i18n";
import { ErrorNote } from "./ErrorNote";

/** A written story or script becomes a project: its story stages run, then the media are confirmed in the plan. */
export function MakeProject({ assetId }: { assetId: string }) {
  const t = useT();
  const nav = useNavigate();
  const make = useProjectFromText();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  if (!open)
    return (
      <button type="button" className="chip chip-button" onClick={() => setOpen(true)}>
        {t("web.project.from_text")}
      </button>
    );
  return (
    <div className="stack">
      <div className="row">
        <input value={name} placeholder={t("web.project.from_text_name")} aria-label={t("web.project.from_text_name")} onChange={(e) => setName(e.target.value)} />
        <button
          type="button"
          className="primary"
          disabled={!name.trim() || make.isPending}
          onClick={() => make.mutate({ name: name.trim(), asset_id: assetId }, { onSuccess: (r) => nav(`/projects/${r.project.id}`) })}
        >
          {t("web.project.from_text_go")}
        </button>
        <button type="button" className="link" onClick={() => setOpen(false)}>
          {t("web.close")}
        </button>
      </div>
      <p className="muted small">{t("web.project.from_text_hint")}</p>
      <ErrorNote error={make.error} />
    </div>
  );
}
