import { describe, expect, it } from "vitest";
import { clampPercent, duration } from "../src/lib/format";
import { interpolate } from "../src/i18n";

describe("format", () => {
  it("formats durations like the CLI", () => {
    expect(duration(null)).toBe("-");
    expect(duration(8)).toBe("8s");
    expect(duration(80)).toBe("1m20s");
    expect(duration(3700)).toBe("1h01m");
  });
  it("clamps percentages", () => {
    expect(clampPercent(150)).toBe(100);
    expect(clampPercent(-3)).toBe(0);
    expect(clampPercent(null)).toBe(0);
    expect(clampPercent(Number.NaN)).toBe(0);
  });
  it("interpolates placeholders and leaves unknown ones alone", () => {
    expect(interpolate("{done} of {total}", { done: 2, total: 4 })).toBe("2 of 4");
    expect(interpolate("a {x}", {})).toBe("a {x}");
  });
});
