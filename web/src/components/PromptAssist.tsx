import { useState } from "react";
import { usePromptAssist } from "../api/llm";
import { useT } from "../i18n";
import { ErrorNote } from "./ErrorNote";

const TARGETS = ["en", "zh-CN", "ja", "ko", "fr", "de", "es"];

/** The two AI buttons beside a prompt: a suggestion (optionally steered by a sentence) and a translation. The answer is
 * shown first; the author takes it (replacing the text) or drops it. Nothing is saved here. */
export function PromptAssist({
  text,
  onApply,
  purpose,
  projectId,
}: {
  text: string;
  onApply: (next: string) => void;
  purpose: string;
  projectId?: number | null;
}) {
  const t = useT();
  const assist = usePromptAssist();
  const [mode, setMode] = useState<"suggest" | "translate" | null>(null);
  const [instruction, setInstruction] = useState("");
  const [target, setTarget] = useState("en");
  const disabled = !text.trim() || assist.isPending;

  const run = () => {
    if (!mode) return;
    assist.mutate({ action: mode, text, purpose, instruction, target, project_id: projectId ?? null });
  };
  const close = () => {
    setMode(null);
    assist.reset();
  };
  const result = assist.data;
  return (
    <div className="stack small">
      <div className="row">
        <button type="button" className="link" disabled={!text.trim()} onClick={() => { assist.reset(); setMode("suggest"); }}>
          {t("web.ai.suggest")}
        </button>
        <button type="button" className="link" disabled={!text.trim()} onClick={() => { assist.reset(); setMode("translate"); }}>
          {t("web.ai.translate")}
        </button>
      </div>
      {mode ? (
        <div className="notice stack">
          <div className="row">
            {mode === "suggest" ? (
              <input
                className="grow"
                value={instruction}
                placeholder={t("web.ai.instruction")}
                onChange={(e) => setInstruction(e.target.value)}
              />
            ) : (
              <label className="row">
                <span>{t("web.ai.target")}</span>
                <select value={target} onChange={(e) => setTarget(e.target.value)}>
                  {TARGETS.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <button type="button" className="primary" disabled={disabled} onClick={run}>
              {assist.isPending ? t("web.ai.working") : t("web.ai.go")}
            </button>
            <button type="button" className="link" onClick={close}>
              {t("web.ai.discard")}
            </button>
          </div>
          <ErrorNote error={assist.error} />
          {result ? (
            <div className="stack">
              <span className="muted">{`${t("web.ai.result")} · ${t("web.ai.model", { model: result.model })}`}</span>
              <pre className="transcript">{result.text}</pre>
              <div className="row">
                <button type="button" className="primary" onClick={() => { onApply(result.text); close(); }}>
                  {t("web.ai.apply")}
                </button>
                <button type="button" className="link" onClick={close}>
                  {t("web.ai.discard")}
                </button>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
