import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, patch } from "../api/client";
import type { Row } from "../api/types";
import { useT } from "../i18n";
import { ErrorNote } from "./ErrorNote";

/**
 * Give a character a voice: pick a short recording (3-20 s), say what it says, confirm the speaker agreed, upload.
 * The clip goes to the shared library as ``voice/<character>`` and the character points at it, so later dialogue
 * for this character is cloned from it.
 */
export function VoiceUpload({ pid, row }: { pid: number; row: Row }) {
  const t = useT();
  const qc = useQueryClient();
  const file = useRef<HTMLInputElement>(null);
  const name = String(row["name"] ?? "");
  const current = String(row["voice_ref_audio"] ?? "");
  const [text, setText] = useState(String(row["voice_ref_text"] ?? ""));
  const [consent, setConsent] = useState(Boolean(row["voice_ref_consent"]));
  const [picked, setPicked] = useState("");

  const upload = useMutation({
    mutationFn: async (f: File) => {
      const q = new URLSearchParams({ filename: f.name, role: "voice", name, text, consent: String(consent), replace: "true" });
      await api(`/api/library/upload?${q.toString()}`, { method: "POST", body: f, headers: { "Content-Type": "application/octet-stream" } });
      return patch<Row>(`/api/projects/${pid}/characters/${String(row["id"])}`, {
        voice_ref_audio: `library:voice/${name}`,
        voice_ref_text: text,
        voice_ref_consent: consent,
      });
    },
    onSuccess: () => {
      setPicked("");
      if (file.current) file.current.value = "";
      return qc.invalidateQueries({ queryKey: ["story", pid, "characters"] });
    },
  });

  const f = file.current?.files?.[0];
  return (
    <fieldset className="stack">
      <strong className="small">{t("web.voice.title")}</strong>
      <p className="muted small">{current ? t("web.voice.current", { ref: current }) : t("web.voice.none")}</p>
      <div className="row">
        <input ref={file} type="file" accept="audio/*" aria-label={t("web.voice.file")} onChange={(e) => setPicked(e.target.files?.[0]?.name ?? "")} />
      </div>
      <label className="field">
        <span>{t("web.field.voice_ref_text")}</span>
        <input value={text} onChange={(e) => setText(e.target.value)} />
      </label>
      <label className="check">
        <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
        <span>{t("web.voice.consent")}</span>
      </label>
      <div className="row">
        <button disabled={!picked || !f || !text.trim() || !consent || !name || upload.isPending} onClick={() => f && upload.mutate(f)}>
          {upload.isPending ? t("web.voice.uploading") : t("web.voice.upload")}
        </button>
        <span className="muted small">{t("web.voice.hint")}</span>
      </div>
      <ErrorNote error={upload.error} />
    </fieldset>
  );
}
