import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMeta, useProjectFromText } from "../api/hooks";
import { useT } from "../i18n";
import { ErrorNote } from "./ErrorNote";

/** A written story or script becomes a project: its story stages run, then the media are confirmed in the plan. */
export function MakeProject({ assetId }: { assetId: string }) {
  const t = useT();
  const nav = useNavigate();
  const make = useProjectFromText();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const profiles = useMeta().data?.delivery_profiles ?? [];
  const [frame, setFrame] = useState("");
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
        <select value={frame} aria-label={t("web.projects.frame")} onChange={(e) => setFrame(e.target.value)}>
          <option value="">{t("web.projects.frame_default")}</option>
          {profiles.map((p) => (
            <option key={p.name} value={p.name}>
              {`${p.label} · ${p.width}×${p.height}`}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="primary"
          disabled={!name.trim() || make.isPending}
          onClick={() => make.mutate({ name: name.trim(), asset_id: assetId, delivery_profile: frame }, { onSuccess: (r) => nav(`/projects/${r.project.id}`) })}
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
