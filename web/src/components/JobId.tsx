import { useT } from "../i18n";

/** The short number of a job (first 8 characters); a click copies the whole id. */
export function JobId({ id }: { id: string }) {
  const t = useT();
  const copy = () => {
    try {
      void navigator.clipboard?.writeText(id);
    } catch {
      /* copying is a convenience */
    }
  };
  return (
    <button type="button" className="link mono job-id" title={`${id} · ${t("web.jobs.copy_id")}`} onClick={copy}>
      {`#${id.slice(0, 8)}`}
    </button>
  );
}
