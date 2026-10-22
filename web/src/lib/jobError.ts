import type { T } from "../i18n";
import type { Job } from "../api/types";

/** The localized text of a failed job's error, with its placeholders ({model}, {tool} ...) filled in. Jobs that failed
 * before the values were kept fall back to the job's own model. */
export function jobError(t: T, job: Pick<Job, "error_code" | "error_params" | "model_ref">) {
  const code = job.error_code ?? "";
  const params = { model: job.model_ref, ...job.error_params };
  return { text: t(`error.${code}`, params), remediation: t(`error.${code}.remediation`, params) };
}
