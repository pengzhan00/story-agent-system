import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const root = join(__dirname, "..");
const catalogs = ["en", "zh-CN"].map((l) => JSON.parse(readFileSync(join(root, "..", "storyagent", "locales", `${l}.json`), "utf-8")) as Record<string, string>);

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    if (statSync(p).isDirectory()) return sources(p);
    return /\.(ts|tsx)$/.test(f) && !f.endsWith(".d.ts") ? [p] : [];
  });
}

const files = sources(join(root, "src"));
const literal = /\bt\(\s*["'`]([a-zA-Z0-9_.]+)["'`]/g;
const template = /\bt\(\s*`([a-zA-Z0-9_.]+)\.\$\{/g;

describe("every key the web client uses exists in both languages", () => {
  const exact = new Set<string>();
  const prefixes = new Set<string>();
  for (const f of files) {
    const text = readFileSync(f, "utf-8");
    for (const m of text.matchAll(literal)) exact.add(m[1]!);
    for (const m of text.matchAll(template)) prefixes.add(m[1]!);
  }
  it("finds keys", () => expect(exact.size).toBeGreaterThan(40));
  for (const [i, cat] of catalogs.entries()) {
    it(`literal keys (${i === 0 ? "en" : "zh-CN"})`, () => {
      expect([...exact].filter((k) => !(k in cat))).toEqual([]);
    });
    it(`dynamic key groups (${i === 0 ? "en" : "zh-CN"})`, () => {
      expect([...prefixes].filter((p) => !Object.keys(cat).some((k) => k.startsWith(`${p}.`)))).toEqual([]);
    });
  }
});
