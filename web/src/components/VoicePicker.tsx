import { useEffect, useState } from "react";
import { useLibraryItems } from "../api/library";
import { useT } from "../i18n";

/**
 * Choose a library voice by ear: the select only picks what to listen to; the voice is applied with its own button, so
 * nothing changes while a person is auditioning.
 */
export function VoicePicker({ value, disabled, busy, onApply }: { value: string; disabled?: boolean; busy?: boolean; onApply: (ref: string) => void }) {
  const t = useT();
  const voices = (useLibraryItems().data ?? []).filter((i) => i.role === "voice");
  const [choice, setChoice] = useState(value);
  useEffect(() => setChoice(value), [value]);
  const heard = voices.find((v) => v.ref === choice);
  return (
    <div className="stack">
      <label className="field">
        <span>{t("web.plan.voice")}</span>
        <select value={choice} disabled={disabled} onChange={(e) => setChoice(e.target.value)}>
          <option value="">{t("web.plan.voice_none")}</option>
          {voices.map((v) => (
            <option key={v.id} value={v.ref}>
              {v.name}
            </option>
          ))}
        </select>
      </label>
      {heard ? (
        <div className="row">
          <audio className="audio" src={`/api/assets/${heard.asset.id}/file`} controls preload="none" aria-label={t("web.plan.voice_listen")} />
          <span className="muted small">{heard.description}</span>
        </div>
      ) : null}
      <div className="row">
        <button type="button" disabled={disabled || busy || choice === "" || choice === value} onClick={() => onApply(choice)}>
          {choice === value && value !== "" ? t("web.plan.voice_in_use") : t("web.plan.voice_use")}
        </button>
      </div>
    </div>
  );
}
