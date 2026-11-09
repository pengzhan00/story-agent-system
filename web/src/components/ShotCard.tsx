import type { Row } from "../api/types";
import { useT } from "../i18n";

/** The fields of a shot's detail card, in reading order; nested groups are flattened into `group.key` lines. */
const FLAT = ["summary", "position_name", "contact", "setting", "light", "sound", "start_frame", "end_frame", "video_motion", "continuity_out", "negative"] as const;
const GROUPS: Record<string, string[]> = {
  motion: ["main", "rhythm", "secondary", "still"],
  camera: ["size", "angle", "height", "lens_mm", "movement", "crop", "focus"],
};
const PERSON = ["clothing", "body_in_view", "pose", "limbs", "face", "skin"] as const;

function text(v: unknown): string {
  if (v === null || v === undefined) return "";
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

export function cardOf(row: Row): Row | null {
  const payload = row["render_payload"] as Row | undefined;
  const card = payload?.["detail"] as Row | undefined;
  return card && Object.keys(card).length > 0 ? card : null;
}

/** A shot's detail card as it was written for the image and video models: read-only, nothing is hidden. */
export function ShotCard({ row }: { row: Row }) {
  const t = useT();
  const card = cardOf(row);
  if (!card) return null;
  const people = Array.isArray(card["people"]) ? (card["people"] as Row[]) : [];
  const problems = Array.isArray(card["problems"]) ? (card["problems"] as unknown[]) : [];
  const lines: Array<[string, string]> = [];
  for (const k of FLAT) if (text(card[k])) lines.push([t(`web.card.${k}`), text(card[k])]);
  for (const [group, keys] of Object.entries(GROUPS)) {
    const inner = (card[group] ?? {}) as Row;
    for (const k of keys) if (text(inner[k])) lines.push([`${t(`web.card.${group}`)} · ${t(`web.card.${k}`)}`, text(inner[k])]);
  }
  return (
    <section className="stack shot-card">
      <h4>{t("web.card.title")}</h4>
      <p className="muted small">{t("web.card.hint")}</p>
      {problems.length > 0 ? (
        <ul className="small">
          {problems.map((p, i) => (
            <li key={i} className="bad">
              {text(p)}
            </li>
          ))}
        </ul>
      ) : null}
      {people.map((p, i) => (
        <dl key={i} className="value-row-list">
          <dt className="strong">{text(p["who"])}</dt>
          {PERSON.filter((k) => text(p[k])).map((k) => (
            <div key={k} className="value-row">
              <dt>{t(`web.card.${k}`)}</dt>
              <dd>{text(p[k])}</dd>
            </div>
          ))}
        </dl>
      ))}
      <dl className="value-row-list">
        {lines.map(([label, value]) => (
          <div key={label} className="value-row">
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
