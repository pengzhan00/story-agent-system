import { useState } from "react";
import { useT } from "../i18n";

const KEY = "license-dismissed:";

function read(ref: string): boolean {
  try {
    return window.localStorage.getItem(KEY + ref) === "1";
  } catch {
    return false;
  }
}

/** A model whose licence limits commercial use: a notice that stays until the viewer dismisses it (per model, per
 * browser). It never blocks anything; the ledger records the licence of every asset in any case. */
export function LicenseAlert({ model, licenseTag }: { model: string; licenseTag?: string }) {
  const t = useT();
  const [gone, setGone] = useState(() => read(model));
  if (gone) return null;
  const dismiss = () => {
    try {
      window.localStorage.setItem(KEY + model, "1");
    } catch {
      /* storage may be unavailable: the notice then returns next time */
    }
    setGone(true);
  };
  return (
    <div className="error-box small" role="alert">
      <span>{t("web.license.alert", { model, tag: licenseTag || "-" })}</span>{" "}
      <button className="link" onClick={dismiss}>
        {t("web.license.dismiss")}
      </button>
    </div>
  );
}
