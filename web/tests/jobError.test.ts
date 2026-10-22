import { describe, expect, it } from "vitest";
import { makeT } from "../src/i18n";
import { jobError } from "../src/lib/jobError";

const t = makeT({ "error.license_ack_required": "model {model} is restricted", "error.license_ack_required.remediation": "allow {model}" });

describe("jobError", () => {
  it("fills the placeholders from the stored values, or from the job's model for older failures", () => {
    const base = { error_code: "license_ack_required", model_ref: "m1" };
    expect(jobError(t, { ...base, error_params: { model: "m2" } }).text).toBe("model m2 is restricted");
    expect(jobError(t, { ...base, error_params: {} }).remediation).toBe("allow m1");
  });
});
